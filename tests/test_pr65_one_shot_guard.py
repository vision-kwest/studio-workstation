"""Focused offline tests only; no AWS calls."""
import datetime as dt
import unittest
from copy import deepcopy
from unittest.mock import Mock
from runtime.pr65_one_shot_guard import plan, arm, TERMINATE, DELETE_VOLUME, GROUP, ROLE

STACK = "studio-gpu-cli-review1"
TOKEN = "unique-review-token-123"
IID = "i-0123456789abcdef0"
VID = "vol-0123456789abcdef0"
TAGS = [{"Key": "StudioStack", "Value": STACK}, {"Key": "StudioCleanupToken", "Value": TOKEN}]
NOW = dt.datetime(2026, 11, 1, 18, 0, tzinfo=dt.timezone.utc)


def resources():
    instance = {"InstanceId": IID, "Tags": deepcopy(TAGS), "State": {"Name": "running"},
                "BlockDeviceMappings": [{"DeviceName": "/dev/sda1", "Ebs": {
                    "VolumeId": VID, "DeleteOnTermination": False}}]}
    volume = {"VolumeId": VID, "Tags": deepcopy(TAGS), "State": "in-use",
              "Attachments": [{"InstanceId": IID, "VolumeId": VID, "Device": "/dev/sda1"}]}
    return instance, volume


class OneShotGuardTests(unittest.TestCase):
    def proposal(self):
        return plan(*resources(), STACK, TOKEN, NOW)

    def test_two_one_time_targets_from_existing_pattern(self):
        p = self.proposal()
        self.assertEqual(p["termination"]["Target"]["Arn"], TERMINATE)
        self.assertEqual(p["volume_cleanup"]["Target"]["Arn"], DELETE_VOLUME)
        self.assertEqual(p["termination"]["GroupName"], GROUP)
        self.assertEqual(p["termination"]["Target"]["RoleArn"], ROLE)
        self.assertEqual(p["termination"]["ActionAfterCompletion"], "DELETE")
        self.assertEqual(p["volume_cleanup"]["Target"]["Input"], '{"VolumeId":"' + VID + '"}')
        self.assertEqual(p["termination"]["ScheduleExpression"], "at(2026-11-01T20:30:00)")
        self.assertEqual(p["volume_cleanup"]["ScheduleExpression"], "at(2026-11-01T21:15:00)")

    def test_retained_root_volume_required(self):
        instance, vol = resources()
        instance["BlockDeviceMappings"][0]["Ebs"]["DeleteOnTermination"] = True
        with self.assertRaisesRegex(ValueError, "retained"):
            plan(instance, vol, STACK, TOKEN, NOW)

    def test_root_volume_id_must_match_attachment(self):
        instance, vol = resources()
        vol["Attachments"][0]["InstanceId"] = "i-00000000000000000"
        with self.assertRaisesRegex(ValueError, "attachment"):
            plan(instance, vol, STACK, TOKEN, NOW)

    def test_bad_instance_tags_rejected(self):
        instance, vol = resources()
        instance["Tags"][1]["Value"] = "wrong-token"
        with self.assertRaisesRegex(ValueError, "ownership"):
            plan(instance, vol, STACK, TOKEN, NOW)

    def test_bad_ebs_tags_rejected(self):
        instance, vol = resources()
        vol["Tags"][1]["Value"] = "wrong-token"
        with self.assertRaisesRegex(ValueError, "ownership"):
            plan(instance, vol, STACK, TOKEN, NOW)

    def test_duplicate_scope_tags_rejected(self):
        instance, vol = resources()
        vol["Tags"].append(vol["Tags"][0])
        with self.assertRaisesRegex(ValueError, "Duplicate"):
            plan(instance, vol, STACK, TOKEN, NOW)

    def test_deadline_bounded_and_utc(self):
        with self.assertRaisesRegex(ValueError, "bounds"):
            plan(*resources(), STACK, TOKEN, NOW, minutes=500)
        with self.assertRaisesRegex(ValueError, "UTC"):
            plan(*resources(), STACK, TOKEN, NOW.replace(tzinfo=None))

    def test_both_schedules_verified(self):
        proposal = self.proposal()
        scheduler = Mock()
        expected = {proposal[k]["Name"]: proposal[k] for k in ("termination", "volume_cleanup")}
        scheduler.get_schedule.side_effect = lambda Name, GroupName: expected[Name]
        ec2 = Mock()
        result = arm(scheduler, ec2, proposal)
        self.assertTrue(result["guard_armed"])
        self.assertEqual(scheduler.create_schedule.call_count, 2)
        self.assertEqual(scheduler.get_schedule.call_count, 2)
        ec2.terminate_instances.assert_not_called()

    def test_mutated_plan_cannot_target_another_instance_or_volume(self):
        p = self.proposal()
        p["volume_cleanup"]["Target"]["Input"] = '{"VolumeId":"vol-00000000000000000"}'
        scheduler, ec2 = Mock(), Mock()
        with self.assertRaisesRegex(ValueError, "target or input"):
            arm(scheduler, ec2, p)
        scheduler.create_schedule.assert_not_called()
        ec2.terminate_instances.assert_not_called()

    def test_readback_wrong_target_requests_emergency_termination(self):
        p = self.proposal()
        scheduler = Mock()
        wrong = deepcopy(p["termination"])
        wrong["Target"]["Arn"] = "arn:aws:scheduler:::aws-sdk:ec2:startInstances"
        scheduler.get_schedule.return_value = wrong
        ec2 = Mock()
        with self.assertRaisesRegex(RuntimeError, "retained EBS"):
            arm(scheduler, ec2, p)
        ec2.terminate_instances.assert_called_once_with(InstanceIds=[IID])
        self.assertEqual(scheduler.create_schedule.call_count, 1)

    def test_ebs_schedule_creation_failure_requests_emergency_termination(self):
        p = self.proposal()
        scheduler = Mock()
        scheduler.create_schedule.side_effect = [None, PermissionError("permission denied")]
        scheduler.get_schedule.return_value = p["termination"]
        ec2 = Mock()
        with self.assertRaisesRegex(RuntimeError, "retained EBS"):
            arm(scheduler, ec2, p)
        ec2.terminate_instances.assert_called_once_with(InstanceIds=[IID])

    def test_emergency_termination_failure_is_not_hidden(self):
        p = self.proposal()
        scheduler = Mock()
        scheduler.create_schedule.side_effect = RuntimeError("scheduler down")
        ec2 = Mock()
        ec2.terminate_instances.side_effect = RuntimeError("ec2 down")
        with self.assertRaisesRegex(RuntimeError, "termination failed"):
            arm(scheduler, ec2, p)


if __name__ == "__main__":
    unittest.main()
