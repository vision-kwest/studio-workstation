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
    def test_stop_refuses_wrong_ec2_state(self, describe, run):
        describe.return_value = dict(STATE, state="stopped")
        with self.assertRaises(SystemExit):
            self.invoke(["stop", "--stack", STACK])
        run.assert_not_called()

    @mock.patch.object(cli, "run")
    @mock.patch.object(cli, "describe", return_value=STATE)
    def test_stop_uses_exact_instance_id(self, describe, run):
        self.invoke(["stop", "--stack", STACK])
        self.assertEqual(run.call_args_list[0].args[0][0:4],
                         ["aws", "ec2", "stop-instances", "--instance-ids"])

    @mock.patch.object(cli, "run")
    @mock.patch.object(cli, "describe", return_value=STATE)
    def test_start_refuses_running_instance(self, describe, run):
        with self.assertRaises(SystemExit):
            self.invoke(["start"])
        run.assert_not_called()

    @mock.patch.object(cli, "run")
    @mock.patch.object(cli, "aws")
    @mock.patch.object(cli, "describe", return_value=STATE)
    def test_destroy_requires_explicit_delete_flag(self, describe, aws, run):
        with self.assertRaises(SystemExit):
            self.invoke(["destroy", "--stack", STACK])
        run.assert_not_called()
        aws.assert_not_called()

    @mock.patch.object(cli, "run")
    @mock.patch.object(cli, "describe", return_value=STATE)
    def test_connect_prints_url_without_aws_mutation(self, describe, run):
        with mock.patch.object(cli, "trusted_url",
                               return_value="https://dcv.kwesidavis.com:8443") as tls:
            self.invoke(["connect"])
        tls.assert_called_once_with(STACK, INSTANCE)
        run.assert_not_called()

    def test_trusted_hostname_connect_uses_verified_stack_identity(self):
        vm = dict(STATE, public_ip="54.159.180.104", state="running")
        with mock.patch.object(cli, "describe", return_value=vm), \
             mock.patch.object(cli, "trusted_url",
                               return_value="https://dcv.kwesidavis.com:8443") as tls, \
             mock.patch.object(cli, "run") as run, \
             contextlib.redirect_stdout(io.StringIO()) as output:
            cli.main(["connect", "--stack", STACK,
                      "--hostname", "dcv.kwesidavis.com"])
        tls.assert_called_once_with(STACK, INSTANCE)
        self.assertIn("https://dcv.kwesidavis.com:8443", output.getvalue())
        run.assert_not_called()

    def test_trusted_hostname_rejected_when_stopped(self):
        vm = dict(STATE, public_ip="54.159.180.104", state="stopped")
        with mock.patch.object(cli, "describe", return_value=vm), \
             mock.patch.object(cli, "verified_dcv_hostname") as tls, \
             mock.patch.object(cli, "run") as run, \
             contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit):
                cli.main(["status", "--stack", STACK,
                          "--hostname", "dcv.kwesidavis.com"])
        tls.assert_not_called()
        run.assert_not_called()

    def test_trusted_hostname_is_not_allowed_with_ssm(self):
        with mock.patch.object(cli, "describe") as describe, \
             mock.patch.object(cli, "run") as run, \
             contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit):
                cli.main(["connect", "--stack", STACK, "--ssm",
                          "--hostname", "dcv.kwesidavis.com"])
        describe.assert_not_called()
        run.assert_not_called()

    def test_trusted_dns_mismatch_fails_before_network_connection(self):
        fake = [(None, None, None, None, ("203.0.113.10", 8443))]
        with mock.patch.object(cli.socket, "getaddrinfo", return_value=fake), \
             mock.patch.object(cli.socket, "create_connection") as conn:
            with self.assertRaisesRegex(ValueError, "DNS A record does not match"):
                cli.verified_dcv_hostname("dcv.kwesidavis.com", "54.159.180.104")
        conn.assert_not_called()

    def test_trusted_connection_pins_verified_ip_and_preserves_hostname_sni(self):
        fake = [(None, None, None, None, ("54.159.180.104", 8443))]
        import ssl
        with mock.patch.object(cli.socket, "getaddrinfo", return_value=fake), \
             mock.patch.object(cli.socket, "create_connection") as conn, \
             mock.patch.object(cli.ssl, "create_default_context") as context:
            tlsctx = context.return_value
            url = cli.verified_dcv_hostname("dcv.kwesidavis.com", "54.159.180.104")
        self.assertEqual(url, "https://dcv.kwesidavis.com:8443")
        conn.assert_called_once_with(("54.159.180.104", 8443), timeout=5)
        tlsctx.wrap_socket.assert_called_once_with(conn.return_value.__enter__.return_value,
                                                   server_hostname="dcv.kwesidavis.com")

    @mock.patch.object(cli, "run")
    def test_create_rejects_invalid_cidr_without_running(self, run):
        with self.assertRaises(SystemExit):
            self.invoke(["create", "--stack", "studio-workstation-cpu-test",
                         "--client-cidr", "999.0.0.1/32"])
        run.assert_not_called()

    @mock.patch.object(cli, "run")
    def test_gpu_dry_run_uses_validated_ami_and_persistent_renderer(self, run):
        self.invoke(["create", "--stack", "studio-gpu-test", "--profile", "gpu", "--dry-run"])
        calls = [str(c.args[0]) for c in run.call_args_list]
        self.assertTrue(any("--profile', 'gpu'" in c and "--lifecycle', 'persistent'" in c for c in calls))
        self.assertTrue(any(cli.GPU_PREFIX in c for c in calls))
        self.assertTrue(any("ImageId=" + cli.GPU_AMI in c for c in calls) is False)
        self.assertFalse(any("cloudformation', 'deploy'" in c for c in calls))

    @mock.patch.object(cli, "run")
    def test_gpu_rejects_cpu_ami_before_aws(self, run):
        with self.assertRaises(SystemExit):
            self.invoke(["create", "--stack", "studio-gpu-test", "--profile", "gpu",
                         "--image-id", "ami-027c98f85b155c2ac"])
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
        self.invoke(["create", "--stack", "studio-cpu-test", "--profile", "cpu", "--dry-run"])
        render = run.call_args_list[0].args[0]
        self.assertNotIn("--profile", render)
        self.assertNotIn("--lifecycle", render)

    @mock.patch.object(cli, "run")
    def test_scoped_gpu_refuses_public_ingress(self, run):
        with self.assertRaises(SystemExit):
            self.invoke(["create", "--stack", "studio-gpu-cli-test1", "--profile", "gpu",
                         "--cleanup-token", "unique-test-token-123", "--dry-run"])
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
                     "--client-cidr", "127.0.0.1/32", "--dry-run"])
        calls = [c.args[0] for c in run.call_args_list]
        self.assertTrue(any("--cleanup-token" in c for c in calls))
        self.assertFalse(any("deploy" in c for c in calls))

    @mock.patch.object(cli, "run")
    def test_scoped_gpu_explicit_public_dcv_dry_run(self, run):
        self.invoke(["create", "--stack", "studio-gpu-cli-test1", "--profile", "gpu",
                     "--cleanup-token", "unique-test-token-123",
                     "--client-cidr", "0.0.0.0/0", "--allow-public-dcv", "--dry-run"])
        self.assertFalse(any("deploy" in str(c.args[0]) for c in run.call_args_list))

    @mock.patch.object(cli, "run")
    @mock.patch.object(cli, "aws")
    @mock.patch.object(cli, "describe", return_value=STATE)
    def test_nonapproved_gpu_destroy_fails_before_aws(self, describe, aws, run):
        with contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit):
                self.invoke(["destroy", "--stack", "studio-gpu-cli-test1", "--delete-stack"])
        aws.assert_not_called()
        run.assert_not_called()

    @mock.patch.object(cli.subprocess, "check_output", return_value="{}")
    def test_aws_json_requests_never_open_pager(self, check_output):
        self.assertEqual(cli.aws("us-east-1", "sts", "get-caller-identity"), {})
        command = check_output.call_args.args[0]
        self.assertEqual(command[0], "aws")
        self.assertIn("--no-cli-pager", command)

    @mock.patch.object(cli.subprocess, "check_output", return_value="")
    def test_successful_empty_aws_write_response_is_not_json_error(self, check_output):
        self.assertEqual(cli.aws("us-east-1", "ec2", "create-tags",
                                 "--resources", "eipalloc-review", "--tags", "Key=Test,Value=Yes"), {})
        self.assertEqual(check_output.call_count, 1)

    @mock.patch.object(cli.subprocess, "check_output",
                       side_effect=__import__("subprocess").CalledProcessError(
                           254, "aws", stderr="AccessDenied"))
    def test_failed_aws_write_is_not_misread_as_empty_success(self, check_output):
        with self.assertRaises(__import__("subprocess").CalledProcessError):
            cli.aws("us-east-1", "iam", "delete-role-policy",
                    "--role-name", "example", "--policy-name", "example")

    @mock.patch.object(cli, "run")
    def test_create_dry_run_validates_without_launch(self, run):
        self.invoke(["create", "--stack", "studio-cpu-test", "--dry-run"])
        calls = [c.args[0] for c in run.call_args_list]
        self.assertTrue(any("validate-template" in c for c in calls))
        self.assertFalse(any("deploy" in c or "create-stack" in c for c in calls))

    @mock.patch.object(cli, "aws")
    def test_gpu_preflight_rejects_old_baked_ami_owner(self, aws):
        from types import SimpleNamespace
        aws.return_value = {"Images": [{"State": "available", "OwnerId": "401233741942"}]}
        with self.assertRaisesRegex(ValueError, "unexpected owner"):
            cli.gpu_release_preflight(SimpleNamespace(region="us-east-1", bucket=cli.DEFAULT_BUCKET))
        aws.assert_called_once()

    @mock.patch.object(cli, "aws")
    def test_gpu_preflight_checks_exact_s3_versions(self, aws):
        aws.side_effect = [
            {"Images": [{"State": "available", "OwnerId": cli.GPU_AMI_OWNER}]},
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
            {"Images": [{"State": "available", "OwnerId": cli.GPU_AMI_OWNER}]},
            {"VersionId": "unexpected"},
        ]
        from types import SimpleNamespace
        with self.assertRaises(ValueError):
            cli.gpu_release_preflight(SimpleNamespace(region="us-east-1", bucket=cli.DEFAULT_BUCKET))

    @mock.patch.object(cli, "aws")
    def test_gpu_preflight_rejects_changed_latest_s3_object(self, aws):
        aws.side_effect = [
            {"Images": [{"State": "available", "OwnerId": cli.GPU_AMI_OWNER}]},
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


class GpuExpirationCliParsingTests(unittest.TestCase):
    """Task 1 parsing contracts. Never create an AWS resource."""

    def options(self, profile="gpu", expires=None, cleanup=None):
        from types import SimpleNamespace
        return SimpleNamespace(profile=profile, expires_in=expires,
                               volume_cleanup_delay=cleanup)

    def invoke(self, *options):
        args = ["create", "--profile", "gpu", "--stack",
                "studio-gpu-cli-accept65", *options]
        with contextlib.redirect_stdout(io.StringIO()) as output:
            cli.main(args)
        return output.getvalue()

    def test_defaults_match_original_pr65_guard(self):
        self.assertEqual(cli.gpu_expiration_options(self.options()), (150, 45))
        self.assertEqual(cli.gpu_expiration_options(self.options("cpu")), None)

    def test_bounded_values_include_original_guard_boundaries(self):
        for expiry in (60, 150, 240):
            for delay in (30, 45, 120):
                with self.subTest(expiry=expiry, delay=delay):
                    self.assertEqual(
                        cli.gpu_expiration_options(self.options(
                            expires=expiry, cleanup=delay)), (expiry, delay))

    def test_out_of_bounds_values_fail_before_any_aws_call(self):
        for value in ("59m", "241m"):
            with self.subTest(expiry=value), \
                 mock.patch.object(cli, "run") as run, \
                 mock.patch.object(cli, "aws") as aws, \
                 contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit):
                    self.invoke("--expires-in", value, "--dry-run")
                run.assert_not_called()
                aws.assert_not_called()
        for value in ("29m", "121m"):
            with self.subTest(cleanup=value), \
                 mock.patch.object(cli, "run") as run, \
                 contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit):
                    self.invoke("--volume-cleanup-delay", value, "--dry-run")
                run.assert_not_called()

    def test_duration_requires_explicit_whole_minutes(self):
        for value in ("180", "3h", "0m", "-1m", "1.5m", "180s", "1M", "5000m"):
            with self.subTest(value=value), \
                 mock.patch.object(cli, "run") as run, \
                 contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit):
                    self.invoke("--expires-in", value, "--dry-run")
                run.assert_not_called()

    def test_cpu_rejects_both_gpu_only_options(self):
        for flag in ("--expires-in", "--volume-cleanup-delay"):
            with self.subTest(flag=flag), \
                 mock.patch.object(cli, "run") as run, \
                 contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit):
                    cli.main(["create", "--profile", "cpu", "--stack",
                              "studio-cpu-test", flag, "90m", "--dry-run"])
                run.assert_not_called()

    def test_dry_run_accepts_valid_custom_gpu_durations_without_launch(self):
        with mock.patch.object(cli, "run") as run:
            self.invoke("--expires-in", "180m", "--volume-cleanup-delay",
                        "60m", "--dry-run")
        calls = [x.args[0] for x in run.call_args_list]
        self.assertTrue(any("validate-template" in cmd for cmd in calls))
        self.assertFalse(any("create-stack" in cmd or "deploy" in cmd for cmd in calls))

    def test_live_expiration_options_forward_to_guarded_creator(self):
        from subprocess import CalledProcessError
        from runtime import pr65_gpu_create as flow
        scenarios = [
            ((), (150, 45)),
            (("--expires-in", "180m"), (180, 45)),
            (("--volume-cleanup-delay", "60m"), (150, 60)),
            (("--expires-in", "240m", "--volume-cleanup-delay", "120m"), (240, 120)),
        ]
        for flags, expected in scenarios:
            with self.subTest(flags=flags), \
                 mock.patch.object(cli, "run") as render, \
                 mock.patch.object(cli, "aws", side_effect=CalledProcessError(
                     255, "aws", stderr="Stack with id does not exist")) as aws, \
                 mock.patch.object(cli, "gpu_release_preflight"), \
                 mock.patch.object(cli, "shared_gpu_source_ready"), \
                 mock.patch.object(flow, "create_guarded",
                                   return_value={"guard": {"guard_armed": True,
                                                            "instance_id": "i-0123456789abcdef0"},
                                                  "terminate_at": "2026-11-01T20:30:00+00:00",
                                                  "volume_cleanup_at": "2026-11-01T21:15:00+00:00"}) as guarded, \
                 mock.patch.object(cli, "complete_gpu_setup",
                                   return_value="https://dcv.kwesidavis.com:8443") as setup:
                output = self.invoke(*flags, "--client-cidr", "8.8.8.8/32",
                                     "--cleanup-token", cli.GPU_ACCEPTANCE_TOKEN)
                self.assertIn('"expiration"', output)
                self.assertIn('"termination_utc"', output)
                self.assertIn('"ebs_cleanup_utc"', output)
                self.assertIn('"remaining_minutes"', output)
                guarded.assert_called_once()
                self.assertEqual(guarded.call_args.kwargs,
                                 {"minutes": expected[0],
                                  "volume_delay_minutes": expected[1]})
                setup.assert_called_once()
                self.assertFalse(any("deploy" in str(c.args[0]) for c in
                                     render.call_args_list))
                aws.assert_called_once()

    def test_explicit_default_values_remain_backward_compatible(self):
        self.assertEqual(cli.gpu_expiration_options(
            self.options(expires=150, cleanup=45)), (150, 45))


if __name__ == "__main__":
    unittest.main()
