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
    @mock.patch.object(cli, "aws")
    def test_destroy_requires_an_unambiguous_explicit_target(self, aws, run):
        for args in ([], ["--delete-stack"], [STACK, "--stack", "other"],
                     ["--stack", STACK, "--name", "other"]):
            with self.subTest(args=args), contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit) as error:
                    self.invoke(["destroy", *args])
                self.assertEqual(error.exception.code, 2)
        run.assert_not_called()
        aws.assert_not_called()




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




    @mock.patch.object(cli, "instance_id", return_value=INSTANCE)
    @mock.patch.object(cli, "aws")
    @mock.patch("time.sleep")
    def test_readiness_failure_reports_last_native_check_and_ssm_identity(self, sleep, aws, iid):
        unsafe = ("inactive\nSECRET_STDOUT\nprefix Checking DCV console session\n"
                  "Checking NVIDIA T4 SECRET_SUFFIX\n")
        for markers, label in [
            ("Checking RES desktop readiness marker\nChecking GDM and DCV services\n", "GDM and DCV services"),
            ("", "not reported"),
        ]:
            with self.subTest(label=label), mock.patch.object(cli.progress, "checkpoint") as checkpoint:
                aws.reset_mock()
                aws.side_effect = [
                    {"Command": {"CommandId": "readiness-command"}},
                    {"Status": "Failed", "StandardOutputContent": markers + unsafe,
                     "StandardErrorContent": "Checking NVIDIA T4\nSECRET_STDERR"},
                ]
                with self.assertRaises(ValueError) as raised:
                    cli.gpu_ready(STACK, "us-east-1")
                message = str(raised.exception)
                self.assertEqual(message, f"GPU readiness failed: instance={INSTANCE} "
                                 f"SSM command=readiness-command; Failed: {label}")
                status = "Failed" if label == "not reported" else "Not ready"
                checkpoint.assert_called_once_with(f"{status}: {label}")
                self.assertNotIn("SECRET", message)
        args = aws.call_args_list[0].args
        import json
        checks = json.loads(args[args.index("--parameters") + 1])["commands"]
        self.assertIn("echo 'Checking RES desktop readiness marker'", checks)
        self.assertTrue(any(check.startswith("dcv describe-session console --json") for check in checks))
        self.assertFalse(any("systemctl restart" in check or "touch " in check for check in checks))


if __name__ == "__main__":
    unittest.main()
