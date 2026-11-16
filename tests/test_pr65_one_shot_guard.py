"""The two schedules protect one workstation, never an independent disk lifecycle."""
import copy
import datetime as dt
import json
import unittest
from unittest.mock import Mock

from runtime import pr65_one_shot_guard as guard, pr65_gpu_create as creator
from workstation_fake import AWS, NOW, h, inventory


class GuardTests(unittest.TestCase):
    def setUp(self):
        self.aws=AWS(); self.selected=self.aws.desktop()
        self.proposal=guard.status(self.aws,self.selected,now=NOW)

    def test_stack_and_instance_targets_have_no_volume_deadline(self):
        p=self.proposal
        self.assertEqual(json.loads(p['termination']['Target']['Input']),{'StackName':self.selected['stack_id']})
        self.assertEqual(json.loads(p['fallback']['Target']['Input']),{'InstanceIds':[self.selected['instance']]})
        self.assertNotIn('volume',json.dumps(p).lower())
        self.assertEqual(dt.datetime.fromisoformat(p['terminate_at']),NOW+dt.timedelta(minutes=150))
        self.assertEqual(p['fallback']['ScheduleExpression'],'at(2026-11-16T20:35:00)')

    def test_all_local_disks_must_delete_on_termination(self):
        for mutation in ('missing','retain-root','retain-second','wrong-stack','wrong-token','wrong-arn','duplicate-tag'):
            vm=copy.deepcopy(self.selected['vm'])
            if mutation=='missing':vm['BlockDeviceMappings']=[]
            elif mutation=='retain-root':vm['BlockDeviceMappings'][0]['Ebs']['DeleteOnTermination']=False
            elif mutation=='retain-second':vm['BlockDeviceMappings'].append({'Ebs':{'DeleteOnTermination':False}})
            elif mutation=='duplicate-tag':vm['Tags'].append(vm['Tags'][0])
            else:
                key={'wrong-stack':'StudioStack','wrong-token':'StudioCleanupToken','wrong-arn':'aws:cloudformation:stack-id'}[mutation]
                next(t for t in vm['Tags'] if t['Key']==key)['Value']='foreign'
            with self.subTest(mutation=mutation),self.assertRaises(ValueError):
                guard.verify_ownership(vm,self.selected['stack'],self.selected['token'],self.selected['stack_id'])

    def test_rejects_invalid_expiration_before_mutations(self):
        for minutes in (0,59,241,True,100.5,'150'):
            with self.subTest(minutes=minutes),self.assertRaises(ValueError):guard.validate_durations(minutes)
        for minutes in (60,150,240):guard.validate_durations(minutes)

    def test_wrong_or_missing_schedule_cannot_authorize_restart(self):
        original=copy.deepcopy(self.aws.schedules)
        for kind in ('termination','fallback'):
            name=self.proposal[kind]['Name']
            for field,value in [('State','DISABLED'),('ActionAfterCompletion','NONE'),('GroupName','foreign'),
                                ('FlexibleTimeWindow',{'Mode':'FLEXIBLE','MaximumWindowInMinutes':10}),
                                ('ScheduleExpressionTimezone','US/Pacific')]:
                self.aws.schedules=copy.deepcopy(original);self.aws.schedules[name][field]=value
                with self.subTest(kind=kind,field=field),self.assertRaises((RuntimeError,ValueError)):
                    guard.status(self.aws,self.selected,now=NOW,headroom=80)
            for field,value in [('RoleArn','foreign'),('Arn','foreign'),('Input','{}'),('RetryPolicy',{})]:
                self.aws.schedules=copy.deepcopy(original);self.aws.schedules[name]['Target'][field]=value
                with self.subTest(kind=kind,field=field),self.assertRaises((RuntimeError,ValueError)):
                    guard.status(self.aws,self.selected,now=NOW,headroom=80)
            self.aws.schedules=copy.deepcopy(original);del self.aws.schedules[name]
            with self.assertRaises(Exception):guard.status(self.aws,self.selected,now=NOW,headroom=80)

    def test_expired_near_deadline_and_exact_restart_boundary(self):
        for elapsed,allowed in ((70,True),(71,False),(151,False)):
            with self.subTest(elapsed=elapsed):
                if allowed:guard.status(self.aws,self.selected,now=NOW+dt.timedelta(minutes=elapsed),headroom=80)
                else:
                    with self.assertRaisesRegex(ValueError,'remaining-time'):
                        guard.status(self.aws,self.selected,now=NOW+dt.timedelta(minutes=elapsed),headroom=80)
        self.assertEqual(guard.status(self.aws,self.selected,now=NOW+dt.timedelta(minutes=151))['status'],'expired')

    def test_plan_tampering_rejected_before_schedule_write(self):
        for key in ('termination','fallback'):
            p=copy.deepcopy(self.proposal);p[key]['Target']['Input']='{}'
            scheduler=Mock();ec2=Mock()
            with self.assertRaises(ValueError):guard.arm(scheduler,ec2,p)
            scheduler.create_schedule.assert_not_called();ec2.terminate_instances.assert_not_called()

    def test_arming_failure_terminates_only_owned_instance_and_reports_double_failure(self):
        for double in (False,True):
            scheduler=Mock();scheduler.create_schedule.side_effect=RuntimeError('injected')
            ec2=Mock()
            if double:ec2.terminate_instances.side_effect=RuntimeError('denied')
            with self.subTest(double=double),self.assertRaisesRegex(RuntimeError,'Guard'):
                guard.arm(scheduler,ec2,self.proposal)
            ec2.terminate_instances.assert_called_once_with(InstanceIds=[self.selected['instance']])

    def test_wrong_safety_association_does_not_pass_for_other_gpu(self):
        other=self.aws.desktop('gpu-b')
        for field in ('token','safety_stack','stack_id'):
            forged=dict(self.selected);forged[field]=other[field]
            with self.subTest(field=field),self.assertRaises(ValueError):guard.status(self.aws,forged,now=NOW)

    def test_extra_target_or_schedule_settings_fail_closed(self):
        for key,value in [('KmsKeyArn','foreign'),('StartDate','tomorrow'),('Target',dict(self.proposal['termination']['Target'],DeadLetterConfig={'Arn':'foreign'}))]:
            self.aws.schedules[self.proposal['termination']['Name']]=dict(self.proposal['termination'],**{key:value})
            with self.subTest(key=key),self.assertRaises(RuntimeError):guard.status(self.aws,self.selected,now=NOW)
