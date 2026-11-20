"""One stop deadline can be extended without test-only caps or destructive targets."""
import copy
import datetime as dt
import json
import unittest
from unittest.mock import patch

from runtime import issue68_expiration_extension as extension, pr65_one_shot_guard as guard
from workstation_fake import AWS, NOW, h


class ExtensionTests(unittest.TestCase):
    def setUp(self):
        self.aws = AWS()
        self.a = self.aws.desktop()
        self.b = self.aws.desktop('gpu-b')
        self.name = 'studio-stop-' + self.a['token']

    def extend(self, minutes=30, now=NOW):
        return extension.extend(self.aws, self.a['stack'], h.REGION, minutes, now=now)

    def test_one_stop_deadline_changes_and_other_workstation_is_untouched(self):
        before = copy.deepcopy(self.aws.schedules)
        self.aws.calls.clear()
        result = self.extend(480)
        self.assertTrue(result['extended'])
        writes = [json.loads(c[c.index('--cli-input-json') + 1]) for c in self.aws.calls
                  if c[:2] == ('scheduler', 'update-schedule')]
        self.assertEqual(len(writes), 1)
        self.assertEqual(writes[0]['Name'], self.name)
        self.assertEqual(writes[0]['Target'], before[self.name]['Target'])
        self.assertEqual(self.aws.schedules['studio-stop-' + self.b['token']], before['studio-stop-' + self.b['token']])
        self.assertEqual(dt.datetime.fromisoformat(result['proposed_stop_utc']), NOW + dt.timedelta(minutes=630))
        self.assertFalse(any(c[1] in ('terminate-instances', 'delete-stack', 'delete-volume') for c in self.aws.calls))

    def test_last_minute_extension_accepts_requested_positive_amounts(self):
        original = copy.deepcopy(self.aws.schedules)
        near = NOW + dt.timedelta(minutes=149, seconds=30)
        for additional in (1, 60, 120, 1440):
            with self.subTest(additional=additional):
                self.aws.schedules = copy.deepcopy(original)
                result = self.extend(additional, near)
                self.assertTrue(result['extended'])
                self.assertEqual(dt.datetime.fromisoformat(result['proposed_stop_utc']), NOW + dt.timedelta(minutes=150 + additional))

    def test_invalid_inputs_and_unrepresentable_dates_never_write(self):
        self.aws.calls.clear()
        for minutes in (0, -1, True, 2.5, '60', 10**30):
            with self.subTest(minutes=minutes), self.assertRaises(ValueError):
                self.extend(minutes)
        self.assertFalse(self.aws.writes)

    def test_transport_failure_is_reconciled_without_blind_rollback(self):
        for after in (False, True):
            with self.subTest(applied=after):
                aws = AWS()
                selected = aws.desktop()
                original = copy.deepcopy(aws.schedules)
                fired = []
                def fail(service, action, args, *rest):
                    if (service, action) == ('scheduler', 'update-schedule') and not fired:
                        fired.append(True)
                        raise RuntimeError('injected transport failure')
                if after:
                    aws.after = fail
                else:
                    aws.before = fail
                aws.calls.clear()
                if after:
                    result = extension.extend(aws, selected['stack'], h.REGION, 30, now=NOW)
                    self.assertTrue(result['extended'])
                    self.assertTrue(result['recovered_after_error'])
                else:
                    with self.assertRaises(RuntimeError):
                        extension.extend(aws, selected['stack'], h.REGION, 30, now=NOW)
                    self.assertEqual(aws.schedules, original)
                self.assertEqual(len(aws.writes), 1)

    def test_changed_schedule_and_unsupported_fields_fail_before_update(self):
        for mutation in ('foreign-target', 'extra-setting'):
            original = copy.deepcopy(self.aws.schedules)
            if mutation == 'foreign-target':
                self.aws.schedules[self.name]['Target']['Input'] = '{"InstanceIds":["i-fffffffffffffffff"]}'
            else:
                self.aws.schedules[self.name]['Target']['DeadLetterConfig'] = {'Arn': 'foreign'}
            self.aws.calls.clear()
            with self.subTest(mutation=mutation), self.assertRaises((ValueError, RuntimeError)):
                self.extend()
            self.assertFalse(self.aws.writes)
            self.aws.schedules = original

    def test_unreadable_post_write_state_is_not_reported_as_success_or_rolled_back(self):
        applied = []
        def before(service, action, args):
            if (service, action) == ('scheduler', 'get-schedule') and applied:
                raise RuntimeError('readback unavailable')
        def after(service, action, args, result):
            if (service, action) == ('scheduler', 'update-schedule'):
                applied.append(True)
        self.aws.before, self.aws.after = before, after
        self.aws.calls.clear()
        with self.assertRaises(RuntimeError):
            self.extend()
        self.assertEqual(len(self.aws.writes), 1)

    def test_already_dispatched_stop_is_reported_and_does_not_restart_or_delete(self):
        disk = copy.deepcopy(self.aws.volumes)
        def stop_race(service, action, args, result):
            if (service, action) == ('scheduler', 'update-schedule'):
                self.aws.vms[self.a['instance']]['State']['Name'] = 'stopping'
        self.aws.after = stop_race
        self.aws.calls.clear()
        result = self.extend(now=NOW + dt.timedelta(minutes=149, seconds=59))
        self.assertEqual(result['state'], 'stopping')
        self.assertIn('warning', result)
        self.assertEqual(self.aws.volumes, disk)
        self.assertFalse(any(c[1] in ('start-instances', 'terminate-instances', 'delete-stack') for c in self.aws.calls))

    def test_legacy_disabled_timers_are_not_reenabled_by_extension(self):
        legacy = self.aws.legacy_desktop()
        original = copy.deepcopy(self.aws.schedules)
        self.aws.calls.clear()
        with self.assertRaisesRegex(ValueError, '[Ll]egacy'):
            extension.extend(self.aws, legacy['stack'], h.REGION, 60, now=NOW)
        self.assertEqual(self.aws.schedules, original)
        self.assertFalse(self.aws.writes)

    def test_disabled_stop_schedule_is_not_silently_reenabled(self):
        self.aws.schedules[self.name]['State'] = 'DISABLED'
        before = copy.deepcopy(self.aws.schedules)
        self.aws.calls.clear()
        with self.assertRaises(ValueError):
            self.extend()
        self.assertEqual(self.aws.schedules, before)
        self.assertFalse(self.aws.writes)

    def test_read_latency_cannot_write_a_deadline_that_has_already_passed(self):
        clock = [NOW + dt.timedelta(minutes=149, seconds=50)]
        reads = []
        def advance(service, action, args, result):
            if (service, action) == ('scheduler', 'get-schedule'):
                reads.append(True)
                if len(reads) == 2:
                    clock[0] = NOW + dt.timedelta(minutes=152)
        self.aws.after = advance
        before = copy.deepcopy(self.aws.schedules)
        self.aws.calls.clear()
        with patch.object(guard, '_utc', side_effect=lambda value=None: clock[0] if value is None else value):
            with self.assertRaisesRegex(ValueError, 'deadline has already passed'):
                extension.extend(self.aws, self.a['stack'], h.REGION, 1)
        self.assertEqual(self.aws.schedules, before)
        self.assertFalse(self.aws.writes)

    def test_ec2_read_failure_after_verified_update_reports_what_changed(self):
        updated = []
        def after(service, action, args, result):
            if (service, action) == ('scheduler', 'update-schedule'):
                updated.append(True)
        def before(service, action, args):
            if (service, action) == ('ec2', 'describe-instances') and updated:
                raise RuntimeError('EC2 inspection unavailable')
        self.aws.before, self.aws.after = before, after
        self.aws.calls.clear()
        with self.assertRaisesRegex(RuntimeError, 'updated and verified, but EC2 state is unverified'):
            self.extend()
        self.assertEqual(self.aws.schedules[self.name]['ScheduleExpression'], 'at(2026-11-16T21:00:00)')
        self.assertEqual(len(self.aws.writes), 1)

    def test_stop_during_final_inspection_does_not_report_a_useful_future_deadline(self):
        clock, reads = [NOW + dt.timedelta(minutes=149, seconds=50)], []
        def slow_read(service, action, args, result):
            if (service, action) == ('ec2', 'describe-instances'):
                reads.append(True)
                if len(reads) == 2:
                    clock[0] = NOW + dt.timedelta(minutes=152)
        self.aws.after = slow_read
        self.aws.calls.clear()
        with patch.object(guard, '_utc', side_effect=lambda value=None: clock[0] if value is None else value):
            with self.assertRaisesRegex(RuntimeError, 'elapsed during verification'):
                extension.extend(self.aws, self.a['stack'], h.REGION, 1)
        self.assertEqual([c[1] for c in self.aws.writes], ['update-schedule'])
