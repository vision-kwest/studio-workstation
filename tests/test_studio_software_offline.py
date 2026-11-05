import json
import subprocess
import unittest
from pathlib import Path
from runtime import render_workstation

ROOT = Path(__file__).resolve().parents[1]
BUCKET = "studio-workstation-releases-401233741942-us-east-1"

class StudioSoftwareContract(unittest.TestCase):
    def test_original_cpu_gpu_rendering_unchanged_without_software_pin(self):
        for profile in ("cpu", "gpu"):
            user_data = render_workstation.render(BUCKET, "releases/v0.1.3/res-2026.06", "workstation", profile)
            self.assertNotIn("studio-software-setup", user_data)
            self.assertIn("STUDIO_STANDALONE=true", user_data)

    def test_opt_in_is_same_for_cpu_and_gpu_and_does_not_block_dcv(self):
        for profile in ("cpu", "gpu"):
            text = render_workstation.render(BUCKET, "releases/v0.1.3/res-2026.06", "workstation", profile,
                                              "Version_Id.1", "a" * 64)
            self.assertIn("systemd-run --unit=studio-software-setup --collect --no-block", text)
            self.assertIn("app_installed.lock", text)
            self.assertIn("linux_vdi_config_host_ready.lock", text)
            self.assertIn("x-systemd.automount,ro,tls,iam", text)
            self.assertIn("software/rez/3.4.0/studio-rez-ubuntu2404.tar.gz", text)
            self.assertLess(text.index("scripts/virtual-desktop-host/linux/install.sh -p false"),
                            text.index("systemd-run --unit=studio-software-setup"))
            self.assertNotIn("mount -a", text)

    def test_release_pin_validation(self):
        for version, digest in [(None, "a"*64), ("bad;id", "a"*64), ("v1", "bad"), ("v1", None)]:
            with self.assertRaises(ValueError):
                render_workstation.render(BUCKET, "releases/v0.1.3/res-2026.06",
                                          "workstation", "cpu", version, digest)

    def test_bash_syntax_and_no_mount_engine(self):
        script = ROOT / "scripts/setup_workstation_software.sh"
        subprocess.run(["bash", "-n", str(script)], check=True)
        text = script.read_text()
        self.assertNotIn("mount -a", text)
        self.assertNotIn("mount -t ", text)
        self.assertIn("systemctl start opt-studio-packages.automount", text)
        self.assertIn("PIP_NO_INDEX=1", text)

    def test_review_only_security_and_exact_s3(self):
        policy = json.loads((ROOT / "infra/efs-publisher-consumer.review-only.json").read_text())
        by_sid = {s["Sid"]: s for s in policy["FileSystemPolicy"]["Statement"]}
        self.assertEqual(by_sid["ConsumerMount"]["Action"], "elasticfilesystem:ClientMount")
        self.assertIn("elasticfilesystem:ClientWrite", by_sid["ConsumerNoWrite"]["Action"])
        self.assertEqual(by_sid["RequireTLS"]["Condition"]["Bool"]["aws:SecureTransport"], "false")
        s3 = json.loads((ROOT / "infra/workstation-release-read-policy.json").read_text())
        exact = [s for s in s3["Statement"] if s["Sid"] == "ReadExactRezBundleVersion"]
        self.assertEqual(len(exact), 1)
        self.assertEqual(exact[0]["Action"], "s3:GetObjectVersion")

if __name__ == "__main__":
    unittest.main()
