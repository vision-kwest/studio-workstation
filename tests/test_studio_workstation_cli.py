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

    @mock.patch.object(cli, "run")
    def test_gpu_dry_run_uses_validated_ami_and_persistent_renderer(self, run):
        self.invoke(["create", "--stack", "studio-gpu-test", "--profile", "gpu"])
        calls = [str(c.args[0]) for c in run.call_args_list]
        self.assertTrue(any("--profile', 'gpu'" in c and "--lifecycle', 'persistent'" in c for c in calls))
        self.assertTrue(any(cli.GPU_PREFIX in c for c in calls))
        self.assertTrue(any("ImageId=" + cli.GPU_AMI in c for c in calls) is False)
        self.assertFalse(any("cloudformation', 'deploy'" in c for c in calls))

    @mock.patch.object(cli, "run")
    def test_gpu_rejects_cpu_ami_before_aws(self, run):
        with self.assertRaises(SystemExit):
            self.invoke(["create", "--stack", "studio-gpu-test", "--profile", "gpu",
                         "--image-id", cli.DEFAULT_AMI])
        run.assert_not_called()

    @mock.patch.object(cli, "run")
    def test_gpu_rejects_wrong_instance_type(self, run):
        with self.assertRaises(SystemExit):
            self.invoke(["create", "--stack", "studio-gpu-test", "--profile", "gpu",
                         "--instance-type", "m7i.large"])
        run.assert_not_called()

    @mock.patch.object(cli, "run")
    def test_cpu_rejects_gpu_instance_type(self, run):
        with self.assertRaises(SystemExit):
            self.invoke(["create", "--stack", "studio-cpu-test", "--profile", "cpu",
                         "--instance-type", "g4dn.xlarge"])
        run.assert_not_called()

    @mock.patch.object(cli, "run")
    def test_cpu_dry_run_does_not_select_gpu(self, run):
        self.invoke(["create", "--stack", "studio-cpu-test", "--profile", "cpu"])
        render = run.call_args_list[0].args[0]
        self.assertNotIn("--profile", render)
        self.assertNotIn("--lifecycle", render)

    @mock.patch.object(cli, "run")
    def test_scoped_gpu_refuses_public_ingress(self, run):
        with self.assertRaises(SystemExit):
            self.invoke(["create", "--stack", "studio-gpu-cli-test1", "--profile", "gpu",
                         "--cleanup-token", "unique-test-token-123"])
        run.assert_not_called()

    @mock.patch.object(cli, "run")
    def test_scoped_gpu_requires_guard_compatible_stack_name(self, run):
        with self.assertRaises(SystemExit):
            self.invoke(["create", "--stack", "studio-gpu-cli-" + "x" * 40,
                         "--profile", "gpu", "--cleanup-token", "unique-test-token-123",
                         "--client-cidr", "127.0.0.1/32"])
        run.assert_not_called()

    @mock.patch.object(cli, "run")
    def test_scoped_gpu_accepts_restricted_ingress_dry_run(self, run):
        self.invoke(["create", "--stack", "studio-gpu-cli-test1", "--profile", "gpu",
                     "--cleanup-token", "unique-test-token-123",
                     "--client-cidr", "127.0.0.1/32"])
        calls = [c.args[0] for c in run.call_args_list]
        self.assertTrue(any("--cleanup-token" in c for c in calls))
        self.assertFalse(any("deploy" in c for c in calls))

    @mock.patch.object(cli, "run")
    def test_scoped_gpu_explicit_public_dcv_dry_run(self, run):
        self.invoke(["create", "--stack", "studio-gpu-cli-test1", "--profile", "gpu",
                     "--cleanup-token", "unique-test-token-123",
                     "--client-cidr", "0.0.0.0/0", "--allow-public-dcv"])
        self.assertFalse(any("deploy" in str(c.args[0]) for c in run.call_args_list))

    @mock.patch.object(cli, "run")
    @mock.patch.object(cli, "aws")
    @mock.patch.object(cli, "describe", return_value=STATE)
    def test_scoped_destroy_accepts_launch_template(self, describe, aws, run):
        aws.return_value = {"StackResourceSummaries": [
            {"ResourceType": "AWS::EC2::Instance"},
            {"ResourceType": "AWS::EC2::SecurityGroup"},
            {"ResourceType": "AWS::EC2::LaunchTemplate"}]}
        self.invoke(["destroy", "--stack", "studio-gpu-cli-test1", "--yes",
                     "--confirm-stack", "studio-gpu-cli-test1",
                     "--confirm-instance", INSTANCE, "--delete-stack"])
        self.assertTrue(any("delete-stack" in c.args[0] for c in run.call_args_list))

    @mock.patch.object(cli, "aws")
    def test_gpu_preflight_checks_exact_s3_versions(self, aws):
        aws.side_effect = [
            {"Images": [{"State": "available", "OwnerId": "401233741942"}]},
            {"VersionId": cli.GPU_VERSIONS["installation-scripts.tar.gz"]},
            {"VersionId": cli.GPU_VERSIONS["installation-scripts.tar.gz"]},
            {"VersionId": cli.GPU_VERSIONS["virtual-desktop.tar.gz"]},
            {"VersionId": cli.GPU_VERSIONS["virtual-desktop.tar.gz"]},
        ]
        from types import SimpleNamespace
        cli.gpu_release_preflight(SimpleNamespace(region="us-east-1", bucket=cli.DEFAULT_BUCKET))
        self.assertEqual(aws.call_count, 5)
        calls = [call.args for call in aws.call_args_list]
        self.assertIn(cli.GPU_KEYS["installation-scripts.tar.gz"], calls[1])
        self.assertIn(cli.GPU_KEYS["installation-scripts.tar.gz"], calls[2])
        self.assertIn(cli.GPU_KEYS["virtual-desktop.tar.gz"], calls[3])
        self.assertIn(cli.GPU_KEYS["virtual-desktop.tar.gz"], calls[4])
        self.assertNotEqual(cli.GPU_KEYS["installation-scripts.tar.gz"].rsplit("/", 1)[0],
                            cli.GPU_KEYS["virtual-desktop.tar.gz"].rsplit("/", 1)[0])

    @mock.patch.object(cli, "aws")
    def test_gpu_preflight_fails_closed_on_wrong_version(self, aws):
        aws.side_effect = [
            {"Images": [{"State": "available", "OwnerId": "401233741942"}]},
            {"VersionId": "unexpected"},
        ]
        from types import SimpleNamespace
        with self.assertRaises(ValueError):
            cli.gpu_release_preflight(SimpleNamespace(region="us-east-1", bucket=cli.DEFAULT_BUCKET))

    @mock.patch.object(cli, "aws")
    def test_gpu_preflight_rejects_changed_latest_s3_object(self, aws):
        aws.side_effect = [
            {"Images": [{"State": "available", "OwnerId": "401233741942"}]},
            {"VersionId": cli.GPU_VERSIONS["installation-scripts.tar.gz"]},
            {"VersionId": "unexpected-latest"},
        ]
        from types import SimpleNamespace
        with self.assertRaises(ValueError):
            cli.gpu_release_preflight(SimpleNamespace(region="us-east-1", bucket=cli.DEFAULT_BUCKET))

    @mock.patch.object(cli, "instance_id", return_value=INSTANCE)
    @mock.patch.object(cli, "aws")
    @mock.patch("time.sleep")
    def test_gpu_ready_checks_ssm_success(self, sleep, aws, iid):
        aws.side_effect = [
            {"Command": {"CommandId": "test-command"}},
            {"Status": "Success"},
        ]
        with contextlib.redirect_stdout(io.StringIO()):
            cli.gpu_ready(STACK, "us-east-1")
        self.assertEqual(aws.call_count, 2)

    @mock.patch.object(cli, "instance_id", return_value=INSTANCE)
    @mock.patch.object(cli, "aws")
    @mock.patch("time.sleep")
    def test_gpu_ready_fails_on_ssm_failure(self, sleep, aws, iid):
        aws.side_effect = [
            {"Command": {"CommandId": "test-command"}},
            {"Status": "Failed", "StandardErrorContent": "DCV not ready"},
        ]
        with self.assertRaises(ValueError):
            cli.gpu_ready(STACK, "us-east-1")


if __name__ == "__main__":
    unittest.main()
