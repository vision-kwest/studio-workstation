"""Automatic stop preserves the workstation; explicit destroy owns deletion."""
import copy
import datetime as dt
import json
import unittest
from unittest.mock import patch

from runtime import pr65_one_shot_guard as guard
from workstation_fake import AWS, NOW, h, inventory


class GuardTests(unittest.TestCase):
    def setUp(self):
        self.aws = AWS()
        self.selected = self.aws.desktop()
        self.proposal = guard.status(self.aws, self.selected, now=NOW)

    def test_only_the_owned_instance_is_stopped_and_schedule_remains_for_restart(self):
        spec = self.proposal['schedule']
        self.assertEqual(spec['Target']['Arn'], 'arn:aws:scheduler:::aws-sdk:ec2:stopInstances')
        self.assertEqual(json.loads(spec['Target']['Input']), {'InstanceIds': [self.selected['instance']]})
        self.assertEqual(spec['ActionAfterCompletion'], 'NONE')
        self.assertEqual(dt.datetime.fromisoformat(self.proposal['stop_at']), NOW + dt.timedelta(minutes=150))
        disk = copy.deepcopy(self.aws.volumes)
        self.aws.fire_schedule(spec['Name'])
        self.assertEqual(self.aws.vms[self.selected['instance']]['State']['Name'], 'stopped')
        self.assertEqual(self.aws.volumes, disk)
        self.assertEqual(self.aws.stacks[self.selected['stack_id']]['StackStatus'], 'CREATE_COMPLETE')
        self.assertIn(spec['Name'], self.aws.schedules)

    def test_ownership_mismatches_cannot_authorize_a_timer(self):
        for mutation in ('missing-disk', 'wrong-stack', 'wrong-token', 'wrong-arn', 'duplicate-tag'):
            vm = copy.deepcopy(self.selected['vm'])
            if mutation == 'missing-disk':
                vm['BlockDeviceMappings'] = []
            elif mutation == 'duplicate-tag':
                vm['Tags'].append(vm['Tags'][0])
            else:
                key = {'wrong-stack': 'StudioStack', 'wrong-token': 'StudioCleanupToken',
                       'wrong-arn': 'aws:cloudformation:stack-id'}[mutation]
                next(t for t in vm['Tags'] if t['Key'] == key)['Value'] = 'foreign'
            with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                guard.verify_ownership(vm, self.selected['stack'], self.selected['token'], self.selected['stack_id'])

    def test_positive_durations_have_no_acceptance_test_caps(self):
        for minutes in (1, 59, 150, 241, 480, 10000, 525600):
            with self.subTest(minutes=minutes):
                self.assertEqual(guard.deadline(NOW, minutes), NOW + dt.timedelta(minutes=minutes))
        for minutes in (0, -1, True, 100.5, '150', 10**30):
            with self.subTest(minutes=minutes), self.assertRaises(ValueError):
                guard.deadline(NOW, minutes)
        with self.assertRaises(ValueError):
            guard.deadline(NOW.replace(tzinfo=None), 10)

    def test_wrong_or_missing_schedule_is_rejected_without_writes(self):
        self.aws.vms[self.selected['instance']]['State']['Name'] = 'stopped'
        self.selected = inventory.context(self.aws, h.REGION, self.selected['stack'])
        original = copy.deepcopy(self.aws.schedules)
        name = self.proposal['schedule']['Name']
        changes = [('ActionAfterCompletion', 'DELETE'), ('GroupName', 'foreign'),
                   ('FlexibleTimeWindow', {'Mode': 'FLEXIBLE', 'MaximumWindowInMinutes': 10}),
                   ('ScheduleExpressionTimezone', 'US/Pacific'), ('Description', 'not a duration')]
        for field, value in changes:
            self.aws.schedules = copy.deepcopy(original)
            self.aws.schedules[name][field] = value
            self.aws.calls.clear()
            with self.subTest(field=field), self.assertRaises((RuntimeError, ValueError)):
                guard.rearm(self.aws, self.selected, now=NOW)
            self.assertFalse(self.aws.writes)
        for field, value in [('RoleArn', 'foreign'), ('Arn', 'arn:aws:scheduler:::aws-sdk:ec2:terminateInstances'),
                             ('Input', '{}'), ('RetryPolicy', {})]:
            self.aws.schedules = copy.deepcopy(original)
            self.aws.schedules[name]['Target'][field] = value
            self.aws.calls.clear()
            with self.subTest(target=field), self.assertRaises((RuntimeError, ValueError)):
                guard.rearm(self.aws, self.selected, now=NOW)
            self.assertFalse(self.aws.writes)
        self.aws.schedules.clear()
        with self.assertRaises(Exception):
            guard.rearm(self.aws, self.selected, now=NOW)

    def test_expired_and_disabled_timers_are_visible_and_rearmed_with_fresh_duration(self):
        name = self.proposal['schedule']['Name']
        self.assertEqual(guard.status(self.aws, self.selected, now=NOW + dt.timedelta(minutes=151))['status'], 'expired')
        self.aws.schedules[name]['State'] = 'DISABLED'
        self.assertEqual(guard.status(self.aws, self.selected, now=NOW)['status'], 'disabled')
        self.aws.vms[self.selected['instance']]['State']['Name'] = 'stopped'
        self.selected = inventory.context(self.aws, h.REGION, self.selected['stack'])
        later = NOW + dt.timedelta(days=2)
        self.aws.calls.clear()
        guard.rearm(self.aws, self.selected, minutes=720, now=later)
        status = guard.status(self.aws, self.selected, now=later)
        self.assertEqual(status['status'], 'scheduled')
        self.assertEqual(status['duration_minutes'], 720)
        self.assertEqual(dt.datetime.fromisoformat(status['stop_at']), later + dt.timedelta(minutes=720))
        self.assertEqual([c[1] for c in self.aws.writes], ['update-schedule'])
        guard.rearm(self.aws, self.selected, now=later + dt.timedelta(days=1))
        self.assertEqual(guard.status(self.aws, self.selected, now=later)['duration_minutes'], 720)

    def test_wrong_safety_association_does_not_pass_for_other_gpu(self):
        other = self.aws.desktop('gpu-b')
        for field in ('token', 'safety_stack', 'stack_id'):
            forged = dict(self.selected)
            forged[field] = other[field]
            with self.subTest(field=field), self.assertRaises(ValueError):
                guard.status(self.aws, forged, now=NOW)

    def test_extra_target_or_schedule_settings_are_not_silently_reset(self):
        self.aws.vms[self.selected['instance']]['State']['Name'] = 'stopped'
        self.selected = inventory.context(self.aws, h.REGION, self.selected['stack'])
        spec = self.proposal['schedule']
        for key, value in [('KmsKeyArn', 'foreign'), ('StartDate', 'tomorrow'),
                           ('Target', dict(spec['Target'], DeadLetterConfig={'Arn': 'foreign'}))]:
            self.aws.schedules[spec['Name']] = dict(spec, **{key: value})
            self.aws.calls.clear()
            with self.subTest(key=key), self.assertRaises((RuntimeError, ValueError)):
                guard.rearm(self.aws, self.selected, now=NOW)
            self.assertFalse(self.aws.writes)

    def test_legacy_disabled_destructive_timers_are_never_reenabled_implicitly(self):
        selected = self.aws.legacy_desktop()
        self.aws.vms[selected['instance']]['State']['Name'] = 'stopped'
        selected = inventory.context(self.aws, h.REGION, selected['stack'])
        original = copy.deepcopy(self.aws.schedules)
        self.aws.calls.clear()
        self.assertEqual(guard.status(self.aws, selected, now=NOW)['status'], 'legacy')
        with self.assertRaisesRegex(ValueError, '[Ll]egacy'):
            guard.rearm(self.aws, selected, now=NOW)
        self.assertEqual(self.aws.schedules, original)
        self.assertFalse(self.aws.writes)

    def test_stopping_does_not_require_deletion_of_a_retained_ebs_disk(self):
        self.aws.vms[self.selected['instance']]['BlockDeviceMappings'][0]['Ebs']['DeleteOnTermination'] = False
        selected = inventory.context(self.aws, h.REGION, self.selected['stack'])
        spec = guard.status(self.aws, selected, now=NOW)['schedule']
        volumes = copy.deepcopy(self.aws.volumes)
        self.aws.fire_schedule(spec['Name'])
        self.assertEqual(self.aws.volumes, volumes)
        self.assertEqual(self.aws.vms[selected['instance']]['State']['Name'], 'stopped')

    def test_foreign_cloudformation_timer_identity_is_rejected(self):
        resource = next(r for r in self.selected['resources'] if r['LogicalResourceId'] == 'AutoStopSchedule')
        resource['PhysicalResourceId'] = 'studio-stop-foreign'
        self.aws.calls.clear()
        with self.assertRaises(ValueError):
            guard.status(self.aws, self.selected, now=NOW)
        self.assertFalse(self.aws.writes)

    def test_rearm_readback_that_outlasts_short_timer_cannot_authorize_start(self):
        self.aws.vms[self.selected['instance']]['State']['Name'] = 'stopped'
        selected = inventory.context(self.aws, h.REGION, self.selected['stack'])
        clock, reads = [NOW], []
        def slow_readback(service, action, args, result):
            if (service, action) == ('scheduler', 'get-schedule'):
                reads.append(True)
                if len(reads) == 3:
                    clock[0] = NOW + dt.timedelta(minutes=2)
        self.aws.after = slow_readback
        self.aws.calls.clear()
        with patch.object(guard, '_utc', side_effect=lambda value=None: clock[0] if value is None else value):
            with self.assertRaisesRegex(RuntimeError, 'elapsed while rearming'):
                guard.rearm(self.aws, selected, minutes=1)
        self.assertEqual([c[1] for c in self.aws.writes], ['update-schedule'])
        self.assertEqual(self.aws.vms[selected['instance']]['State']['Name'], 'stopped')
