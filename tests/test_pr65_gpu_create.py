"""Native safety identity and pre-billing schedule contracts, with fault injection."""
import copy
import json
import unittest
from unittest.mock import patch
from runtime import pr65_gpu_create as creator, pr65_one_shot_guard as guard
from workstation_fake import AWS, NOW, h, ROOT, inventory, missing


class CreationTests(unittest.TestCase):
    def test_two_names_have_independent_native_identity_and_no_shared_role_edits(self):
        aws=AWS();a=aws.desktop('art');b=aws.desktop('ai')
        self.assertNotEqual(a['token'],b['token']);self.assertNotEqual(a['safety_stack'],b['safety_stack'])
        for selected in (a,b):guard.status(aws,selected,now=NOW)
        self.assertEqual(len(aws.schedules),4)
        self.assertFalse(any(c[0]=='iam' and c[1].startswith(('put-','update-','create-')) for c in aws.calls))
        self.assertFalse(any(c[1]=='delete-volume' for c in aws.calls))

    def test_primary_is_armed_and_read_back_before_workload_creation(self):
        aws=AWS();selected=aws.desktop();calls=aws.calls
        creates=[i for i,c in enumerate(calls) if c[:2]==('cloudformation','create-stack')]
        scheduled=next(i for i,c in enumerate(calls) if c[:2]==('scheduler','create-schedule'))
        read=next(i for i,c in enumerate(calls) if c[:2]==('scheduler','get-schedule'))
        self.assertLess(creates[0],scheduled);self.assertLess(scheduled,read);self.assertLess(read,creates[1])
        primary=aws.schedules['studio-expiry-'+selected['token']]
        self.assertEqual(json.loads(primary['Target']['Input']),{'StackName':selected['stack_id']})

    def test_invalid_role_prevents_any_billable_stack(self):
        aws=AWS()
        def corrupt(service,action,args,result):
            if (service,action)==('iam','get-role-policy') and args[args.index('--policy-name')+1]==creator.POLICY:
                result['PolicyDocument']['Statement'][0]['Resource']='*'
        aws.after=corrupt
        with self.assertRaisesRegex(ValueError,'permissions'):aws.desktop()
        self.assertFalse(aws.vms);self.assertFalse(aws.schedules)

    def test_failure_before_primary_readback_never_creates_compute(self):
        for action in ('create-schedule','get-schedule'):
            aws=AWS()
            def fail(service,op,args):
                if service=='scheduler' and op==action:raise RuntimeError('injected guard failure')
            aws.before=fail
            with self.subTest(action=action),self.assertRaises(RuntimeError):aws.desktop()
            self.assertFalse(aws.vms)

    def test_failure_after_workload_creation_requests_native_teardown(self):
        aws=AWS()
        def fail(service,action,args):
            if service=='scheduler' and action=='update-schedule':raise RuntimeError('injected pin failure')
        aws.before=fail
        with self.assertRaisesRegex(RuntimeError,'Creation failed'):aws.desktop()
        self.assertTrue(aws.vms)
        self.assertTrue(all(vm['State']['Name']=='terminated' for vm in aws.vms.values()))
        self.assertFalse(aws.volumes)
        self.assertTrue(any(c[:2]==('cloudformation','delete-stack') for c in aws.calls))
        self.assertTrue(aws.schedules)  # Do not disarm protection on uncertain creation.

    def test_scope_permissions_are_exact_and_volume_delete_is_absent(self):
        template=json.loads((ROOT/'infra/workstation-expiration-role.json').read_text())
        self.assertNotIn('deletevolume',json.dumps(template).lower())
        aws=AWS();s=aws.desktop();identity=creator.safety(aws,h.REGION,s['stack'])
        role=identity['role'].rsplit('/',1)[1]
        original=copy.deepcopy(aws.policies[role])
        for change in ('scope','trust','extra-policy','managed-policy','wrong-role'):
            with self.subTest(change=change):
                aws.policies[role]=copy.deepcopy(original)
                if change=='scope':aws.policies[role][creator.POLICY]['Statement'][0]['Condition']['StringEquals']['ec2:ResourceTag/StudioCleanupToken']='foreign'
                if change=='extra-policy':aws.policies[role]['Extra']={}
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

    def test_duplicate_name_never_updates_existing_stack(self):
        aws=AWS();first=aws.desktop();before=copy.deepcopy(aws.vms)
        with self.assertRaises(Exception):aws.desktop()
        self.assertEqual(aws.vms,before)
        self.assertFalse(any(c[1]=='update-stack' for c in aws.calls))
