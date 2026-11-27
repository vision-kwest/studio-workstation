"""Exercise actual Automation handoff code and lifecycle boundaries without AWS."""
import copy
import datetime as dt
import json
import os
import stat
import subprocess
import tempfile
import unittest
from unittest import mock
from types import SimpleNamespace
from pathlib import Path

from runtime import readiness_lifetime as lifetime, readiness_lifetime_handler as handler
from runtime import pr65_one_shot_guard as guard, issue68_expiration_extension as extension
from workstation_fake import AWS, NOW, h, inventory, missing


class ReadinessLifetimeTests(unittest.TestCase):
    def setUp(self):
        self.aws = AWS()
        self.selected = self.aws.desktop(minutes=35)
        self.name = 'studio-stop-' + self.selected['token']
        self.schedule = self.aws.schedules[self.name]
        self.payload = {'ScheduleName': self.name, 'InstanceId': self.selected['instance'],
                        'StackId': self.selected['stack_id'], 'CleanupToken': self.selected['token'],
                        'StopRole': self.schedule['Target']['RoleArn'], 'Operation': 'handoff',
                        'SetupDescription': self.schedule['Description'],
                        'SetupDeadline': self.schedule['ScheduleExpression'][3:-1]}
        self.clock = [NOW + dt.timedelta(minutes=30)]
        clock = self.clock
        class Frozen(dt.datetime):
            @classmethod
            def now(cls, tz=None):
                return clock[0]
        self.addCleanup(mock.patch.stopall)
        mock.patch.object(handler, 'dt', SimpleNamespace(datetime=Frozen, timedelta=dt.timedelta, timezone=dt.timezone)).start()
        self.scheduler = mock.Mock()
        self.scheduler.get_schedule.side_effect = lambda **kw: copy.deepcopy(self.aws.schedules[kw['Name']])
        def update(**kw):
            kw.pop('ClientToken')
            self.aws.schedules[kw['Name']] = copy.deepcopy(kw)
            return {}
        self.scheduler.update_schedule.side_effect = update
        self.ec2 = mock.Mock()
        self.ec2.describe_instances.side_effect = lambda **kw: {'Reservations': [{'Instances': [self.aws.vms[kw['InstanceIds'][0]]]}]}
        mock.patch.object(handler.boto3, 'client', side_effect=lambda name: {'scheduler': self.scheduler, 'ec2': self.ec2}[name]).start()

    def test_duration_begins_at_readiness_and_duplicate_execution_never_resets_it(self):
        self.assertEqual(guard.status(self.aws, self.selected, now=NOW)['phase'], 'setup')
        self.assertEqual(handler.handle(self.payload, None), {'Done': True})
        ready = copy.deepcopy(self.aws.schedules[self.name])
        self.assertEqual(ready['ScheduleExpression'], 'at(2026-11-16T19:05:00)')
        self.assertIn('Studio ready ', ready['Description'])
        self.clock[0] += dt.timedelta(minutes=10)
        self.assertEqual(handler.handle(self.payload, None), {'Done': True})
        self.assertEqual(self.aws.schedules[self.name], ready)
        self.scheduler.update_schedule.assert_called_once()

    def test_stale_expired_stopping_or_foreign_requests_never_change_the_timer(self):
        original = copy.deepcopy(self.schedule)
        for failure in ('generation', 'manual-extension', 'expired', 'stopping', 'identity', 'target'):
            with self.subTest(failure=failure):
                self.aws.schedules[self.name] = copy.deepcopy(original)
                self.clock[0] = NOW + dt.timedelta(minutes=30)
                vm = self.aws.vms[self.selected['instance']]
                vm['State']['Name'] = 'running'
                payload = dict(self.payload)
                if failure == 'generation':payload['SetupDescription'] = payload['SetupDescription'].replace(self.selected['token'], 'f' * 36)
                if failure == 'manual-extension':self.aws.schedules[self.name]['ScheduleExpression'] = 'at(2026-11-16T21:00:00)'
                if failure == 'expired':self.clock[0] = NOW + dt.timedelta(minutes=60)
                if failure == 'stopping':vm['State']['Name'] = 'stopping'
                if failure == 'identity':payload['StackId'] = 'foreign'
                if failure == 'target':self.aws.schedules[self.name]['Target']['Input'] = '{"InstanceIds":["i-foreign"]}'
                before = copy.deepcopy(self.aws.schedules)
                with self.assertRaises(ValueError):handler.handle(payload, None)
                self.assertEqual(self.aws.schedules, before)
        self.scheduler.update_schedule.assert_not_called()

    def test_accepted_update_with_lost_response_is_reconciled_by_readback(self):
        original = self.scheduler.update_schedule.side_effect
        def lost(**kw):
            original(**kw)
            raise TimeoutError('response lost')
        self.scheduler.update_schedule.side_effect = lost
        self.assertEqual(handler.handle(self.payload, None), {'Done': True})
        self.scheduler.update_schedule.assert_called_once()

    def test_start_submits_durable_work_before_compute_and_preserves_data(self):
        self.aws.vms[self.selected['instance']]['State']['Name'] = 'stopped'
        selected = inventory.context(self.aws, h.REGION, self.selected['stack'])
        self.aws.calls.clear()
        before = copy.deepcopy(self.aws.volumes)
        guard.rearm(self.aws, selected, minutes=35, now=NOW)
        self.assertEqual(len(self.aws.automations), 1)
        self.assertEqual(self.aws.vms[selected['instance']]['State']['Name'], 'stopped')
        self.assertEqual(self.aws.volumes, before)
        current = guard.status(self.aws, selected, now=NOW)
        self.assertEqual((current['phase'], current['duration_minutes']), ('setup', 35))
        execution = next(iter(self.aws.automations.values()))
        self.assertEqual(execution['Parameters']['SetupDescription'], [current['schedule']['Description']])
        self.assertNotEqual(current['schedule']['Description'], self.payload['SetupDescription'])

    def test_manual_extension_cancels_pending_handoff_and_preserves_new_deadline(self):
        lifetime.submit(self.aws, self.selected, self.schedule)
        result = extension.extend(self.aws, self.selected['stack'], h.REGION, 30, now=NOW)
        self.assertTrue(result['extended'])
        self.assertEqual(next(iter(self.aws.automations.values()))['AutomationExecutionStatus'], 'Cancelled')
        self.assertIn('Studio manual ', self.aws.schedules[self.name]['Description'])
        with self.assertRaisesRegex(ValueError, 'deadline changed'):handler.handle(self.payload, None)
        self.scheduler.update_schedule.assert_not_called()

    def test_slow_automation_submission_cannot_authorize_compute_after_expiry(self):
        self.aws.vms[self.selected['instance']]['State']['Name'] = 'stopped'
        selected = inventory.context(self.aws, h.REGION, self.selected['stack'])
        clock = [NOW]
        def slow(service, action, args, result):
            if action == 'start-automation-execution':clock[0] += dt.timedelta(minutes=61)
        self.aws.after = slow
        with mock.patch.object(guard, '_utc', side_effect=lambda value=None: clock[0] if value is None else value):
            with self.assertRaisesRegex(RuntimeError, 'elapsed during Automation submission'):
                guard.rearm(self.aws, selected)
        self.assertEqual(self.aws.vms[selected['instance']]['State']['Name'], 'stopped')

    def test_unconfirmed_cancellation_prevents_manual_timer_write(self):
        lifetime.submit(self.aws, self.selected, self.schedule)
        def no_cancel(service, action, args, result):
            if action == 'stop-automation-execution':
                next(iter(self.aws.automations.values()))['AutomationExecutionStatus'] = 'Cancelling'
        self.aws.after = no_cancel
        before = copy.deepcopy(self.aws.schedules)
        with self.assertRaisesRegex(RuntimeError, 'cancellation is unverified'):
            lifetime.cancel_pending(self.aws, self.selected, sleep=lambda _: None)
        self.assertEqual(self.aws.schedules, before)

    def test_handoff_finishing_during_cancellation_is_reread_before_extension(self):
        lifetime.submit(self.aws, self.selected, self.schedule)
        def finish(service, action, args):
            if action == 'stop-automation-execution':
                handler.handle(self.payload, None)
                next(iter(self.aws.automations.values()))['AutomationExecutionStatus'] = 'Success'
                raise missing('InvalidAutomationStatusUpdateException', action)
        self.aws.before = finish
        result = extension.extend(self.aws, self.selected['stack'], h.REGION, 30, now=NOW)
        self.assertTrue(result['extended'])
        self.assertEqual(self.aws.schedules[self.name]['ScheduleExpression'], 'at(2026-11-16T19:35:00)')
        self.assertIn('Studio ready ', self.aws.schedules[self.name]['Description'])

    def test_cancellation_permission_failure_does_not_change_the_timer(self):
        lifetime.submit(self.aws, self.selected, self.schedule)
        def denied(service, action, args):
            if action == 'stop-automation-execution':raise missing('AccessDeniedException', action)
        self.aws.before = denied
        before = copy.deepcopy(self.aws.schedules)
        with self.assertRaises(subprocess.CalledProcessError):
            extension.extend(self.aws, self.selected['stack'], h.REGION, 30, now=NOW)
        self.assertEqual(self.aws.schedules, before)

    def test_native_resources_have_no_client_or_managed_node_registration_dependency(self):
        name = 'studio-ready-' + self.selected['token']
        props = next(a for a in self.aws.associations.values() if a['Name'] == name)
        self.assertEqual(props['AutomationTargetParameterName'], 'InstanceId')
        self.assertEqual(props['Targets'], [{'Key': 'ParameterValues', 'Values': [self.selected['instance']]}])
        self.assertNotIn('WaitForSuccessTimeoutSeconds', props)
        document = self.aws.documents[name]['Content']
        steps = {step['name']: step for step in document['mainSteps']}
        loop = steps['awaitReadiness']
        self.assertEqual((loop['action'], loop['inputs']['MaxIterations']), ('aws:loop', 60))
        steps.update({step['name']: step for step in loop['inputs']['Steps']})
        order = [step['name'] for step in loop['inputs']['Steps']]
        self.assertLess(order.index('waitOnline'), order.index('installEfs'))
        self.assertLess(order.index('installEfs'), order.index('checkReady'))
        efs = next(r['PhysicalResourceId'] for r in self.selected['resources']
                   if r['LogicalResourceId'] == 'EfsClientDocument')
        self.assertEqual(steps['installEfs']['inputs'], {
            'DocumentName': efs, 'InstanceIds': ['{{ InstanceId }}'], 'TimeoutSeconds': 30})
        self.assertEqual(steps['installEfs']['onFailure'], 'step:retryDelay')
        self.assertFalse(steps['installEfs']['isCritical'])
        self.assertEqual(steps['checkReady']['action'], 'aws:runCommand')
        self.assertEqual(steps['checkReady']['onFailure'], 'step:retryDelay')
        self.assertNotIn('nextStep', steps['retryDelay'])
        self.assertEqual(steps['handoff']['action'], 'aws:executeScript')
        self.assertEqual(steps['waitOnline']['onFailure'], 'step:retryDelay')
        self.assertEqual(steps['waitRunning']['timeoutSeconds'], 300)
        self.assertEqual(steps['waitOnline']['timeoutSeconds'], 300)
        retry_seconds = int(steps['retryDelay']['inputs']['Duration'][2:-1])
        self.assertEqual(retry_seconds, 60)
        self.assertGreaterEqual(loop['inputs']['MaxIterations'] * retry_seconds, lifetime.SETUP_MINUTES * 60)
        shell = '\n'.join(steps['checkReady']['inputs']['Parameters']['commands'])
        subprocess.run(['bash', '-n'], input=shell, text=True, check=True)
        self.assertIn('SubState --value)" = exited', shell)
        statements = self.aws.policies['studio-ready-' + self.selected['token']]['ReadinessLifetime']['Statement']
        send = next(s for s in statements if s['Action'] == 'ssm:SendCommand')
        self.assertEqual(send['Resource'], [
            'arn:aws:ssm:us-east-1::document/AWS-RunShellScript',
            f'arn:aws:ssm:{h.REGION}:{h.ACCOUNT}:document/{efs}',
            f'arn:aws:ec2:{h.REGION}:{h.ACCOUNT}:instance/{self.selected["instance"]}'])
        for action in ('scheduler:GetSchedule', 'scheduler:UpdateSchedule'):
            granted = next(s for s in statements if action in s['Action'])
            self.assertTrue(granted['Resource'].endswith('/' + self.name))
        self.assertFalse(any('StopInstances' in str(s['Action']) or 'Terminate' in str(s['Action']) for s in statements))

    def test_schedule_permissions_use_exact_group_arn_and_preserve_creation_order(self):
        for profile in ('cpu', 'gpu'):
            with self.subTest(profile=profile):
                resources = lifetime.resources(self.selected['token'], self.payload['StopRole'], self.schedule, profile)
                role = resources['ReadinessLifetimeRole']
                statements = role['Properties']['Policies'][0]['PolicyDocument']['Statement']
                start = next(s for s in statements if s['Action'] == 'ssm:StartAutomationExecution')
                self.assertEqual(start['Resource'], [
                    f'arn:aws:ssm:us-east-1:401233741942:document/studio-ready-{self.selected["token"]}',
                    'arn:aws:ssm:us-east-1:401233741942:automation-execution/*'])
                expected = f'arn:aws:scheduler:us-east-1:401233741942:schedule/studio-task7-pr61/{self.name}'
                for action in ('scheduler:GetSchedule', 'scheduler:UpdateSchedule'):
                    granted = next(s for s in statements if action in s['Action'])
                    self.assertEqual(granted['Resource'], expected)
                self.assertEqual(role['DependsOn'], 'AutoStopSchedule')
                association = resources['ReadinessLifetimeAssociation']['Properties']
                self.assertEqual(association['AssociationDispatchAssumeRole'],
                                 {'Fn::GetAtt': ['ReadinessLifetimeRole', 'Arn']})

    def test_old_stacks_keep_explicitly_labelled_legacy_semantics(self):
        self.aws.resources[self.selected['stack_id']] = [r for r in self.selected['resources'] if r['LogicalResourceId'] not in lifetime.RESOURCES]
        self.schedule['Description'] = 'Studio auto-stop after 35 minutes'
        self.aws.vms[self.selected['instance']]['State']['Name'] = 'stopped'
        old = inventory.context(self.aws, h.REGION, self.selected['stack'])
        status = guard.rearm(self.aws, old, now=NOW)
        self.assertEqual(status['stop_at'], (NOW + dt.timedelta(minutes=35)).isoformat())
        self.assertIn('includes startup time', guard.status(self.aws, old, now=NOW)['warning'])
        self.assertFalse(self.aws.automations)

    def test_both_desktop_services_and_a_running_session_are_required(self):
        probes = [line for line in lifetime.checks(False)
                  if 'gdm3' in line or 'dcvserver' in line or line.startswith('dcv describe-session')]
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            # Match systemctl's ANY-active result for a multi-unit query, catching the original defect.
            (root / 'systemctl').write_text('#!/bin/sh\nshift\nfor service do [ "$service" = "$FAILED_SERVICE" ] || exit 0; done\nexit 3\n')
            (root / 'dcv').write_text('#!/bin/sh\nprintf \'{"id":"console","owner":"workstation","status":"%s"}\\n\' "$SESSION_STATUS"\n')
            for name in ('systemctl', 'dcv'):(root / name).chmod(0o755)
            for failed, status in (('', 'running'), ('gdm3', 'running'), ('dcvserver', 'running'), ('', 'stopped'), ('', 'starting')):
                with self.subTest(failed=failed, status=status):
                    result = subprocess.run(['bash', '-eu'], input='\n'.join(probes + ['echo STUDIO_WORKSTATION_READY']),
                        text=True, capture_output=True, env={**os.environ, 'PATH': tmp + ':' + os.environ['PATH'],
                                                            'FAILED_SERVICE': failed, 'SESSION_STATUS': status})
                    self.assertEqual(result.returncode == 0, not failed and status == 'running')
                    self.assertEqual('STUDIO_WORKSTATION_READY' in result.stdout, not failed and status == 'running')

    def test_shared_setup_needs_successful_service_mount_and_readable_launchers(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            shared, home = root / 'mount/shared', root / 'home'
            for path in ('apps', 'models', 'projects', 'share/applications'):
                (shared / path).mkdir(parents=True, exist_ok=True)
            (home / '.local/share').mkdir(parents=True)
            (root / 'studio').symlink_to(shared)
            (home / '.local/share/applications').symlink_to(root / 'studio/share/applications')
            launcher = shared / 'share/applications/example.desktop'
            stub = '''systemctl() { case "$*" in
  *SubState*) echo exited;; *Result*) echo "$SETUP_RESULT";; *ExecMainStatus*) echo 0;; esac; }
findmnt() { test "$MOUNTED" = yes; }
getent() { echo "workstation:x:2000:2000::$TEST_HOME:/bin/bash"; }
runuser() { shift 3; "$@"; }
'''
            for gpu in (False, True):
                checks = lifetime.checks(gpu)
                shared_checks = checks[checks.index("echo 'Checking shared software setup'"):]
                script = stub + '\n'.join(shared_checks)
                script = script.replace('/opt/studio/packages', str(root / 'mount')).replace('/studio/', str(root / 'studio') + '/')
                script = script.replace('readlink /studio)', 'readlink ' + str(root / 'studio') + ')')
                for service, mounted, menu in (('success', 'yes', True), ('failed', 'yes', True),
                                                ('success', 'no', True), ('success', 'yes', False)):
                    with self.subTest(gpu=gpu, service=service, mounted=mounted, menu=menu):
                        launcher.write_text('[Desktop Entry]\nName=Example\n')
                        if not menu:
                            launcher.unlink()
                        result = subprocess.run(['bash', '-eu'], input=script, text=True, capture_output=True,
                            env={**os.environ, 'SETUP_RESULT': service, 'MOUNTED': mounted, 'TEST_HOME': str(home)})
                        ready = service == 'success' and mounted == 'yes' and menu
                        self.assertEqual(result.returncode == 0, ready, result.stderr)
                        self.assertEqual('STUDIO_WORKSTATION_READY' in result.stdout, ready)

    def test_gpu_probe_follows_login_and_rejects_missing_ambiguous_or_broken_graphics(self):
        checks = lifetime.checks(True)
        probes = checks[checks.index("echo 'Checking NVIDIA OpenGL'"):checks.index("echo 'Checking shared software setup'")]
        for case in ('greeter', 'user', 'missing-auth', 'multiple-auth', 'missing-socket',
                     'multiple-sockets', 'software-renderer', 'probe-error'):
            with self.subTest(case=case), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                number, uid = (0, 119) if case == 'greeter' else (1, 2000)
                auth = root / str(uid) / 'gdm/Xauthority'
                auth.parent.mkdir(parents=True)
                if case != 'missing-auth':auth.write_text('test authority')
                xorg = f'/usr/lib/xorg/Xorg vt2 -displayfd 3 -auth {auth} -nolisten tcp'
                if case == 'multiple-auth':xorg += '\n/usr/lib/xorg/Xorg -auth /another/authority'
                for display in ([] if case == 'missing-socket' else [number, 2] if case == 'multiple-sockets' else [number]):
                    # An inert socket inode exercises Bash -S without opening a network socket.
                    os.mknod(root / f'X{display}', stat.S_IFSOCK | 0o600)
                stub = '''ps() { printf '%s\\n' "$XORG_ARGS"; }
glxinfo() {
  test "$DISPLAY" = "$EXPECTED_DISPLAY" || return 1
  test "$XAUTHORITY" = "$EXPECTED_AUTH" || return 1
  test "$__GLX_VENDOR_LIBRARY_NAME" = nvidia || return 1
  printf 'OpenGL renderer string: %s\\n' "$RENDERER"
  return "$GL_EXIT"
}
'''
                script = stub + '\n'.join(probes + ['echo STUDIO_WORKSTATION_READY'])
                result = subprocess.run(['bash', '-eu'], input=script.replace('/tmp/.X11-unix/', tmp + '/'),
                    text=True, capture_output=True, env={**os.environ, 'XORG_ARGS': xorg,
                        'EXPECTED_DISPLAY': f':{number}', 'EXPECTED_AUTH': str(auth),
                        'RENDERER': 'llvmpipe' if case == 'software-renderer' else 'Tesla T4/PCIe/SSE2',
                        'GL_EXIT': '9' if case == 'probe-error' else '0'})
                ready = case in ('greeter', 'user')
                self.assertEqual(result.returncode == 0, ready, result.stderr)
                self.assertEqual('STUDIO_WORKSTATION_READY' in result.stdout, ready)

    def test_every_failed_guest_check_prevents_false_readiness(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            clock = root / 'clock'
            (root / 'date').write_text('#!/bin/sh\ncase " $* " in *" -d "*) echo 3;; *) n=$(cat "$TEST_CLOCK"); echo "$n"; echo $((n+1)) > "$TEST_CLOCK";; esac\n')
            (root / 'sleep').write_text('#!/bin/sh\nexit 0\n')
            for name in ('date', 'sleep'):(root / name).chmod(0o755)
            count = len(lifetime.checks(True)) - 1
            for failed in range(count + 1):
                with self.subTest(failed=failed):
                    clock.write_text('0')
                    probes = ['true'] * count + ['echo STUDIO_WORKSTATION_READY']
                    if failed < count:probes[failed] = 'false'
                    with mock.patch.object(lifetime, 'checks', return_value=probes):
                        result = subprocess.run(['bash', '-s'], input='\n'.join(lifetime.commands(True)), text=True,
                            capture_output=True, env={**os.environ, 'PATH': tmp + ':' + os.environ['PATH'], 'TEST_CLOCK': str(clock)})
                    self.assertEqual(result.returncode == 0, failed == count)
                    self.assertEqual('STUDIO_WORKSTATION_READY' in result.stdout, failed == count)


if __name__ == '__main__':
    unittest.main()
