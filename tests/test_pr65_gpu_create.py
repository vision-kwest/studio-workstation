"""Offline-only tests for bounded PR65 GPU CreateStack guard integration."""
import contextlib
import datetime as dt
import io
import unittest
from unittest.mock import patch

from runtime import pr65_gpu_create as flow
from runtime import pr65_one_shot_guard as guard
from scripts import studio_workstation_cli as cli

STACK = "studio-gpu-cli-review1"
TOKEN = "unique-review-token-123"
IID = "i-0123456789abcdef0"
VID = "vol-0123456789abcdef0"
ARN = "arn:aws:cloudformation:us-east-1:401233741942:stack/" + STACK + "/uniqueid"
TAGS = [{"Key": "StudioStack", "Value": STACK}, {"Key": "StudioCleanupToken", "Value": TOKEN}]
NOW = dt.datetime(2026, 11, 1, 18, tzinfo=dt.timezone.utc)
REGION = "us-east-1"
PARAMS = ["ImageId=ami-027c98f85b155c2ac", "InstanceType=g4dn.xlarge",
          "DiskSizeGiB=100", "ClientCidr=192.0.2.42/32"]


class FakeAWS:
    def __init__(self):
        self.calls = []
        self.schedules = {}
        self.instance = {"InstanceId": IID, "State": {"Name": "running"},
                         "Tags": TAGS, "BlockDeviceMappings": [{"DeviceName": "/dev/sda1",
                            "Ebs": {"VolumeId": VID, "DeleteOnTermination": False}}]}
        self.volume = {"VolumeId": VID, "State": "in-use", "Tags": TAGS,
                       "Attachments": [{"VolumeId": VID, "InstanceId": IID, "Device": "/dev/sda1"}]}
        self.stack = {"StackName": STACK, "StackId": ARN, "StackStatus": "CREATE_IN_PROGRESS", "Tags": TAGS}
        self.seen_instances = 0
        self.status = "CREATE_IN_PROGRESS"
        self.schedule_failure = False
        self.group_state = "ACTIVE"
        self.role_policy_names = [flow.POLICY]
        self.bad_trust = False
        self.no_volume = False
        self.no_instance = False
        self.wrong_volume_tags = False
        self.wrong_instance_tags = False
        self.wrong_stack_tags = False
        self.terminated = []
        self.deleted = []

    def __call__(self, region, *arguments):
        self.calls.append((region, *arguments))
        service, op, *rest = arguments
        if service == "scheduler" and op == "get-schedule-group":
            return {"Name": guard.GROUP, "State": self.group_state}
        if service == "iam" and op == "get-role":
            return {"Role": {"Arn": guard.ROLE, "AssumeRolePolicyDocument": {
                "Statement": [{"Effect": "Allow", "Action": "sts:AssumeRole",
                               "Principal": {"Service": "scheduler.amazonaws.com"},
                               "Condition": {
                                   "ArnEquals": {"aws:SourceArn":
                                       ("arn:aws:scheduler:us-east-1:401233741942:"
                                        "schedule-group/" + guard.GROUP)},
                                   "StringEquals": {"aws:SourceAccount":
                                       "wrong" if self.bad_trust else "401233741942"}}}]}}}
        if service == "iam" and op == "get-role-policy":
            return {"PolicyDocument": {"Statement": [
                {"Effect": "Allow", "Action": "ec2:TerminateInstances",
                 "Resource": "arn:aws:ec2:us-east-1:401233741942:instance/*",
                 "Condition": {"StringEquals": {
                     "ec2:ResourceTag/StudioStack": STACK,
                     "ec2:ResourceTag/StudioCleanupToken": TOKEN}}},
                {"Effect": "Allow", "Action": "ec2:DeleteVolume",
                 "Resource": "arn:aws:ec2:us-east-1:401233741942:volume/*",
                 "Condition": {"StringEquals": {
                     "ec2:ResourceTag/StudioStack": STACK,
                     "ec2:ResourceTag/StudioCleanupToken": TOKEN}}}]}}
        if service == "iam" and op == "list-role-policies":
            return {"PolicyNames": self.role_policy_names}
        if service == "iam" and op == "list-attached-role-policies":
            return {"AttachedPolicies": []}
        if service == "cloudformation" and op == "create-stack":
            assert "--disable-rollback" in arguments
            assert "ParameterKey=ImageId,ParameterValue=ami-027c98f85b155c2ac" in arguments
            return {"StackId": ARN}
        if service == "cloudformation" and op == "describe-stacks":
            s = dict(self.stack, StackStatus=self.status)
            if self.wrong_stack_tags:
                s["Tags"] = []
            return {"Stacks": [s]}
        if service == "cloudformation" and op == "list-stack-resources":
            if self.no_instance:
                return {"StackResourceSummaries": []}
            return {"StackResourceSummaries": [
                {"LogicalResourceId": "Workstation", "ResourceType": "AWS::EC2::Instance",
                 "PhysicalResourceId": IID}]}
        if service == "ec2" and op == "describe-instances":
            self.seen_instances += 1
            inst = dict(self.instance)
            if self.no_volume:
                inst["BlockDeviceMappings"] = []
            if self.wrong_instance_tags:
                inst["Tags"] = []
            return {"Reservations": [{"Instances": [inst]}]}
        if service == "ec2" and op == "describe-volumes":
            vol = dict(self.volume)
            if self.wrong_volume_tags:
                vol["Tags"] = []
            return {"Volumes": [vol]}
        if service == "scheduler" and op == "create-schedule":
            if self.schedule_failure:
                raise PermissionError("Scheduler rejected request")
            spec = __import__("json").loads(rest[rest.index("--cli-input-json") + 1])
            self.schedules[spec["Name"]] = spec
            return {"ScheduleArn": "arn:aws:scheduler:us-east-1:401233741942:schedule/default/" + spec["Name"]}
        if service == "scheduler" and op == "get-schedule":
            return self.schedules[rest[rest.index("--name") + 1]]
        if service == "ec2" and op == "terminate-instances":
            self.terminated.extend(rest[rest.index("--instance-ids") + 1:])
            return {"TerminatingInstances": []}
        if service == "cloudformation" and op == "delete-stack":
            self.deleted.append(rest[rest.index("--stack-name") + 1])
            return {}
        raise AssertionError("Unexpected AWS call: " + str(arguments))


def fake_clock(*values):
    values = iter(values)
    return lambda: next(values)


class GuardedCreateTests(unittest.TestCase):
    def launch(self, fake, **kwargs):
        return flow.create_guarded(fake, REGION, STACK, TOKEN,
                                   "/tmp/reviewed-template.json", PARAMS,
                                   clock=kwargs.pop("clock", fake_clock(0, 0)),
                                   sleep=kwargs.pop("sleep", lambda seconds: None),
                                   utc_now=lambda: NOW, **kwargs)

    def test_success_creates_async_then_arms_two_schedules(self):
        f = FakeAWS()
        result = self.launch(f)
        self.assertTrue(result["guard"]["guard_armed"])
        self.assertEqual(set(result["guard"]["schedules"]), set(f.schedules))
        self.assertEqual(len(f.schedules), 2)
        self.assertEqual(result["guard"]["volume_id"], VID)
        self.assertEqual(result["stack_id"], ARN)
        self.assertTrue(result["manual_ebs_audit_required"])
        self.assertEqual(f.deleted, [])
        self.assertEqual(f.terminated, [])
        self.assertTrue(any(c[1:3] == ("cloudformation", "create-stack") for c in f.calls))
        self.assertFalse(any(c[1:3] == ("cloudformation", "deploy") for c in f.calls))

    def test_role_trust_mismatch_prevents_stack_creation(self):
        f = FakeAWS()
        f.bad_trust = True
        with self.assertRaisesRegex(ValueError, "trust"):
            self.launch(f)
        self.assertFalse(any(c[1:3] == ("cloudformation", "create-stack") for c in f.calls))

    def test_missing_guard_role_permissions_prevents_stack_creation(self):
        f = FakeAWS()
        f.role_policy_names = []
        with self.assertRaisesRegex(ValueError, "inline policies"):
            self.launch(f)
        self.assertFalse(any(c[1:3] == ("cloudformation", "create-stack") for c in f.calls))

    def test_mismatched_volume_tags_fail_and_request_stack_delete(self):
        f = FakeAWS()
        f.wrong_volume_tags = True
        with self.assertRaisesRegex(RuntimeError, "manual"):
            self.launch(f)
        self.assertEqual(f.deleted, [ARN])
        self.assertIn(IID, f.terminated)
        self.assertFalse(f.schedules)

    def test_mismatched_instance_tags_fail_closed(self):
        f = FakeAWS()
        f.wrong_instance_tags = True
        with self.assertRaisesRegex(RuntimeError, "manual"):
            self.launch(f)
        self.assertEqual(f.deleted, [ARN])
        self.assertFalse(f.schedules)
        self.assertEqual(f.terminated, [])  # never terminate an unverified instance

    def test_stack_ownership_mismatch_fails_closed(self):
        f = FakeAWS()
        f.wrong_stack_tags = True
        with self.assertRaisesRegex(RuntimeError, "stack ownership not verified"):
            self.launch(f)
        self.assertEqual(f.deleted, [])  # do not delete a stack with unverified tags
        self.assertFalse(f.schedules)

    def test_stack_failure_while_creating_requests_delete(self):
        f = FakeAWS()
        f.status = "CREATE_FAILED"
        with self.assertRaisesRegex(RuntimeError, "manual"):
            self.launch(f)
        self.assertEqual(f.deleted, [ARN])
        self.assertFalse(f.schedules)

    def test_no_resource_discovered_before_deadline_fails_closed(self):
        f = FakeAWS()
        f.no_instance = True
        with self.assertRaisesRegex(RuntimeError, "manual"):
            self.launch(f, clock=fake_clock(0, 0, 121))
        self.assertEqual(f.deleted, [ARN])
        self.assertEqual(f.terminated, [])
        self.assertFalse(f.schedules)

    def test_root_ebs_never_appears_terminates_instance(self):
        f = FakeAWS()
        f.no_volume = True
        with self.assertRaisesRegex(RuntimeError, "manual"):
            self.launch(f, clock=fake_clock(0, 0, 121))
        self.assertEqual(f.deleted, [ARN])
        self.assertIn(IID, f.terminated)

    def test_scheduler_failure_requests_termination_and_stack_delete(self):
        f = FakeAWS()
        f.schedule_failure = True
        with self.assertRaisesRegex(RuntimeError, "manual"):
            self.launch(f)
        self.assertIn(IID, f.terminated)
        self.assertEqual(f.deleted, [ARN])
        self.assertFalse(f.schedules)

    def test_missing_active_schedule_group_does_not_create_ec2(self):
        f = FakeAWS()
        f.group_state = "DELETING"
        with self.assertRaisesRegex(ValueError, "ACTIVE"):
            self.launch(f)
        self.assertFalse(any(c[1:3] == ("cloudformation", "create-stack") for c in f.calls))

    def test_unguarded_gpu_yes_denied_before_any_aws(self):
        with patch.object(cli, "run") as run, patch.object(cli, "aws") as aws:
            with self.assertRaises(SystemExit), contextlib.redirect_stdout(io.StringIO()):
                cli.main(["create", "--stack", STACK, "--profile", "gpu",
                          "--client-cidr", "192.0.2.42/32", "--cleanup-token", TOKEN,
                          "--yes", "--confirm-stack", STACK])
            run.assert_not_called()
            aws.assert_not_called()

    def test_gpu_yes_without_token_denied_before_any_aws(self):
        with patch.object(cli, "run") as run, patch.object(cli, "aws") as aws:
            with self.assertRaises(SystemExit), contextlib.redirect_stdout(io.StringIO()):
                cli.main(["create", "--stack", STACK, "--profile", "gpu", "--yes",
                          "--confirm-stack", STACK])
            run.assert_not_called()
            aws.assert_not_called()


if __name__ == "__main__":
    unittest.main()
