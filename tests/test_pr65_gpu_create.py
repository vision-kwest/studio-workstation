"""CloudFormation owns the stop timer; scheduler IAM cannot destroy a workstation."""
import copy
import datetime as dt
import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from runtime import pr65_gpu_create as creator, pr65_one_shot_guard as guard
from workstation_fake import AWS, NOW, h, ROOT


class CreationTests(unittest.TestCase):
    def test_two_names_have_independent_native_timers_and_no_shared_role_edits(self):
        aws = AWS()
        a, b = aws.desktop('art'), aws.desktop('ai')
        self.assertNotEqual(a['token'], b['token'])
        self.assertNotEqual(a['safety_stack'], b['safety_stack'])
        for selected in (a, b):
            guard.status(aws, selected, now=NOW)
        self.assertEqual(len(aws.schedules), 2)
        self.assertFalse(any(c[0] == 'iam' and c[1].startswith(('put-', 'update-', 'create-')) for c in aws.calls))
        self.assertFalse(any(c[1] in ('delete-volume', 'terminate-instances') for c in aws.calls))

    def test_cloudformation_owns_instance_and_timer_before_cli_readback(self):
        aws = AWS()
        selected = aws.desktop(minutes=720)
        resources = {r['LogicalResourceId']: r for r in selected['resources']}
        self.assertEqual(resources['AutoStopSchedule']['ResourceType'], 'AWS::Scheduler::Schedule')
        self.assertEqual(resources['AutoStopSchedule']['PhysicalResourceId'], 'studio-stop-' + selected['token'])
        self.assertFalse(any(c[:2] == ('scheduler', 'create-schedule') for c in aws.calls))
        wait = next(i for i, c in enumerate(aws.calls)
                    if c[:2] == ('cloudformation', 'wait') and selected['stack_id'] in c)
        read = next(i for i, c in enumerate(aws.calls) if c[:2] == ('scheduler', 'get-schedule'))
        self.assertLess(wait, read)
        spec = aws.schedules['studio-stop-' + selected['token']]
        self.assertEqual(json.loads(spec['Target']['Input']), {'InstanceIds': [selected['instance']]})
        self.assertEqual(spec['ActionAfterCompletion'], 'NONE')
        self.assertEqual(guard.status(aws, selected, now=NOW)['duration_minutes'], 720)

    def test_invalid_role_prevents_any_billable_stack(self):
        aws = AWS()
        def corrupt(service, action, args, result):
            if (service, action) == ('iam', 'get-role-policy') and args[args.index('--policy-name') + 1] == creator.POLICY:
                result['PolicyDocument']['Statement'][0]['Resource'] = '*'
        aws.after = corrupt
        with self.assertRaisesRegex(ValueError, 'permissions'):
            aws.desktop()
        self.assertFalse(aws.vms)
        self.assertFalse(aws.schedules)

    def test_timer_setup_failure_reports_stop_outcome_and_preserves_local_work(self):
        for failed_action, stop_fails in (('get-schedule', False), ('update-schedule', False), ('get-schedule', True)):
            aws = AWS()
            def fail(service, action, args):
                if (service, action) == ('scheduler', failed_action):
                    raise RuntimeError('injected timer failure')
                if (service, action) == ('ec2', 'stop-instances') and stop_fails:
                    raise RuntimeError('injected stop failure')
            aws.before = fail
            cause = 'Auto-stop update failed; original schedule is unchanged' if failed_action == 'update-schedule' else 'injected timer failure'
            with self.subTest(action=failed_action, stop_fails=stop_fails), self.assertRaisesRegex(RuntimeError, cause) as raised:
                aws.desktop()
            self.assertEqual('stopping is unverified: injected stop failure' in str(raised.exception), stop_fails)
            self.assertEqual('; stop requested' in str(raised.exception), not stop_fails)
            self.assertTrue(aws.vms and aws.volumes and aws.schedules)
            self.assertTrue(all(vm['State']['Name'] == ('running' if stop_fails else 'stopped') for vm in aws.vms.values()))
            stop = next(i for i, c in enumerate(aws.calls) if c[:2] == ('ec2', 'stop-instances'))
            events = next(i for i, c in enumerate(aws.calls) if c[:2] == ('cloudformation', 'describe-stack-events'))
            self.assertLess(stop, events)
            self.assertFalse(any(c[1] in ('delete-stack', 'terminate-instances', 'delete-volume') for c in aws.calls))

    def test_scope_permissions_only_allow_stopping_exact_owned_instances(self):
        template=json.loads((ROOT/'infra/workstation-expiration-role.json').read_text())
        actions=[s['Action'] for p in template['Resources']['ExpirationRole']['Properties']['Policies']
                 for s in p['PolicyDocument']['Statement']]
        self.assertEqual(actions,['ec2:StopInstances'])
        aws=AWS();s=aws.desktop();identity=creator.safety(aws,h.REGION,s['stack'])
        role=identity['role'].rsplit('/',1)[1]
        original=copy.deepcopy(aws.policies[role])
        for change in ('scope','trust','extra-policy','managed-policy','wrong-role','destructive-action'):
            with self.subTest(change=change):
                aws.policies[role]=copy.deepcopy(original)
                if change=='scope':aws.policies[role][creator.POLICY]['Statement'][0]['Condition']['StringEquals']['ec2:ResourceTag/StudioCleanupToken']='foreign'
                if change=='extra-policy':aws.policies[role]['Extra']={}
                if change=='destructive-action':aws.policies[role][creator.POLICY]['Statement'][0]['Action']='ec2:TerminateInstances'
                expected=identity['role']
                def change_read(service,action,args,result):
                    if change=='trust' and (service,action)==('iam','get-role'):
                        result['Role']=copy.deepcopy(result['Role']);result['Role']['AssumeRolePolicyDocument']['Statement'][0]['Condition']['StringEquals']['aws:SourceAccount']='foreign'
                    if change=='managed-policy' and action=='list-attached-role-policies':result['AttachedPolicies']=[{'PolicyArn':'foreign'}]
                    if change=='wrong-role' and action=='get-role':result['Role']={**result['Role'],'Arn':'foreign'}
                aws.after=change_read
                with self.assertRaises(ValueError):creator.preflight(aws,h.REGION,s['stack'],s['token'],role_arn=expected)
        aws.after=None

    def test_recreated_name_receives_new_identity(self):
        aws=AWS();a=aws.desktop();aws(h.REGION,'cloudformation','delete-stack','--stack-name',a['stack_id'])
        aws(h.REGION,'cloudformation','delete-stack','--stack-name',a['safety_stack'])
        b=aws.desktop()
        self.assertNotEqual(a['token'],b['token']);self.assertNotEqual(a['stack_id'],b['stack_id'])

        self.assertNotIn('studio-stop-'+a['token'], aws.schedules)

    def test_duplicate_name_never_updates_existing_stack(self):
        aws=AWS();first=aws.desktop();before=copy.deepcopy(aws.vms)
        with self.assertRaises(Exception):aws.desktop()
        self.assertEqual(aws.vms,before)
        self.assertFalse(any(c[1]=='update-stack' for c in aws.calls))

    def test_extra_instance_is_rejected_before_creating_any_aws_resource(self):
        vm = {'Type': 'AWS::EC2::Instance', 'Properties': {'BlockDeviceMappings': [{'Ebs': {'DeleteOnTermination': True}}]}}
        body = {'Resources': {'Workstation': vm, 'ExtraInstance': copy.deepcopy(vm)}}
        aws = AWS()
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / 'invalid.json'
            path.write_text(json.dumps(body))
            with self.assertRaises(ValueError):
                creator.create_guarded(aws, h.REGION, 'art', path, [], utc_now=lambda: NOW)
        self.assertFalse(aws.calls)

    def test_short_user_timer_begins_after_slow_native_provisioning(self):
        aws, clock, initial = AWS(), [NOW], []
        def provisioning(service, action, args):
            if service != 'cloudformation':
                return
            if action == 'wait':
                record = aws.lookup(args[args.index('--stack-name') + 1])
                safety = any(t == {'Key': 'StudioManaged', 'Value': 'expiry'} for t in record['Tags'])
                clock[0] += dt.timedelta(minutes=12 if safety else 4)
            if action == 'create-stack' and args[args.index('--stack-name') + 1] == 'gpu-a':
                body = json.loads(Path(args[args.index('--template-body') + 1][7:]).read_text())
                initial.append(body['Resources']['AutoStopSchedule']['Properties']['ScheduleExpression'])
        aws.before = provisioning
        selected = aws.desktop(minutes=1, utc_now=lambda: clock[0])
        status = guard.status(aws, selected, now=clock[0])
        self.assertGreater(dt.datetime.fromisoformat(initial[0][3:-1]).replace(tzinfo=dt.timezone.utc), clock[0])
        self.assertEqual(status['duration_minutes'], 1)
        self.assertEqual(dt.datetime.fromisoformat(status['stop_at']), clock[0] + dt.timedelta(minutes=1))
