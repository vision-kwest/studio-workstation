"""Offline safety tests for the Studio CLI. No AWS calls."""
import contextlib
import io
import unittest
from unittest import mock
from scripts import studio_workstation_cli as cli

INSTANCE = "i-0bbfbdc878d3a2320"
STACK = "studio-workstation-cpu-v013"
STATE = {"stack": STACK, "instance": INSTANCE, "state": "running",
         "public_ip": "44.202.195.142", "dcv_url": "https://44.202.195.142:8443"}

class StudioCliTests(unittest.TestCase):
    def invoke(self, argv):
        with contextlib.redirect_stdout(io.StringIO()):
            cli.main(argv)

    @mock.patch.object(cli, "run")
    @mock.patch.object(cli, "describe", return_value=STATE)
    def test_stop_refuses_without_exact_confirmation(self, describe, run):
        with self.assertRaises(SystemExit):
            self.invoke(["stop", "--stack", STACK, "--yes"])
        run.assert_not_called()

    @mock.patch.object(cli, "run")
    @mock.patch.object(cli, "describe", return_value=STATE)
    def test_stop_uses_exact_instance_id(self, describe, run):
        self.invoke(["stop", "--stack", STACK, "--yes", "--confirm-instance", INSTANCE])
        self.assertEqual(run.call_args_list[0].args[0][0:4],
                         ["aws", "ec2", "stop-instances", "--instance-ids"])

    @mock.patch.object(cli, "run")
    @mock.patch.object(cli, "describe", return_value=STATE)
    def test_start_refuses_running_instance(self, describe, run):
        with self.assertRaises(SystemExit):
            self.invoke(["start", "--yes", "--confirm-instance", INSTANCE])
        run.assert_not_called()

    @mock.patch.object(cli, "run")
    @mock.patch.object(cli, "aws")
    @mock.patch.object(cli, "describe", return_value=STATE)
    def test_destroy_requires_explicit_delete_flag(self, describe, aws, run):
        with self.assertRaises(SystemExit):
            self.invoke(["destroy", "--yes", "--confirm-stack", STACK,
                         "--confirm-instance", INSTANCE])
        run.assert_not_called()
        aws.assert_not_called()

    @mock.patch.object(cli, "run")
    @mock.patch.object(cli, "describe", return_value=STATE)
    def test_connect_prints_url_without_aws_mutation(self, describe, run):
        self.invoke(["connect"])
        run.assert_not_called()

    @mock.patch.object(cli, "run")
    def test_create_rejects_invalid_cidr_without_running(self, run):
        with self.assertRaises(SystemExit):
            self.invoke(["create", "--stack", "studio-workstation-cpu-test",
                         "--client-cidr", "999.0.0.1/32"])
        run.assert_not_called()

if __name__ == "__main__":
    unittest.main()
