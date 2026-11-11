"""Offline checks for official EFS client installation in Studio CLI."""
import subprocess
import unittest
from unittest import mock
from scripts import studio_workstation_cli as cli


class EfsClientInstallTests(unittest.TestCase):
    def test_success(self):
        calls = []
        def manager(region, *args):
            calls.append(args)
            if args[1] == "send-command":
                self.assertIn("AWS-ConfigureAWSPackage", args)
                self.assertIn("AmazonEFSUtils", args)
                return {"Command": {"CommandId": "cmd-1"}}
            return {"Status": "Success"}
        cli.install_efs_client("i-test", "us-east-1", manager=manager, sleep=lambda _: None)
        self.assertEqual(len(calls), 2)

    def test_package_failure_fails_closed(self):
        def manager(region, *args):
            if args[1] == "send-command":
                return {"Command": {"CommandId": "cmd-1"}}
            return {"Status": "Failed"}
        with self.assertRaisesRegex(RuntimeError, "EFS helper installation failed"):
            cli.install_efs_client("i-test", "us-east-1", manager=manager, sleep=lambda _: None)

    def test_iam_denial_not_retried(self):
        manager = mock.Mock(side_effect=subprocess.CalledProcessError(
            254, ["aws"], stderr="AccessDenied"))
        with self.assertRaises(subprocess.CalledProcessError):
            cli.install_efs_client("i-test", "us-east-1", manager=manager, sleep=lambda _: None)
        manager.assert_called_once()


if __name__ == "__main__":
    unittest.main()
