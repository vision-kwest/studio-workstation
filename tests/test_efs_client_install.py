"""Offline checks for official EFS client installation in Studio CLI."""
import json
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
                parameters = json.loads(args[args.index("--parameters") + 1])
                self.assertEqual(parameters["name"], ["AmazonEFSUtils"])
                self.assertEqual(parameters["action"], ["Install"])
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

    def test_restart_installs_only_when_helper_is_explicitly_missing(self):
        for presence in ("present", "missing"):
            with self.subTest(presence=presence):
                manager = mock.Mock(side_effect=[
                    {"Command": {"CommandId": "probe"}}, {},
                    {"Status": "Success", "ResponseCode": 0, "StandardOutputContent": presence + "\n"},
                    {"Command": {"CommandId": "install"}}, {"Status": "Success"}])
                cli.install_efs_client("i-test", "us-east-1", only_missing=True,
                                       manager=manager, sleep=lambda _: None)
                calls = [call.args for call in manager.call_args_list]
                self.assertIn("AWS-RunShellScript", calls[0])
                self.assertEqual(calls[1], ("us-east-1", "ssm", "wait", "command-executed",
                                          "--command-id", "probe", "--instance-id", "i-test"))
                self.assertEqual(len(calls), 3 if presence == "present" else 5)
                if presence == "missing":
                    self.assertIn("AWS-ConfigureAWSPackage", calls[3])
                    self.assertIn("i-test", calls[3])

    def test_uncertain_presence_never_installs(self):
        for result in ({}, {"Status": "Failed", "ResponseCode": 1, "StandardOutputContent": "missing"},
                       {"Status": "Success", "ResponseCode": 1, "StandardOutputContent": "missing"},
                       {"Status": "Success", "ResponseCode": 0, "StandardOutputContent": "unexpected"}):
            with self.subTest(result=result):
                manager = mock.Mock(side_effect=[{"Command": {"CommandId": "probe"}}, {}, result])
                with self.assertRaisesRegex(RuntimeError, "inconclusive"):
                    cli.install_efs_client("i-test", "us-east-1", only_missing=True, manager=manager)
                self.assertEqual(manager.call_count, 3)

    def test_probe_api_or_waiter_failure_never_installs(self):
        for step, message in enumerate(("AccessDenied", "Waiter CommandExecuted failed: Max attempts exceeded",
                                        "InvocationDoesNotExist")):
            with self.subTest(step=step):
                error = subprocess.CalledProcessError(255, ["aws"], stderr=message)
                replies = [{"Command": {"CommandId": "probe"}}, {}][:step] + [error]
                manager = mock.Mock(side_effect=replies)
                with self.assertRaises(subprocess.CalledProcessError) as caught:
                    cli.install_efs_client("i-test", "us-east-1", only_missing=True, manager=manager)
                self.assertIs(caught.exception, error)
                self.assertEqual(manager.call_count, step + 1)


if __name__ == "__main__":
    unittest.main()
