"""Issue #43 CPU/GPU shared hostname offline failure-path tests. NEVER call AWS."""
import copy
import datetime as dt
import io
import json
import subprocess
import unittest
from contextlib import redirect_stdout, redirect_stderr
from unittest import mock

from runtime import issue43_hostname_handoff as h
from scripts import studio_workstation_cli as cli

GPU = "i-0123456789abcdef0"
VOLUME = "vol-0123456789abcdef0"
NOW = dt.datetime(2026, 11, 2, 7, 0, 0, tzinfo=dt.timezone.utc)
EXPIRES = "2026-11-02T09:00:00"
VOLUME_CLEANUP = "2026-11-02T10:00:00"


class FakeAWS:
    def __init__(self):
        tags = [{"Key": "StudioStack", "Value": h.GPU_STACK},
                {"Key": "StudioCleanupToken", "Value": h.GPU_TOKEN}]
        self.cpu = {"InstanceId": h.CPU_ID, "State": {"Name": "stopped"},
                    "VpcId": "vpc-approved", "NetworkInterfaces": [{"NetworkInterfaceId": h.CPU_ENI}]}
        self.gpu = {"InstanceId": GPU, "State": {"Name": "running"}, "VpcId": "vpc-approved",
                    "ImageId": h.GPU_AMI, "InstanceType": "g4dn.xlarge",
                    "IamInstanceProfile": {"Arn": h.BASE_PROFILE},
                    "Tags": copy.deepcopy(tags),
                    "NetworkInterfaces": [{"NetworkInterfaceId": "eni-0123456789abcdef0",
                                           "Attachment": {"DeviceIndex": 0, "Status": "attached"}}],
                    "BlockDeviceMappings": [{"DeviceName": "/dev/sda1",
                                              "Ebs": {"VolumeId": VOLUME, "DeleteOnTermination": False}}]}
        self.volume = {"VolumeId": VOLUME, "State": "in-use", "Tags": copy.deepcopy(tags),
                       "Attachments": [{"InstanceId": GPU, "VolumeId": VOLUME, "Device": "/dev/sda1"}]}
        self.eip = {"AllocationId": h.EIP_ID, "PublicIp": h.EIP_IP,
                    "InstanceId": h.CPU_ID, "NetworkInterfaceId": h.CPU_ENI,
                    "AssociationId": "eipassoc-previous",
                    "Tags": [{"Key": "StudioIssue", "Value": "43"},
                             {"Key": "StudioDcvHostname", "Value": h.HOST},
                             {"Key": "StudioOwnerStack", "Value": h.CPU_STACK},
                             {"Key": "StudioDcvInstanceId", "Value": h.CPU_ID}]}
        self.records = [{"Name": h.HOST + ".", "Type": "A", "TTL": 300,
                         "ResourceRecords": [{"Value": h.EIP_IP}]}]
        self.schedules = {
            "studio-pr65-i-" + GPU: self._sched("studio-pr65-i-" + GPU, h.guard.TERMINATE,
                                                {"InstanceIds": [GPU]}, EXPIRES),
            "studio-pr65-v-" + VOLUME: self._sched("studio-pr65-v-" + VOLUME, h.guard.DELETE_VOLUME,
                                                  {"VolumeId": VOLUME}, VOLUME_CLEANUP)
        }
        self.calls = []
        self.writes = []
        self.fail_on = None
        self.next_assoc = 0
        self.ssm_status = "Success"
        self.ssm_out = "STUDIO_ISSUE43_GPU_TLS_READY\n"

    @staticmethod
    def _sched(name, target, payload, when):
        return {"Name": name, "GroupName": h.guard.GROUP, "State": "ENABLED",
                "ActionAfterCompletion": "DELETE", "FlexibleTimeWindow": {"Mode": "OFF"},
                "ScheduleExpressionTimezone": "UTC", "ScheduleExpression": "at(" + when + ")",
                "Target": {"Arn": target, "RoleArn": h.guard.ROLE,
                           "RetryPolicy": {"MaximumRetryAttempts": 3, "MaximumEventAgeInSeconds": 3600},
                           "Input": json.dumps(payload)}}

    def __call__(self, region, *args):
        self.calls.append((region, *args))
        assert region == h.REGION
        service, op = args[:2]
        if op in ("associate-address", "disassociate-address", "create-tags", "send-command"):
            self.writes.append((service, op, *args[2:]))
            if self.fail_on == op:
                raise RuntimeError("Simulated failure on " + op)
        if (service, op) == ("scheduler", "get-schedule-group"):
            return {"Name": h.guard.GROUP, "State": "ACTIVE"}
        if (service, op) == ("iam", "get-role"):
            return {"Role": {
                "Arn": h.guard.ROLE,
                "AssumeRolePolicyDocument": {"Version": "2012-10-17", "Statement": [{
                    "Effect": "Allow", "Action": "sts:AssumeRole",
                    "Principal": {"Service": "scheduler.amazonaws.com"},
                    "Condition": {
                        "ArnEquals": {"aws:SourceArn": "arn:aws:scheduler:us-east-1:401233741942:schedule-group/" + h.guard.GROUP},
                        "StringEquals": {"aws:SourceAccount": h.ACCOUNT}}}]}}}
        if (service, op) == ("iam", "get-role-policy"):
            return {"PolicyDocument": {"Version": "2012-10-17", "Statement": [{
                "Effect": "Allow", "Action": "ec2:TerminateInstances",
                "Resource": "arn:aws:ec2:us-east-1:401233741942:instance/*",
                "Condition": {"StringEquals": {
                    "ec2:ResourceTag/StudioStack": h.GPU_STACK,
                    "ec2:ResourceTag/StudioCleanupToken": h.GPU_TOKEN}}},
                {"Effect": "Allow", "Action": "ec2:DeleteVolume",
                 "Resource": "arn:aws:ec2:us-east-1:401233741942:volume/*",
                 "Condition": {"StringEquals": {
                     "ec2:ResourceTag/StudioStack": h.GPU_STACK,
                     "ec2:ResourceTag/StudioCleanupToken": h.GPU_TOKEN}}}]}}
        if (service, op) == ("iam", "list-role-policies"):
            return {"PolicyNames": [h.pr65_gpu_create.POLICY]}
        if (service, op) == ("iam", "list-attached-role-policies"):
            return {"AttachedPolicies": []}
        if (service, op) == ("cloudformation", "describe-stack-resource"):
            stack = args[args.index("--stack-name") + 1]
            iid = h.CPU_ID if stack == h.CPU_STACK else GPU
            return {"StackResourceDetail": {"StackName": stack, "LogicalResourceId": "Workstation",
                                            "PhysicalResourceId": iid, "ResourceStatus": "CREATE_COMPLETE"}}
        if (service, op) == ("ec2", "describe-instances"):
            ids = args[args.index("--instance-ids") + 1:]
            instances = {h.CPU_ID: self.cpu, GPU: self.gpu}
            return {"Reservations": [{"Instances": [copy.deepcopy(instances[i]) for i in ids]}]}
        if (service, op) == ("ec2", "describe-volumes"):
            return {"Volumes": [copy.deepcopy(self.volume)]}
        if (service, op) == ("ec2", "describe-addresses"):
            return {"Addresses": [copy.deepcopy(self.eip)]}
        if (service, op) == ("route53", "list-resource-record-sets"):
            return {"ResourceRecordSets": copy.deepcopy(self.records)}
        if (service, op) == ("scheduler", "get-schedule"):
            name = args[args.index("--name") + 1]
            return copy.deepcopy(self.schedules[name])
        if (service, op) == ("ssm", "send-command"):
            return {"Command": {"CommandId": "cmd-test"}}
        if (service, op) == ("ssm", "get-command-invocation"):
            return {"Status": self.ssm_status, "ResponseCode": 0,
                    "StandardOutputContent": self.ssm_out}
        if (service, op) == ("ec2", "disassociate-address"):
            aid = args[args.index("--association-id") + 1]
            assert self.eip.get("AssociationId") == aid
            self.eip.pop("AssociationId", None)
            self.eip.pop("InstanceId", None)
            self.eip.pop("NetworkInterfaceId", None)
            if self.fail_on == "disassociate-address-after":
                raise RuntimeError("Simulated network error after disassociation")
            return {}
        if (service, op) == ("ec2", "associate-address"):
            if self.eip.get("AssociationId"):
                raise RuntimeError("Refuse reassociation while already attached")
            assert "--no-allow-reassociation" in args
            iid = args[args.index("--instance-id") + 1]
            self.next_assoc += 1
            self.eip.update({"InstanceId": iid, "NetworkInterfaceId": h.CPU_ENI if iid == h.CPU_ID else self.gpu["NetworkInterfaces"][0]["NetworkInterfaceId"],
                             "AssociationId": "eipassoc-new" + str(self.next_assoc)})
            return {"AssociationId": self.eip["AssociationId"]}
        if (service, op) == ("ec2", "create-tags"):
            spec = args[args.index("--tags") + 1]
            _, kv = spec.split("Key=", 1)
            key, value = kv.split(",Value=", 1)
            for t in self.eip["Tags"]:
                if t["Key"] == key:
                    t["Value"] = value
                    break
            return {}
        raise AssertionError("Unexpected operation: " + repr(args))


class HandoffTests(unittest.TestCase):
    def setUp(self):
        self.aws = FakeAWS()

    def test_plan_read_only_exact_gpu_and_expiration(self):
        p = h.gpu_preview(self.aws, now=NOW)
        self.assertEqual((p["from"], p["to"]), (h.CPU_ID, GPU))
        self.assertEqual(p["gpu_eni"], "eni-0123456789abcdef0")
        self.assertEqual(p["guard"]["root_volume_id"], VOLUME)
        self.assertTrue(p["writes_enabled"])
        self.assertEqual(self.aws.writes, [])
        self.assertNotIn("gpu_tls_ready", p)

    def test_live_action_requires_explicit_authorization_before_aws_access(self):
        with self.assertRaisesRegex(RuntimeError, "explicit execution authorization"):
            h.handoff_gpu(self.aws, lambda *_: None, allow_live=False)
        with self.assertRaisesRegex(RuntimeError, "explicit execution authorization"):
            h.restore_cpu(self.aws, GPU, allow_live=False)
        self.assertEqual(self.aws.calls, [])

    def test_cpu_must_be_stopped(self):
        self.aws.cpu["State"]["Name"] = "running"
        with self.assertRaisesRegex(ValueError, "CPU source must be stopped"):
            h.gpu_preview(self.aws, now=NOW)
        self.assertEqual(self.aws.writes, [])

    def test_gpu_must_be_running_and_match_exact_image_profile(self):
        self.aws.gpu["State"]["Name"] = "stopped"
        with self.assertRaisesRegex(ValueError, "GPU must be running"):
            h.gpu_preview(self.aws, now=NOW)
        self.aws.gpu["State"]["Name"] = "running"
        self.aws.gpu["ImageId"] = "ami-unapproved"
        with self.assertRaisesRegex(ValueError, "GPU AMI"):
            h.gpu_preview(self.aws, now=NOW)
        self.aws.gpu["ImageId"] = h.GPU_AMI
        self.aws.gpu["IamInstanceProfile"]["Arn"] = "unapproved"
        with self.assertRaisesRegex(ValueError, "GPU AMI"):
            h.gpu_preview(self.aws, now=NOW)

    def test_unknown_eip_owner_dns_or_ownership_tags_fail_closed(self):
        self.aws.eip["InstanceId"] = "i-0badbadbad"
        with self.assertRaisesRegex(ValueError, "Current EIP owner"):
            h.gpu_preview(self.aws, now=NOW)
        self.aws.eip["InstanceId"] = h.CPU_ID
        self.aws.records[0]["ResourceRecords"] = [{"Value": "203.0.113.9"}]
        with self.assertRaisesRegex(ValueError, "Route 53 A record"):
            h.gpu_preview(self.aws, now=NOW)
        self.aws.records[0]["ResourceRecords"] = [{"Value": h.EIP_IP}]
        self.aws.gpu["Tags"].append({"Key": "StudioStack", "Value": "duplicate"})
        with self.assertRaisesRegex(ValueError, "Duplicate resource tag"):
            h.gpu_preview(self.aws, now=NOW)

    def test_missing_or_expired_or_disabled_gpu_schedules_block(self):
        self.aws.schedules["studio-pr65-i-" + GPU]["State"] = "DISABLED"
        with self.assertRaisesRegex(ValueError, "guard mismatch"):
            h.gpu_preview(self.aws, now=NOW)
        self.aws.schedules["studio-pr65-i-" + GPU]["State"] = "ENABLED"
        self.aws.schedules["studio-pr65-i-" + GPU]["ScheduleExpression"] = "at(2026-11-02T07:15:00)"
        with self.assertRaisesRegex(ValueError, "expiration is too near"):
            h.gpu_preview(self.aws, now=NOW)
        self.aws.schedules["studio-pr65-i-" + GPU]["ScheduleExpression"] = "at(" + EXPIRES + ")"
        self.aws.schedules["studio-pr65-v-" + VOLUME]["Target"]["Input"] = '{"VolumeId":"vol-wrong"}'
        with self.assertRaisesRegex(ValueError, "guard mismatch"):
            h.gpu_preview(self.aws, now=NOW)
        self.assertEqual(self.aws.writes, [])

    def test_gpu_existing_unrelated_eip_or_secondary_eni_refused(self):
        self.aws.gpu["NetworkInterfaces"][0]["Association"] = {
            "AllocationId": "eipalloc-different", "PublicIp": "203.0.113.12"}
        with self.assertRaisesRegex(ValueError, "do not replace it"):
            h.gpu_preview(self.aws, now=NOW)
        self.aws.gpu["NetworkInterfaces"][0].pop("Association")
        self.aws.gpu["NetworkInterfaces"][0]["Attachment"]["DeviceIndex"] = 1
        with self.assertRaisesRegex(ValueError, "primary ENI"):
            h.gpu_preview(self.aws, now=NOW)
        self.assertEqual(self.aws.writes, [])

    def test_sched_role_iam_preflight_is_checked_and_fail_closed(self):
        original = self.aws.__call__
        def deny_role(region, *args):
            if args[:2] == ("iam", "get-role"):
                return {"Role": {"Arn": "arn:aws:iam::401233741942:role/wrong", "AssumeRolePolicyDocument": {}}}
            return original(region, *args)
        with mock.patch.object(self.aws, "__call__", side_effect=deny_role):
            # Special method lookup bypasses instance __call__ patch; wrap
            # an explicit callable to exercise the same AWS read boundary.
            with self.assertRaisesRegex(ValueError, "Unexpected expiration role"):
                h.gpu_preview(lambda *args: deny_role(*args), now=NOW)
        self.assertEqual(self.aws.writes, [])

    def test_expiration_trust_or_extra_managed_policy_blocks_eip_move(self):
        actual = self.aws.__call__
        def broken_trust(region, *args):
            if args[:2] == ("iam", "get-role"):
                valid = actual(region, *args)
                valid["Role"]["AssumeRolePolicyDocument"]["Statement"][0]["Condition"][
                    "StringEquals"]["aws:SourceAccount"] = "000000000000"
                return valid
            return actual(region, *args)
        with self.assertRaisesRegex(ValueError, "scheduler trust mismatch"):
            h.gpu_preview(broken_trust, now=NOW)
        self.assertEqual(self.aws.writes, [])
        def extra_policy(region, *args):
            if args[:2] == ("iam", "list-attached-role-policies"):
                return {"AttachedPolicies": [{"PolicyArn": "arn:aws:iam::aws:policy/AdministratorAccess"}]}
            return actual(region, *args)
        with self.assertRaisesRegex(ValueError, "Unexpected managed policies"):
            h.gpu_preview(extra_policy, now=NOW)
        self.assertEqual(self.aws.writes, [])

    def test_weakened_gpu_guard_retry_policy_refused(self):
        self.aws.schedules["studio-pr65-i-" + GPU]["Target"]["RetryPolicy"]["MaximumRetryAttempts"] = 0
        with self.assertRaisesRegex(ValueError, "guard mismatch"):
            h.gpu_preview(self.aws, now=NOW)
        self.assertEqual(self.aws.writes, [])

    def test_expiration_cannot_be_extended_outside_original_pr65_bounds(self):
        self.aws.schedules["studio-pr65-i-" + GPU]["ScheduleExpression"] = "at(2026-11-02T12:00:00)"
        with self.assertRaisesRegex(ValueError, "supervised guard bound"):
            h.gpu_preview(self.aws, now=NOW)
        self.aws.schedules["studio-pr65-i-" + GPU]["ScheduleExpression"] = "at(2026-11-02T08:00:00)"
        self.aws.schedules["studio-pr65-v-" + VOLUME]["ScheduleExpression"] = "at(2026-11-02T10:30:00)"
        with self.assertRaisesRegex(ValueError, "timing outside"):
            h.gpu_preview(self.aws, now=NOW)
        self.assertEqual(self.aws.writes, [])

    def test_gpu_expiration_volume_ownership_guard_reused(self):
        self.aws.volume["Tags"][0]["Value"] = "wrong-stack"
        with self.assertRaisesRegex(ValueError, "ownership"):
            h.gpu_preview(self.aws, now=NOW)
        self.assertEqual(self.aws.writes, [])

    def test_exact_gpu_iam_review_no_wildcards_and_cpu_untouched(self):
        a = h.review_gpu_iam(GPU)
        self.assertEqual(a["new_inline_policy"]["Statement"][0]["Action"], "sts:AssumeRole")
        self.assertEqual(a["new_inline_policy"]["Statement"][0]["Condition"]["StringEquals"]["aws:userid"],
                         h.BASE_ROLE_ID + ":" + GPU)
        self.assertEqual(a["additional_trust_statement"]["Condition"]["StringEquals"]["aws:userid"],
                         h.BASE_ROLE_ID + ":" + GPU)
        self.assertEqual(a["certificate_arn"], h.CERT_ARN)
        with self.assertRaises(ValueError):
            h.review_gpu_iam("*")
        self.assertEqual(self.aws.calls, [])

    def test_gpu_readiness_requires_exact_ssm_success_marker(self):
        h.check_gpu_tls(self.aws, GPU, sleep=lambda _: None)
        self.assertEqual(self.aws.writes[0][:2], ("ssm", "send-command"))
        self.aws = FakeAWS()
        self.aws.ssm_out = "READY_OTHER\n"
        with self.assertRaisesRegex(ValueError, "safe marker"):
            h.check_gpu_tls(self.aws, GPU, sleep=lambda _: None)
        self.aws = FakeAWS()
        self.aws.ssm_status = "Failed"
        with self.assertRaisesRegex(RuntimeError, "GPU DCV"):
            h.check_gpu_tls(self.aws, GPU, sleep=lambda _: None)

    def test_ssm_invocation_eventual_consistency_retry_is_bounded(self):
        attempts = 0
        def call(region, *args):
            nonlocal attempts
            if args[:2] == ("ssm", "get-command-invocation"):
                attempts += 1
                if attempts <= 2:
                    raise subprocess.CalledProcessError(254, args,
                        stderr="An error occurred (InvocationDoesNotExist) while retrieving SSM command")
            return self.aws(region, *args)
        h.check_gpu_tls(call, GPU, sleep=lambda _: None)
        self.assertEqual(attempts, 3)

    def test_ssm_access_denied_is_not_treated_as_eventual_consistency(self):
        attempts = 0
        def call(region, *args):
            nonlocal attempts
            if args[:2] == ("ssm", "get-command-invocation"):
                attempts += 1
                raise subprocess.CalledProcessError(254, args, stderr="AccessDenied")
            return self.aws(region, *args)
        with self.assertRaises(subprocess.CalledProcessError):
            h.check_gpu_tls(call, GPU, sleep=lambda _: None)
        self.assertEqual(attempts, 1)

    def test_simulated_live_handoff_with_scoped_rollback_protection(self):
        with mock.patch.object(h, "LIVE_HANDOFF_ENABLED", True):
            result = h.handoff_gpu(self.aws, lambda host, ip: self.assertEqual(
                (host, ip), (h.HOST, h.EIP_IP)), now=NOW, clock=lambda: NOW, allow_live=True,
                verify_guest=lambda aws, iid: self.assertEqual(iid, GPU))
        self.assertEqual(result["owner"], GPU)
        self.assertEqual(self.aws.eip["InstanceId"], GPU)
        self.assertEqual(h._tags(self.aws.eip)["StudioDcvInstanceId"], GPU)
        self.assertFalse(any(x[0] == "route53" or x[0] == "iam" for x in self.aws.writes))
        self.assertFalse(any("delete-schedule" in x for x in self.aws.calls))

    def test_failed_public_tls_rolls_back_to_original_cpu(self):
        with mock.patch.object(h, "LIVE_HANDOFF_ENABLED", True):
            with self.assertRaisesRegex(RuntimeError, "restored to CPU"):
                h.handoff_gpu(self.aws, lambda *_: (_ for _ in ()).throw(
                    ValueError("public cert invalid")), now=NOW, clock=lambda: NOW, allow_live=True,
                    verify_guest=lambda *_: None)
        self.assertEqual(self.aws.eip["InstanceId"], h.CPU_ID)
        self.assertEqual(h._tags(self.aws.eip)["StudioDcvInstanceId"], h.CPU_ID)

    def test_disassociation_refusal_keeps_original_cpu_owner(self):
        self.aws.fail_on = "disassociate-address"
        with mock.patch.object(h, "LIVE_HANDOFF_ENABLED", True):
            with self.assertRaisesRegex(RuntimeError, "restored to CPU"):
                h.handoff_gpu(self.aws, lambda *_: None, now=NOW, clock=lambda: NOW, allow_live=True,
                              verify_guest=lambda *_: None)
        self.assertEqual(self.aws.eip["InstanceId"], h.CPU_ID)
        self.assertEqual(self.aws.eip["AssociationId"], "eipassoc-previous")

    def test_partial_disassociation_error_restores_cpu(self):
        self.aws.fail_on = "disassociate-address-after"
        with mock.patch.object(h, "LIVE_HANDOFF_ENABLED", True):
            with self.assertRaisesRegex(RuntimeError, "restored to CPU"):
                h.handoff_gpu(self.aws, lambda *_: None, now=NOW, clock=lambda: NOW, allow_live=True,
                              verify_guest=lambda *_: None)
        self.assertEqual(self.aws.eip["InstanceId"], h.CPU_ID)
        self.assertEqual(h._tags(self.aws.eip)["StudioDcvInstanceId"], h.CPU_ID)

    def test_failed_associate_rolls_back_and_leaves_dns_intact(self):
        self.aws.fail_on = "associate-address"
        with mock.patch.object(h, "LIVE_HANDOFF_ENABLED", True):
            with self.assertRaisesRegex(RuntimeError, "restoration unverified"):
                h.handoff_gpu(self.aws, lambda *_: None, now=NOW, clock=lambda: NOW, allow_live=True,
                              verify_guest=lambda *_: None)
        self.assertEqual(self.aws.records[0]["ResourceRecords"], [{"Value": h.EIP_IP}])

    def test_failed_guest_preflight_cannot_disassociate_eip(self):
        with mock.patch.object(h, "LIVE_HANDOFF_ENABLED", True):
            with self.assertRaisesRegex(RuntimeError, "unprepared"):
                h.handoff_gpu(self.aws, lambda *_: None, now=NOW, clock=lambda: NOW, allow_live=True,
                              verify_guest=lambda *_: (_ for _ in ()).throw(RuntimeError("unprepared")))
        self.assertEqual(self.aws.eip["InstanceId"], h.CPU_ID)
        self.assertEqual(self.aws.writes, [])

    def test_cpu_return_denies_running_gpu_and_wrong_owner(self):
        self.aws.eip["InstanceId"] = GPU
        for t in self.aws.eip["Tags"]:
            if t["Key"] == "StudioDcvInstanceId":
                t["Value"] = GPU
        with self.assertRaisesRegex(ValueError, "GPU must be stopped"):
            h.cpu_return_preview(self.aws, GPU)
        self.aws.gpu["State"]["Name"] = "stopped"
        p = h.cpu_return_preview(self.aws, GPU)
        self.assertEqual(p["to"], h.CPU_ID)
        self.assertTrue(p["cpu_will_remain_stopped"])
        with self.assertRaisesRegex(ValueError, "does not own"):
            h.cpu_return_preview(self.aws, "i-abcdef01234567890")

    def test_cpu_return_after_gpu_termination_and_eip_detachment(self):
        self.aws.eip.pop("InstanceId")
        self.aws.eip.pop("AssociationId")
        self.aws.eip.pop("NetworkInterfaceId")
        for t in self.aws.eip["Tags"]:
            if t["Key"] == "StudioDcvInstanceId":
                t["Value"] = GPU
        p = h.cpu_return_preview(self.aws, GPU)
        self.assertIsNone(p["association"])
        with mock.patch.object(h, "LIVE_HANDOFF_ENABLED", True):
            result = h.restore_cpu(self.aws, GPU, allow_live=True)
        self.assertEqual(result["owner"], h.CPU_ID)
        self.assertEqual(self.aws.eip["InstanceId"], h.CPU_ID)
        self.assertEqual(h._tags(self.aws.eip)["StudioDcvInstanceId"], h.CPU_ID)

    def test_cli_handoff_requires_execute_before_aws_or_gpu_mutation(self):
        with mock.patch.object(cli, "aws") as aws, redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit):
                cli.main(["hostname-handoff"])
            with self.assertRaises(SystemExit):
                cli.main(["hostname-return", "--gpu-instance-id", GPU])
        aws.assert_not_called()

    def test_cli_execute_dispatches_guarded_handoff_and_return(self):
        with (mock.patch.object(cli, "aws") as aws,
              mock.patch.object(h, "handoff_gpu", return_value={"owner": GPU}) as handoff,
              mock.patch.object(h, "restore_cpu", return_value={"owner": h.CPU_ID}) as restore,
              redirect_stdout(io.StringIO()) as output):
            cli.main(["hostname-handoff", "--execute"])
            self.assertEqual(json.loads(output.getvalue())["owner"], GPU)
            output.seek(0)
            output.truncate(0)
            cli.main(["hostname-return", "--execute", "--gpu-instance-id", GPU])
            self.assertEqual(json.loads(output.getvalue())["owner"], h.CPU_ID)
        handoff.assert_called_once_with(aws, cli.verified_dcv_hostname, allow_live=True)
        restore.assert_called_once_with(aws, GPU, allow_live=True)
        aws.assert_not_called()

    def test_cli_iam_dry_run_has_no_aws_calls(self):
        with mock.patch.object(cli, "aws") as aws, redirect_stdout(io.StringIO()) as out:
            cli.main(["hostname-iam", "--gpu-instance-id", GPU])
        self.assertEqual(json.loads(out.getvalue())["gpu_instance_id"], GPU)
        aws.assert_not_called()

    def test_cli_plan_read_only_calls_only_approved_aws_queries(self):
        # The fake PR65 expiration is anchored at NOW; never depend on wall time.
        original = h.gpu_preview
        with (mock.patch.object(cli, "aws", side_effect=self.aws),
              mock.patch.object(h, "gpu_preview", wraps=lambda aws: original(aws, now=NOW)),
              redirect_stdout(io.StringIO()) as out):
            cli.main(["hostname-plan"])
        self.assertEqual(json.loads(out.getvalue())["to"], GPU)
        self.assertEqual(self.aws.writes, [])


    def test_gpu_tls_probe_checks_expiry_and_hostname_separately(self):
        fake = FakeAWS()
        h.check_gpu_tls(fake, GPU, sleep=lambda *_: None)
        sent = next(call for call in fake.calls if call[1:3] == ("ssm", "send-command"))
        payload = json.loads(sent[sent.index("--parameters") + 1])
        commands = payload["commands"]
        expiry = [c for c in commands if "openssl x509" in c and "-checkend 172800" in c]
        hostname = [c for c in commands if "openssl x509" in c and "-checkhost" in c]
        self.assertEqual(len(expiry), 1)
        self.assertEqual(len(hostname), 1)
        self.assertNotIn("-checkhost", expiry[0])
        self.assertNotIn("-checkend", hostname[0])
        self.assertIn("grep -q 'does match'", hostname[0])



if __name__ == "__main__":
    unittest.main()
