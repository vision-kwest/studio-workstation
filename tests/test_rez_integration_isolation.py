"""Task 6: no accidental EFS/Rez hookup to validated RES, DCV or GPU lifecycle."""
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class StandaloneIntegrationIsolationTests(unittest.TestCase):
    def read(self, path):
        return (ROOT / path).read_text(encoding="utf-8")

    def test_res_desktop_still_skips_full_shared_storage_for_standalone(self):
        bootstrap = self.read("runtime/overrides/ideavirtualdesktop/app/linux/bootstrap.py")
        self.assertIn(
            'if os.environ.get("STUDIO_STANDALONE") != "true":\n'
            '            shared_storage.configure()', bootstrap)
        self.assertIn("dcv.configure()", bootstrap)
        self.assertIn("x_server._x_server_validated()", bootstrap)

    def test_ssh_policy_unchanged_by_rez_and_efs(self):
        bootstrap = self.read("runtime/overrides/ideavirtualdesktop/app/linux/bootstrap.py")
        self.assertIn("restrict_ssh.configure(session_owner=True)", bootstrap)
        self.assertIn("disable_ssh.configure()", bootstrap)
        self.assertNotIn("standalone_efs", bootstrap)
        self.assertNotIn("install_workstation_rez", bootstrap)

    def test_main_lifecycle_does_not_implicitly_mount_or_install(self):
        management = self.read("scripts/studio_workstation_cli.py")
        renderer = self.read("runtime/render_workstation.py")
        for source in (management, renderer):
            for module in ("standalone_efs", "install_workstation_rez",
                           "check_openusd_rez"):
                with self.subTest(module=module):
                    self.assertNotIn(module, source)
        self.assertIn("gpu_restart_preflight", management)
        self.assertIn("GPU_ACCEPTANCE_STACK", management)

    def test_efs_adapter_never_reactivates_res_control_plane(self):
        storage = self.read("runtime/standalone_efs.py")
        for forbidden in ("cluster_settings", "mount -a", "authorized_keys",
                          "boto3", "aws ec2", "s3_mount"):
            with self.subTest(token=forbidden):
                # Full sentences in module documentation describing excluded
                # upstream behavior are allowed; only executable operations
                # below import statements matter here.
                source = storage.split("import fcntl", 1)[-1]
                self.assertNotIn(forbidden, source)
        self.assertIn('PROFILES = ("cpu", "gpu")', storage)
        self.assertIn('MOUNTPOINT = Path("/opt/studio/packages")', storage)

    def test_openusd_smoke_does_not_modify_package_or_call_aws(self):
        source = self.read("scripts/check_openusd_rez.py")
        self.assertIn('PROFILES = ("cpu", "gpu")', source)
        self.assertIn('PACKAGE_REQUEST = "usd-26.05"', source)
        for forbidden in ("aws ", "sudo ", "mount -a", "rez-build --install",
                          "promote_usd", "subprocess.Popen", "shell=True"):
            with self.subTest(token=forbidden):
                code = source.split("import argparse", 1)[-1]
                self.assertNotIn(forbidden, code)


if __name__ == "__main__":
    unittest.main()
