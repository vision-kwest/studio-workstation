"""Offline GPU profile contracts: reuse original RES NVIDIA path, no AWS writes."""
import json
import tempfile
from unittest import mock
import sys
import unittest
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"runtime"))
from render_workstation import render, main as render_main

class GpuProfileContracts(unittest.TestCase):
    def test_gpu_uses_upstream_nvidia_flag(self):
        cpu=render("studio-release-bucket","releases/v0.1.3/res-2026.06","workstation","cpu")
        gpu=render("studio-release-bucket","releases/v0.1.3/res-2026.06","workstation","gpu")
        self.assertIn("-g NONE -e standalone",cpu)
        self.assertIn("-g NVIDIA -e standalone",gpu)
        self.assertIn("scripts/virtual-desktop-host/linux/install.sh",gpu)
        self.assertNotIn("-g NVIDIA -e standalone",cpu)
    def test_gl_uses_upstream_with_standalone_family_adapter(self):
        gl=(ROOT/"runtime/overrides/ideabootstrap/dcv/linux/gl.py").read_text()
        self.assertIn('os.environ.get("STUDIO_STANDALONE") == "true"',gl)
        self.assertIn('os.environ["STUDIO_GPU_FAMILIES"]',gl)
        self.assertIn("cluster_settings.get_setting(",gl)
    def test_gpu_template_profile_is_g4dn_only(self):
        t=json.loads((ROOT/"runtime/workstation-template.json").read_text())
        self.assertEqual(t["Resources"]["Workstation"]["Properties"]["UserData"],"RENDER_OFFLINE")
        # renderer switches instance type to g4dn.xlarge, never changes shared CPU template
        source=(ROOT/"runtime/render_workstation.py").read_text()
        self.assertIn('["g4dn.xlarge"]',source)
        self.assertIn('a.profile == "gpu"',source)
    def test_persistent_gpu_template_is_stop_start_safe(self):
        with tempfile.TemporaryDirectory() as d:
            out = Path(d) / "gpu.json"
            with mock.patch.object(sys, "argv", ["render_workstation.py", "--profile", "gpu",
                   "--lifecycle", "persistent", "--bucket", "studio-release-bucket",
                   "--prefix", "releases/task7-pr61-postreboot-a8ec326/res-2026.06",
                   "--output", str(out)]):
                render_main()
            t = json.loads(out.read_text())
            p = t["Resources"]["Workstation"]["Properties"]
            self.assertEqual(p["InstanceInitiatedShutdownBehavior"], "stop")
            self.assertTrue(p["BlockDeviceMappings"][0]["Ebs"]["DeleteOnTermination"])
            self.assertTrue(p["BlockDeviceMappings"][0]["Ebs"]["Encrypted"])
            self.assertEqual(t["Parameters"]["InstanceType"]["AllowedValues"], ["g4dn.xlarge", "g4dn.2xlarge"])
            self.assertEqual(t["Parameters"]["InstanceType"]["Default"], "g4dn.xlarge")
            self.assertNotIn("Task7", {tag["Key"] for tag in p["Tags"]})
            self.assertIn("-g NVIDIA", p["UserData"]["Fn::Base64"]["Fn::Sub"])


    def test_native_volume_tag_propagation_and_disposal(self):
        with tempfile.TemporaryDirectory() as d:
            out = Path(d) / "native.json"
            with mock.patch.object(sys, "argv", ["render_workstation.py", "--profile", "gpu",
                   "--lifecycle", "persistent", "--bucket", "studio-release-bucket",
                   "--prefix", "releases/test/res-2026.06", "--output", str(out)]):
                render_main()
            template = json.loads(out.read_text())
            self.assertEqual(set(template["Resources"]), {"Workstation", "WorkstationSG"})
            props = template["Resources"]["Workstation"]["Properties"]
            self.assertTrue(props["PropagateTagsToVolumeOnCreation"])
            self.assertTrue(all(m["Ebs"]["DeleteOnTermination"] for m in props["BlockDeviceMappings"]))
            self.assertIn({"Key": "StudioStackId", "Value": {"Ref": "AWS::StackId"}}, props["Tags"])

    def test_unscoped_gpu_does_not_add_launch_template(self):
        with tempfile.TemporaryDirectory() as d:
            out = Path(d) / "unscoped.json"
            with mock.patch.object(sys, "argv", ["render_workstation.py",
                   "--profile", "gpu", "--lifecycle", "persistent",
                   "--bucket", "studio-release-bucket",
                   "--prefix", "releases/test/res-2026.06",
                   "--output", str(out)]):
                render_main()
            t = json.loads(out.read_text())
            self.assertNotIn("CleanupVolumeTagLaunchTemplate", t["Resources"])
            self.assertNotIn("LaunchTemplate", t["Resources"]["Workstation"]["Properties"])

    def test_default_gpu_renderer_preserves_task7_temporary_mode(self):
        with tempfile.TemporaryDirectory() as d:
            out = Path(d) / "gpu.json"
            with mock.patch.object(sys, "argv", ["render_workstation.py", "--profile", "gpu",
                   "--bucket", "studio-release-bucket", "--prefix", "releases/test/res-2026.06",
                   "--output", str(out)]):
                render_main()
            template = json.loads(out.read_text())
            self.assertEqual(template["Parameters"]["InstanceType"]["AllowedValues"], ["g4dn.xlarge"])
            p = template["Resources"]["Workstation"]["Properties"]
            self.assertEqual(p["InstanceInitiatedShutdownBehavior"], "terminate")
            self.assertTrue(p["BlockDeviceMappings"][0]["Ebs"]["DeleteOnTermination"])
            self.assertIn("Task7", {tag["Key"] for tag in p["Tags"]})

    def test_release_archive_pins_match_validated_sha256_manifest(self):
        from scripts import studio_workstation_cli as cli
        expected = {
            "installation-scripts.tar.gz": (
                "bs3P_1D8yZI33nM5K0u168XG_bXYc914",
                "c780e2c78946363e6b8817dbca5cfbcab714798aee055b8160b21b02023a7816"),
            "virtual-desktop.tar.gz": (
                "nzyYrLhYAQ.OTFNpFE_xSHPaK06T1V53",
                "e7686d6240db9062b5ea62f94859bd657de8c1231e8a3ba097704f9faa45d147"),
        }
        from render_workstation import GPU_ARCHIVES, PINNED_GPU_PREFIX, PINNED_GPU_APP_PREFIX
        manifest = json.loads((ROOT / "runtime/pr65-staged-release-manifest.json").read_text())
        for name, (version, digest) in expected.items():
            self.assertEqual(cli.GPU_VERSIONS[name], version)
            self.assertEqual(GPU_ARCHIVES[name], (version, digest))
            if name == "virtual-desktop.tar.gz":  # PR #65 manifest remains historical
                self.assertEqual(manifest["archives"][name]["s3_version_id"], version)
                self.assertEqual(manifest["archives"][name]["sha256"], digest)
            self.assertEqual(cli.GPU_KEYS[name],
                             ("releases/issue168-nvidia-dkms-r2-20261123/res-2026.06/" + name
                              if name == "installation-scripts.tar.gz"
                              else "releases/task7-pr61-postreboot-a8ec326/res-2026.06/" + name))
            if name == "virtual-desktop.tar.gz":
                self.assertEqual(manifest["archives"][name]["s3_key"], cli.GPU_KEYS[name])
        self.assertEqual(PINNED_GPU_PREFIX, cli.GPU_PREFIX)
        self.assertEqual(PINNED_GPU_APP_PREFIX, cli.GPU_APP_PREFIX)
        self.assertNotEqual(PINNED_GPU_PREFIX, PINNED_GPU_APP_PREFIX)
        policy = json.loads((ROOT / "infra/workstation-release-read-policy.json").read_text())
        driver = next(s for s in policy["Statement"] if s["Sid"] == "ReadNvidiaDkmsInstallerVersion")
        self.assertEqual(driver["Resource"], "arn:aws:s3:::" + cli.DEFAULT_BUCKET + "/" + cli.GPU_KEYS["installation-scripts.tar.gz"])
        self.assertEqual(driver["Condition"], {"StringEquals": {"s3:VersionId": cli.GPU_VERSIONS["installation-scripts.tar.gz"]}})

    def test_pinned_gpu_bootstrap_and_app_archive(self):
        from render_workstation import GPU_ARCHIVES, PINNED_GPU_BUCKET, PINNED_GPU_PREFIX
        data = render(PINNED_GPU_BUCKET, PINNED_GPU_PREFIX, "workstation", "gpu")
        for version, digest in GPU_ARCHIVES.values():
            self.assertIn(version, data)
            self.assertIn(digest, data)
        self.assertIn("s3api get-object", data)
        self.assertIn("sha256sum --check --status", data)
        self.assertIn("STUDIO_APP_VERSION_ID=", data)
        self.assertIn("STUDIO_APP_SHA256=", data)

    def test_scoped_gpu_renders_independently_pinned_split_archive_urls(self):
        from render_workstation import GPU_ARCHIVES, PINNED_GPU_BUCKET, PINNED_GPU_PREFIX, PINNED_GPU_APP_PREFIX
        data = render(PINNED_GPU_BUCKET, PINNED_GPU_PREFIX, "workstation", "gpu")
        boot = "s3://" + PINNED_GPU_BUCKET + "/" + PINNED_GPU_PREFIX + "/installation-scripts.tar.gz"
        app = "s3://" + PINNED_GPU_BUCKET + "/" + PINNED_GPU_APP_PREFIX + "/virtual-desktop.tar.gz"
        self.assertIn(boot, data)
        self.assertIn("STUDIO_APP_PACKAGE_URI=" + app, data)
        self.assertNotIn("STUDIO_APP_PACKAGE_URI=s3://" + PINNED_GPU_BUCKET + "/" +
                         PINNED_GPU_PREFIX + "/virtual-desktop.tar.gz", data)
        self.assertIn(GPU_ARCHIVES["installation-scripts.tar.gz"][0], data)
        self.assertIn(GPU_ARCHIVES["installation-scripts.tar.gz"][1], data)
        self.assertIn("STUDIO_APP_VERSION_ID=" + GPU_ARCHIVES["virtual-desktop.tar.gz"][0], data)
        self.assertIn("STUDIO_APP_SHA256=" + GPU_ARCHIVES["virtual-desktop.tar.gz"][1], data)

    def test_res_app_loader_pins_version_and_hash(self):
        source = (ROOT / "runtime/overrides/scripts/common/linux/install_app.sh").read_text()
        self.assertIn('s3api get-object', source)
        self.assertIn('STUDIO_APP_VERSION_ID', source)
        self.assertIn('STUDIO_APP_SHA256', source)
        self.assertIn('sha256sum --check --status', source)
        self.assertIn('s3 cp', source)  # Original managed RES path remains.


    def test_invalid_profile_fails(self):
        with self.assertRaises(ValueError):
            render("studio-release-bucket","releases/v0.1.3/res-2026.06","workstation","unknown")

if __name__=="__main__":
    unittest.main()
