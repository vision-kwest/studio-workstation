"""Only workstation expiration is extended; instance fallback is an internal safeguard."""
import copy
import datetime as dt
import json
import unittest
from runtime import issue68_expiration_extension as extension, pr65_one_shot_guard as guard
from workstation_fake import AWS, NOW, h, inventory, missing


class ExtensionTests(unittest.TestCase):
    def setUp(self):
        self.aws=AWS();self.a=self.aws.desktop();self.b=self.aws.desktop('gpu-b')

    def extend(self,minutes=30):
        return extension.extend(self.aws,self.a['stack'],h.REGION,minutes,now=NOW)

    def test_fallback_first_then_stack_and_other_workstation_unchanged(self):
        before=copy.deepcopy(self.aws.schedules);self.aws.calls=[]
        result=self.extend();self.assertTrue(result['extended'])
        writes=[json.loads(c[c.index('--cli-input-json')+1]) for c in self.aws.calls if c[:2]==('scheduler','update-schedule')]
        self.assertEqual([s['Name'] for s in writes],['studio-fallback-'+self.a['token'],'studio-expiry-'+self.a['token']])
        for name,spec in before.items():
            if self.b['token'] in name:self.assertEqual(self.aws.schedules[name],spec)
        self.assertNotIn('volume',json.dumps(result).lower())

    def test_invalid_limits_ownership_and_deadlines_never_write(self):
        for minutes in (0,181,True,1.5,'30'):
            self.aws.calls=[]
            with self.subTest(minutes=minutes),self.assertRaises(ValueError):self.extend(minutes)
            self.assertFalse(self.aws.writes)
        with self.assertRaises(ValueError):self.extend(180)
        with self.assertRaises(ValueError):extension.extend(self.aws,self.a['stack'],h.REGION,30,now=NOW+dt.timedelta(minutes=140))

    def test_write_failure_before_or_after_update_restores_or_verifies_state(self):
        for target in ('studio-fallback-','studio-expiry-'):
            for after in (False,True):
                with self.subTest(target=target,after=after):
                    aws=AWS();a=aws.desktop();original=copy.deepcopy(aws.schedules);fired=[]
                    def fault(service,action,args,*rest):
                        if action=='update-schedule' and not fired:
                            spec=json.loads(args[args.index('--cli-input-json')+1])
                            if spec['Name'].startswith(target):fired.append(True);raise RuntimeError('injected')
                    if after:aws.after=fault
                    else:aws.before=fault
                    if target=='studio-expiry-' and after:
                        self.assertTrue(extension.extend(aws,a['stack'],h.REGION,30,now=NOW)['recovered_after_error'])
                    else:
                        with self.assertRaisesRegex(RuntimeError,'aborted'):extension.extend(aws,a['stack'],h.REGION,30,now=NOW)
                        self.assertEqual(aws.schedules,original)

    def test_near_expiry_error_explains_cutoff_before_considering_added_minutes(self):
        self.aws.calls=[]
        original=copy.deepcopy(self.aws.schedules)
        for additional in (120,60):
            with self.subTest(additional=additional),self.assertRaises(ValueError) as error:
                extension.extend(self.aws,self.a['stack'],h.REGION,additional,
                                 now=NOW+dt.timedelta(minutes=142,seconds=31))
            message=str(error.exception)
            for detail in ('deadline 2026-11-16 20:30:00 UTC','7.48 minutes remaining',
                           'at least 15 minutes','Latest allowed time: 2026-11-16 20:15:00 UTC'):
                self.assertIn(detail,message)
        self.assertEqual(self.aws.schedules,original)
        self.assertFalse(self.aws.writes)

    def test_external_change_between_scope_check_and_prepare_is_rejected(self):
        reads=[]
        def mutate(service,action,args,result):
            if action=='get-schedule' and args[args.index('--name')+1].startswith('studio-expiry-'):
                reads.append(1)
                if len(reads)==3:
                    result['Target']=copy.deepcopy(result['Target']);result['Target']['Input']='{"StackName":"foreign"}'
        self.aws.after=mutate;self.aws.calls=[]
        with self.assertRaisesRegex(RuntimeError,'changed'):self.extend()
        self.assertFalse(self.aws.writes)

    def test_unexpected_schedule_options_do_not_get_silently_reset(self):
        self.aws.schedules['studio-expiry-'+self.a['token']]['Target']['DeadLetterConfig']={'Arn':'foreign'}
        self.aws.calls=[]
        with self.assertRaises(RuntimeError):self.extend()
        self.assertFalse(self.aws.writes)

    def test_unverified_rollback_does_not_claim_success(self):
        calls=[]
        def fault(service,action,args):
            if action=='update-schedule':
                calls.append(1)
                if len(calls)>=2:raise RuntimeError('injected')
        self.aws.before=fault
        with self.assertRaisesRegex(RuntimeError,'rollback unverified'):self.extend()
