"""Task 2: offline-only IAM/SSM certificate enrollment and cleanup contracts."""
import copy
import io
import json
import subprocess
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest import mock

from runtime import issue43_gpu_acm_setup as setup
from runtime import issue43_hostname_handoff as h
from scripts import studio_workstation_cli as cli
from test_issue43_hostname_handoff import FakeAWS, GPU, NOW


class IAMFake(FakeAWS):
    def __init__(self):
        super().__init__()
        self.gpu_policy = None
        self.cpu_policy = setup._policy(setup.CPU_SESSION)
        self.export_policy = {"Version": "2012-10-17", "Statement": [{
            "Sid": "ExportOnlyOneDcvCertificate", "Effect": "Allow",
            "Action": "acm:ExportCertificate", "Resource": h.CERT_ARN}]}
        self.trust = {"Version": "2012-10-17",
                      "Statement": [setup._statement(setup.CPU_SESSION)]}
        self.fail_trust_once = False
        self.iam_writes = []
        self.gpu["State"]["Name"] = "running"

    def __call__(self, region, *args):
        self.calls.append((region, *args))
        service, operation = args[:2]
        if service == "iam" and operation in ("get-role", "get-role-policy", "list-role-policies",
                                              "put-role-policy", "delete-role-policy",
                                              "update-assume-role-policy"):
            assert region == h.REGION
            name = args[args.index("--role-name") + 1]
            if operation == "get-role":
                if name == setup.BASE_ROLE:
                    return {"Role": {"RoleId": setup.SOURCE_ID, "Arn": setup.SOURCE_ARN}}
                assert name == setup.EXPORT_NAME
                return {"Role": {"Arn": h.EXPORT_ROLE,
                                 "AssumeRolePolicyDocument": copy.deepcopy(self.trust)}}
            if operation == "list-role-policies":
                return {"PolicyNames": [setup.CPU_POLICY] +
                        ([setup.GPU_POLICY] if self.gpu_policy is not None else [])}
            if operation == "get-role-policy":
                policy = args[args.index("--policy-name") + 1]
                if name == setup.EXPORT_NAME:
                    assert policy == "StudioIssue43ExactAcMExport"
                    return {"PolicyDocument": copy.deepcopy(self.export_policy)}
                assert name == setup.BASE_ROLE
                if policy == setup.CPU_POLICY:
                    return {"PolicyDocument": copy.deepcopy(self.cpu_policy)}
                assert policy == setup.GPU_POLICY and self.gpu_policy is not None
                return {"PolicyDocument": copy.deepcopy(self.gpu_policy)}
            self.iam_writes.append((operation, name))
            if operation == "put-role-policy":
                assert name == setup.BASE_ROLE
                self.gpu_policy = json.loads(args[args.index("--policy-document") + 1])
            elif operation == "delete-role-policy":
                assert name == setup.BASE_ROLE and self.gpu_policy is not None
                self.gpu_policy = None
            else:
                assert name == setup.EXPORT_NAME
                if self.fail_trust_once:
                    self.fail_trust_once = False
                    raise RuntimeError("simulated trust update failure")
                self.trust = json.loads(args[args.index("--policy-document") + 1])
            return {}
        return super().__call__(region, *args)


class ACMSetupTests(unittest.TestCase):
    def setUp(self):
        self.aws = IAMFake()

    def test_install_only_exact_gpu_and_preserve_cpu_access(self):
        with (mock.patch.object(h, "gpu_preview", return_value={"to": GPU}) as preview,
              mock.patch.object(h, "check_gpu_tls",
                                side_effect=[RuntimeError("not ready"), None]) as tls,
              mock.patch.object(setup, "_ssm") as ssm):
            info = setup.prepare_gpu(self.aws, sleep=lambda _: None)
        self.assertEqual(info["instance_id"], GPU)
        preview.assert_called()
        self.assertEqual(tls.call_count, 2)
        ssm.assert_called_once()
        self.assertTrue(setup._get_role_state(self.aws, GPU))
        self.assertEqual(self.aws.trust["Statement"][0], setup._statement(setup.CPU_SESSION))
        self.assertEqual(self.aws.gpu_policy, setup._policy(setup.SOURCE_ID + ":" + GPU))

    def test_existing_exact_grant_does_not_rewrite_iam_or_reinstall(self):
        self.assertTrue(setup._install_grant(self.aws, GPU))
        self.aws.iam_writes.clear()
        with (mock.patch.object(h, "gpu_preview", return_value={"to": GPU}),
              mock.patch.object(h, "check_gpu_tls") as tls,
              mock.patch.object(setup, "_ssm") as ssm):
            setup.prepare_gpu(self.aws, sleep=lambda _: None)
        self.assertEqual(tls.call_count, 2)  # Preflight and final TLS checks
        ssm.assert_not_called()
        self.assertEqual(self.aws.iam_writes, [])

    def test_failed_guest_install_revokes_new_grant_without_touching_cpu(self):
        with (mock.patch.object(h, "gpu_preview", return_value={"to": GPU}),
              mock.patch.object(h, "check_gpu_tls", side_effect=RuntimeError("not ready")),
              mock.patch.object(setup, "_ssm", side_effect=RuntimeError("install failed"))):
            with self.assertRaisesRegex(RuntimeError, "install failed"):
                setup.prepare_gpu(self.aws, sleep=lambda _: None)
        self.assertIsNone(self.aws.gpu_policy)
        self.assertEqual(self.aws.trust["Statement"], [setup._statement(setup.CPU_SESSION)])
        self.assertEqual(self.aws.eip["InstanceId"], h.CPU_ID)

    def test_failed_trust_write_rolls_back_inline_policy(self):
        self.aws.fail_trust_once = True
        with self.assertRaisesRegex(RuntimeError, "trust update failure"):
            setup._install_grant(self.aws, GPU)
        self.assertIsNone(self.aws.gpu_policy)
        self.assertEqual(self.aws.trust["Statement"], [setup._statement(setup.CPU_SESSION)])

    def test_foreign_gpu_grant_does_not_get_overwritten(self):
        self.aws.gpu_policy = setup._policy(setup.SOURCE_ID + ":i-abcdef12345678900")
        with self.assertRaisesRegex(ValueError, "different session"):
            setup._install_grant(self.aws, GPU)
        self.assertEqual(self.aws.iam_writes, [])

    def test_partial_trust_state_fails_closed(self):
        self.aws.trust["Statement"].append(setup._statement(setup.SOURCE_ID + ":" + GPU))
        with self.assertRaisesRegex(ValueError, "partial state"):
            setup._install_grant(self.aws, GPU)
        self.assertEqual(self.aws.iam_writes, [])

    def test_gpu_revocation_after_stop_keeps_cpu(self):
        setup._install_grant(self.aws, GPU)
        self.aws.gpu["State"]["Name"] = "stopped"
        res = setup.revoke_gpu(self.aws, GPU)
        self.assertTrue(res["revoked"])
        self.assertFalse(setup._get_role_state(self.aws, GPU))
        self.assertEqual(self.aws.trust["Statement"], [setup._statement(setup.CPU_SESSION)])
        self.assertEqual(self.aws.eip["InstanceId"], h.CPU_ID)
        self.assertFalse(setup.revoke_gpu(self.aws, GPU)["revoked"])

    def test_cleanup_after_gpu_absent_from_ec2_inventory(self):
        setup._install_grant(self.aws, GPU)
        original = self.aws.__call__
        def absent(region, *args):
            if args[:2] == ("ec2", "describe-instances"):
                return {"Reservations": []}
            return original(region, *args)
        result = setup.revoke_gpu(absent, GPU)
        self.assertTrue(result["revoked"])
        self.assertFalse(setup._get_role_state(self.aws, GPU))

    def test_cleanup_never_swallow_aws_permission_failure(self):
        setup._install_grant(self.aws, GPU)
        original = self.aws.__call__
        def denied(region, *args):
            if args[:2] == ("ec2", "describe-instances"):
                raise subprocess.CalledProcessError(254, args, stderr="AccessDenied")
            return original(region, *args)
        with self.assertRaises(subprocess.CalledProcessError):
            setup.revoke_gpu(denied, GPU)
        self.assertTrue(setup._get_role_state(self.aws, GPU))

    def test_revoke_refuses_gpu_eip_or_running_instance(self):
        setup._install_grant(self.aws, GPU)
        self.aws.eip["InstanceId"] = GPU
        with self.assertRaisesRegex(ValueError, "Return EIP"):
            setup.revoke_gpu(self.aws, GPU)
        self.aws.eip["InstanceId"] = h.CPU_ID
        with self.assertRaisesRegex(ValueError, "Stop/terminate"):
            setup.revoke_gpu(self.aws, GPU)
        self.assertTrue(setup._get_role_state(self.aws, GPU))

    def test_expanded_acm_export_policy_blocks_setup(self):
        self.aws.export_policy["Statement"][0]["Resource"] = "*"
        with self.assertRaisesRegex(ValueError, "restricted to one certificate"):
            setup._install_grant(self.aws, GPU)
        self.assertEqual(self.aws.iam_writes, [])

    def test_guest_setup_is_pinned_no_secrets_or_eip_changes(self):
        cmd = "\n".join(setup._guest_script())
        self.assertIn(setup.PIN, cmd)
        self.assertIn(setup.AWCP_SHA256, cmd)
        self.assertIn("AWCP_VERSION=3.1.2", cmd)
        self.assertIn("--enroll-default", cmd)
        self.assertIn("--no-start", cmd)
        self.assertIn("STUDIO_ISSUE43_GPU_ACM_INSTALLED", cmd)
        for forbidden in ("change-resource-record-sets", "associate-address",
                          "password=", "aws_secret_access_key", "cat /var/lib/studio/dcv-acm/privkey.pem"):
            self.assertNotIn(forbidden, cmd)
        self.assertTrue(any(x.startswith("test -f /root/bootstrap/semaphore/") for x in setup._guest_script()))

    def test_ssm_invocation_success_requires_exact_marker(self):
        self.aws.ssm_out = setup.MARKER + "\n"
        setup._ssm(self.aws, GPU, ["echo safe"], sleep=lambda _: None)
        self.aws.ssm_out = "some-other-marker\n"
        with self.assertRaisesRegex(RuntimeError, "missing success marker"):
            setup._ssm(self.aws, GPU, ["echo safe"], sleep=lambda _: None)

    def test_ssm_fails_immediately_on_access_denied(self):
        count = 0
        def denied(region, *args):
            nonlocal count
            if args[:2] == ("ssm", "get-command-invocation"):
                count += 1
                raise subprocess.CalledProcessError(254, args, stderr="AccessDenied")
            return self.aws(region, *args)
        with self.assertRaises(subprocess.CalledProcessError):
            setup._ssm(denied, GPU, ["echo safe"], sleep=lambda _: None)
        self.assertEqual(count, 1)

    def test_cli_requires_execute_before_any_aws_writes(self):
        with (mock.patch.object(cli, "aws") as aws,
              redirect_stderr(io.StringIO())):
            with self.assertRaises(SystemExit):
                cli.main(["hostname-acm-prepare"])
            with self.assertRaises(SystemExit):
                cli.main(["hostname-acm-revoke", "--gpu-instance-id", GPU])
        aws.assert_not_called()

    def test_cli_dispatches_exact_acm_prepare_and_revoke(self):
        with (mock.patch.object(cli, "aws") as aws,
              mock.patch.object(setup, "prepare_gpu", return_value={"instance_id": GPU}) as prep,
              mock.patch.object(setup, "revoke_gpu", return_value={"revoked": True}) as revoke,
              redirect_stdout(io.StringIO())):
            cli.main(["hostname-acm-prepare", "--execute"])
            cli.main(["hostname-acm-revoke", "--execute", "--gpu-instance-id", GPU])
        prep.assert_called_once_with(aws, independent=True)
        revoke.assert_called_once_with(aws, GPU, independent=True)


if __name__ == "__main__":
    unittest.main()
