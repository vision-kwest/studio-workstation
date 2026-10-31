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
            self.assertFalse(p["BlockDeviceMappings"][0]["Ebs"]["DeleteOnTermination"])
            self.assertTrue(p["BlockDeviceMappings"][0]["Ebs"]["Encrypted"])
            self.assertEqual(t["Parameters"]["InstanceType"]["AllowedValues"], ["g4dn.xlarge"])
            self.assertNotIn("Task7", {tag["Key"] for tag in p["Tags"]})
            self.assertIn("-g NVIDIA", p["UserData"]["Fn::Base64"]["Fn::Sub"])

    def test_scoped_gpu_root_volume_is_tagged_at_launch(self):
        with tempfile.TemporaryDirectory() as d:
            out = Path(d) / "scoped.json"
            with mock.patch.object(sys, "argv", ["render_workstation.py",
                   "--profile", "gpu", "--lifecycle", "persistent",
                   "--bucket", "studio-release-bucket",
                   "--prefix", "releases/task7-pr61-postreboot-a8ec326/res-2026.06",
                   "--cleanup-token", "unique-test-token-123",
                   "--test-stack", "studio-gpu-cli-unique-test",
                   "--output", str(out)]):
                render_main()
            t = json.loads(out.read_text())
            resources = t["Resources"]
            lt = resources["CleanupVolumeTagLaunchTemplate"]["Properties"]
            spec = lt["LaunchTemplateData"]["TagSpecifications"]
            self.assertEqual(len(spec), 1)
            self.assertEqual(spec[0]["ResourceType"], "volume")
            expected = {"StudioCleanupToken": "unique-test-token-123",
                        "StudioStack": "studio-gpu-cli-unique-test"}
            self.assertEqual({x["Key"]: x["Value"] for x in spec[0]["Tags"]}, expected)
            self.assertEqual({x["Key"]: x["Value"] for x in
                              resources["WorkstationSG"]["Properties"]["Tags"]}, expected)
            instance = resources["Workstation"]["Properties"]
            self.assertEqual({x["Key"]: x["Value"] for x in instance["Tags"]
                              if x["Key"] in expected}, expected)
            self.assertEqual(instance["LaunchTemplate"]["LaunchTemplateId"],
                             {"Ref": "CleanupVolumeTagLaunchTemplate"})
            self.assertFalse(instance["BlockDeviceMappings"][0]["Ebs"]["DeleteOnTermination"])

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
            p = json.loads(out.read_text())["Resources"]["Workstation"]["Properties"]
            self.assertEqual(p["InstanceInitiatedShutdownBehavior"], "terminate")
            self.assertTrue(p["BlockDeviceMappings"][0]["Ebs"]["DeleteOnTermination"])
            self.assertIn("Task7", {tag["Key"] for tag in p["Tags"]})

    def test_invalid_profile_fails(self):
        with self.assertRaises(ValueError):
            render("studio-release-bucket","releases/v0.1.3/res-2026.06","workstation","unknown")

if __name__=="__main__":
    unittest.main()
