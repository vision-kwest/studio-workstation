"""Prevent stale software-enabled GPU/CPU deployments (Issue #86)."""
import unittest
from scripts import studio_workstation_cli as cli


class SoftwarePreflightTests(unittest.TestCase):
    def test_current_bootstrap_accepted(self):
        cli.verify_software_bootstrap_source(
            "# Official EFS via SSM Distributor\ncommand -v mount.efs >/dev/null")

    def test_obsolete_bundled_deb_refused_before_launch(self):
        with self.assertRaisesRegex(RuntimeError, "Outdated Studio"):
            cli.verify_software_bootstrap_source(
                'dpkg -i "$dir"/amazon-efs-utils_*.deb\ncommand -v mount.efs')

    def test_missing_helper_contract_refused(self):
        with self.assertRaisesRegex(RuntimeError, "Outdated Studio"):
            cli.verify_software_bootstrap_source("echo old-bootstrap")


if __name__ == "__main__":
    unittest.main()
