"""Issue #68: entirely mocked expiration extension and partial-failure contracts."""
import contextlib
import copy
import datetime as dt
import io
import json
import unittest
from unittest import mock

from runtime import issue68_expiration_extension as extension
from runtime import issue43_hostname_handoff as handoff
from scripts import studio_workstation_cli as cli
from test_issue43_hostname_handoff import FakeAWS, GPU, VOLUME, NOW


class SchedulerAWS(FakeAWS):
    def __init__(self):
        super().__init__()
        self.fail_target = None
        self.fail_after_apply = None
        self.fail_rollback = False
        self.failed = set()
        self.update_names = []

    def __call__(self, region, *args):
        if args[:2] == ("scheduler", "update-schedule"):
            self.calls.append((region, *args))
            assert region == handoff.REGION
            spec = json.loads(args[args.index("--cli-input-json") + 1])
            name = spec["Name"]
            self.update_names.append(name)
            self.writes.append(("scheduler", "update-schedule", name))
            if self.fail_rollback and len(self.update_names) >= 3:
                raise PermissionError("EBS rollback denied")
            if name == self.fail_target and name not in self.failed:
                self.failed.add(name)
                raise PermissionError("Scheduler update rejected")
            self.schedules[name] = copy.deepcopy(spec)
            if name == self.fail_after_apply and name not in self.failed:
                self.failed.add(name)
                raise RuntimeError("API reported error after update was applied")
            return {"ScheduleArn": name}
        return super().__call__(region, *args)


class ExpirationExtensionTests(unittest.TestCase):
    def setUp(self):
        self.aws = SchedulerAWS()
        self.term_name = "studio-pr65-i-" + GPU
        self.ebs_name = "studio-pr65-v-" + VOLUME
        self.before_term = copy.deepcopy(self.aws.schedules[self.term_name])
        self.before_ebs = copy.deepcopy(self.aws.schedules[self.ebs_name])
        self.before_eip = copy.deepcopy(self.aws.eip)
        self.before_dns = copy.deepcopy(self.aws.records)

    def execute(self, minutes=30, **kwargs):
        return extension.extend(self.aws, handoff.GPU_STACK, handoff.REGION,
                                minutes, now=kwargs.get("now", NOW))

    def assert_unchanged(self):
        self.assertEqual(self.aws.schedules[self.term_name], self.before_term)
        self.assertEqual(self.aws.schedules[self.ebs_name], self.before_ebs)
        self.assertEqual(self.aws.eip, self.before_eip)
        self.assertEqual(self.aws.records, self.before_dns)

    def test_extension_reports_exact_original_and_new_deadlines(self):
        p = self.execute()
        self.assertEqual(p["instance_id"], GPU)
        self.assertEqual(p["root_volume_id"], VOLUME)
        self.assertEqual(p["previous_termination_utc"], "2026-11-02T09:00:00+00:00")
        self.assertEqual(p["proposed_termination_utc"], "2026-11-02T09:30:00+00:00")
        self.assertEqual(p["proposed_ebs_cleanup_utc"], "2026-11-02T10:30:00+00:00")
        self.assertTrue(p["extended"])
        self.assertNotIn("requires_execute", p)
        self.assertEqual(self.aws.update_names, [self.ebs_name, self.term_name])

    def test_execute_moves_ebs_first_then_ec2_and_preserves_exact_scope(self):
        p = self.execute(60)
        self.assertTrue(p["extended"])
        self.assertFalse(p["recovered_after_error"])
        self.assertEqual(self.aws.update_names, [self.ebs_name, self.term_name])
        self.assertEqual(self.aws.schedules[self.term_name]["ScheduleExpression"],
                         "at(2026-11-02T10:00:00)")
        self.assertEqual(self.aws.schedules[self.ebs_name]["ScheduleExpression"],
                         "at(2026-11-02T11:00:00)")
        for name, before in ((self.term_name, self.before_term),
                             (self.ebs_name, self.before_ebs)):
            after = self.aws.schedules[name]
            for field in ("Name", "GroupName", "State", "ActionAfterCompletion",
                          "FlexibleTimeWindow", "ScheduleExpressionTimezone", "Target"):
                self.assertEqual(after[field], before[field])
            self.assertEqual(after["Target"]["RoleArn"], handoff.guard.ROLE)
        self.assertEqual(self.aws.eip, self.before_eip)
        self.assertEqual(self.aws.records, self.before_dns)

    def test_ebs_first_update_error_keeps_both_original_deadlines(self):
        self.aws.fail_target = self.ebs_name
        with self.assertRaisesRegex(RuntimeError, "original termination"):
            self.execute()
        self.assertEqual(self.aws.update_names, [self.ebs_name])
        self.assert_unchanged()

    def test_termination_update_failure_rolls_back_ebs(self):
        self.aws.fail_target = self.term_name
        with self.assertRaisesRegex(RuntimeError, "original termination"):
            self.execute(60)
        self.assertEqual(self.aws.update_names,
                         [self.ebs_name, self.term_name, self.ebs_name])
        self.assert_unchanged()

    def test_ebs_update_error_after_write_rolls_back(self):
        self.aws.fail_after_apply = self.ebs_name
        with self.assertRaisesRegex(RuntimeError, "original termination"):
            self.execute(60)
        self.assertEqual(self.aws.update_names, [self.ebs_name, self.ebs_name])
        self.assert_unchanged()

    def test_error_after_both_updates_uses_independent_readback(self):
        self.aws.fail_after_apply = self.term_name
        p = self.execute(60)
        self.assertTrue(p["extended"])
        self.assertTrue(p["recovered_after_error"])
        self.assertEqual(self.aws.update_names, [self.ebs_name, self.term_name])
        self.assertEqual(self.aws.schedules[self.term_name]["ScheduleExpression"],
                         "at(2026-11-02T10:00:00)")

    def test_failed_rollback_reports_uncertain_ebs_state(self):
        self.aws.fail_target = self.term_name
        self.aws.fail_rollback = True
        with self.assertRaisesRegex(RuntimeError, "rollback unverified"):
            self.execute(60)
        self.assertEqual(self.aws.schedules[self.term_name], self.before_term)
        self.assertNotEqual(self.aws.schedules[self.ebs_name], self.before_ebs)

    def test_invalid_scope_and_extension_bounds_write_nothing(self):
        for minutes in (0, -1, 181, 1000, True, 1.5):
            with self.subTest(minutes=minutes):
                with self.assertRaises(ValueError):
                    self.execute(minutes)
        with self.assertRaisesRegex(ValueError, "exact PR65"):
            extension.extend(self.aws, handoff.CPU_STACK, handoff.REGION, 30, now=NOW)
        with self.assertRaisesRegex(ValueError, "region"):
            extension.extend(self.aws, handoff.GPU_STACK, "us-west-2", 30, now=NOW)
        with self.assertRaisesRegex(ValueError, "80–240"):
            self.execute(121)  # 120 current + 121 exceeds 240-minute bound
        self.assertEqual(self.aws.writes, [])
        self.assert_unchanged()

    def test_near_or_expired_termination_refuses_write(self):
        with self.assertRaisesRegex(ValueError, "at least 15 minutes"):
            self.execute(90, now=NOW + dt.timedelta(minutes=111))
        with self.assertRaisesRegex(ValueError, "at least 15 minutes"):
            self.execute(90, now=NOW + dt.timedelta(minutes=125))
        self.assertFalse(self.aws.writes)

    def test_wrong_owner_or_schedule_role_fails_before_write(self):
        self.aws.volume["Tags"][1]["Value"] = "foreign"
        with self.assertRaisesRegex(ValueError, "ownership"):
            self.execute()
        self.aws.volume["Tags"][1]["Value"] = handoff.GPU_TOKEN
        self.aws.schedules[self.ebs_name]["Target"]["RoleArn"] = "arn:aws:iam::foreign:role/other"
        with self.assertRaisesRegex(ValueError, "guard mismatch"):
            self.execute()
        self.assertEqual(self.aws.writes, [])

    def test_extra_schedule_target_or_optional_control_refuses_to_overwrite(self):
        self.aws.schedules[self.term_name]["Target"]["DeadLetterConfig"] = {"Arn": "foreign"}
        with self.assertRaisesRegex(ValueError, "Unexpected expiration target"):
            self.execute()
        del self.aws.schedules[self.term_name]["Target"]["DeadLetterConfig"]
        self.aws.schedules[self.term_name]["KmsKeyArn"] = "arn:aws:kms:us-east-1:401233741942:key/changed"
        with self.assertRaisesRegex(ValueError, "custom schedule options"):
            self.execute()
        self.assertFalse(self.aws.writes)

    def test_missing_schedule_or_iam_role_prevents_updates(self):
        self.aws.schedules.pop(self.term_name)
        with self.assertRaises(KeyError):
            self.execute()
        self.aws.schedules[self.term_name] = copy.deepcopy(self.before_term)
        self.aws.bad_trust = True
        # Mock IAM role trust in the existing FakeAWS adapter.
        original = self.aws.__call__
        def reject_iam(region, *args):
            if args[:2] == ("iam", "get-role"):
                raise PermissionError("IAM AccessDenied")
            return original(region, *args)
        with self.assertRaisesRegex(PermissionError, "IAM AccessDenied"):
            extension.extend(reject_iam, handoff.GPU_STACK, handoff.REGION, 30, now=NOW)
        self.assertFalse(self.aws.writes)

    def test_max_240_minute_term_and_120_minute_ebs_delay_still_valid(self):
        self.aws.schedules[self.term_name]["ScheduleExpression"] = "at(2026-11-02T11:00:00)"
        self.aws.schedules[self.ebs_name]["ScheduleExpression"] = "at(2026-11-02T13:00:00)"
        status = cli.gpu_expiration_status(handoff.GPU_STACK, GPU,
                                           handoff.REGION, manager=self.aws, now=NOW)
        self.assertEqual(status["remaining_minutes"], 240)
        self.assertEqual(status["ebs_cleanup_utc"], "2026-11-02T13:00:00+00:00")
        self.assertFalse(self.aws.writes)

    def test_cli_additional_minutes_directly_authorizes_extension(self):
        from unittest.mock import patch
        args = ["extend-expiration", "--stack", handoff.GPU_STACK,
                "--additional-minutes", "30"]
        with (patch.object(extension, "extend", return_value={"extended": True}) as execute,
              contextlib.redirect_stdout(io.StringIO()) as output):
            cli.main(args)
        self.assertIn('"extended": true', output.getvalue())
        execute.assert_called_once_with(
            cli.aws, handoff.GPU_STACK, handoff.REGION, 30)

    def test_removed_execute_option_is_rejected(self):
        from unittest.mock import patch
        with (patch.object(extension, "extend") as execute,
              contextlib.redirect_stderr(io.StringIO())):
            with self.assertRaises(SystemExit):
                cli.main(["extend-expiration", "--stack", handoff.GPU_STACK,
                          "--additional-minutes", "30", "--execute"])
        execute.assert_not_called()

    def test_cli_rejects_cpu_invalid_minutes_and_wrong_region_before_aws(self):
        for args in (
            ["extend-expiration", "--additional-minutes", "30"],
            ["extend-expiration", "--stack", handoff.GPU_STACK],
            ["extend-expiration", "--stack", handoff.GPU_STACK,
             "--additional-minutes", "0"],
            ["extend-expiration", "--stack", handoff.GPU_STACK,
             "--additional-minutes", "181"],
            ["extend-expiration", "--stack", handoff.GPU_STACK,
             "--additional-minutes", "30", "--region", "us-west-2"],
        ):
            with (self.subTest(args=args),
                  mock.patch.object(cli, "aws") as aws,
                  contextlib.redirect_stderr(io.StringIO())):
                with self.assertRaises(SystemExit):
                    cli.main(args)
                aws.assert_not_called()


if __name__ == "__main__":
    unittest.main()
