"""Disposable workstation teardown: native CloudFormation, exact scope, resumable checks."""
import copy
import unittest
from unittest.mock import Mock

from runtime import issue43_destroy as cleanup, pr65_gpu_create as creator
from workstation_fake import AWS, NOW, h, inventory, missing, tags


class TeardownTests(unittest.TestCase):
    def setUp(self):
        self.aws=AWS();self.a=self.aws.desktop('gpu-a');self.b=self.aws.desktop('gpu-b')

    def destroy(self, name='gpu-a'):
        return cleanup.destroy(self.aws,Mock(),name,h.REGION,sleep=lambda _:None)

    def test_native_delete_removes_instance_and_disks_not_other_workstation(self):
        other=copy.deepcopy(self.aws.vms[self.b['instance']]);shared=copy.deepcopy(self.aws.address)
        outcome=self.destroy()
        self.assertEqual(outcome['state'],'DELETE_COMPLETE')
        self.assertEqual(self.aws.vms[self.a['instance']]['State']['Name'],'terminated')
        self.assertEqual(self.aws.vms[self.b['instance']],other)
        self.assertEqual(self.aws.address,shared)
        self.assertFalse(any(c[1]=='delete-volume' for c in self.aws.calls))
        self.assertEqual(len(self.aws.schedules),2)
        self.assertEqual(self.aws.stacks[self.a['safety_stack']]['StackStatus'],'DELETE_COMPLETE')
        self.assertEqual(self.aws.stacks[self.b['safety_stack']]['StackStatus'],'CREATE_COMPLETE')

    def test_delete_by_immutable_arn_and_retry_after_success(self):
        first=self.destroy(self.a['stack_id'])
        second=self.destroy(self.a['stack_id'])
        self.assertEqual(first['stack_id'],second['stack_id'])
        self.assertEqual(second['state'],'DELETE_COMPLETE')

    def test_failed_native_delete_keeps_schedules_and_role_for_retry(self):
        original=copy.deepcopy(self.aws.schedules)
        def fail(service,action,args):
            if (service,action)==('cloudformation','delete-stack'):
                self.aws.stacks[self.a['stack_id']]['StackStatus']='DELETE_FAILED'
                raise RuntimeError('injected native failure')
        self.aws.before=fail
        with self.assertRaisesRegex(RuntimeError,'native failure'):self.destroy()
        self.assertEqual(self.aws.schedules,original)
        self.aws.before=None
        self.assertEqual(self.destroy()['state'],'DELETE_COMPLETE')

    def test_surviving_disk_is_reported_not_managed_by_a_second_deleter(self):
        self.aws.keep_volume=True
        original=copy.deepcopy(self.aws.schedules)
        with self.assertRaisesRegex(RuntimeError,'remaining instances/disks'):self.destroy()
        self.assertEqual(self.aws.schedules,original)
        self.assertFalse(any(c[1]=='delete-volume' for c in self.aws.calls))

    def test_optional_hostname_failure_cannot_prevent_native_cost_teardown(self):
        from runtime import independent_eip
        from unittest.mock import patch
        with patch.object(independent_eip,'release_if_owned',side_effect=ValueError('foreign owner')):
            with self.assertRaisesRegex(ValueError,'foreign owner'):self.destroy()
        self.assertEqual(self.aws.vms[self.a['instance']]['State']['Name'],'terminated')
        self.assertTrue(self.aws.schedules)  # Incomplete cleanup remains visible.

    def test_bad_disk_policy_or_unexpected_resource_blocks_mutation(self):
        for kind in ('disk','resource','ownership'):
            aws=AWS();a=aws.desktop()
            if kind=='disk':aws.vms[a['instance']]['BlockDeviceMappings'][0]['Ebs']['DeleteOnTermination']=False
            if kind=='resource':aws.resources[a['stack_id']].append({'LogicalResourceId':'SharedEfs','ResourceType':'AWS::EFS::FileSystem'})
            if kind=='ownership':next(t for t in aws.vms[a['instance']]['Tags'] if t['Key']=='aws:cloudformation:stack-id')['Value']='foreign'
            aws.calls=[]
            with self.subTest(kind=kind),self.assertRaises(ValueError):cleanup.destroy(aws,Mock(),a['stack'],h.REGION)
            self.assertFalse(aws.writes)

    def test_forged_safety_schedule_is_preserved_and_never_targets_other_vm(self):
        p=self.aws.schedules['studio-fallback-'+self.a['token']]
        p['Target']['Input']='{"InstanceIds":["'+self.b['instance']+'"]}'
        with self.assertRaisesRegex(ValueError,'another workstation'):self.destroy()
        self.assertEqual(self.aws.vms[self.b['instance']]['State']['Name'],'running')
        self.assertIn(p['Name'],self.aws.schedules)

    def test_expired_auto_deleted_schedules_do_not_block_native_teardown(self):
        for name in list(self.aws.schedules):
            if self.a['token'] in name:del self.aws.schedules[name]
        self.assertEqual(self.destroy()['state'],'DELETE_COMPLETE')

    def test_safety_only_interrupted_create_has_explicit_native_cleanup(self):
        identity=creator.safety(self.aws,h.REGION,'interrupted',create=True)
        self.assertEqual(self.destroy('interrupted')['state'],'DELETE_COMPLETE')
        self.assertEqual(self.aws.stacks[identity['arn']]['StackStatus'],'DELETE_COMPLETE')

    def test_missing_ec2_record_does_not_lose_stack_identity(self):
        self.aws(h.REGION,'cloudformation','delete-stack','--stack-name',self.a['stack_id'])
        del self.aws.vms[self.a['instance']]
        self.assertEqual(self.destroy(self.a['stack_id'])['state'],'DELETE_COMPLETE')

    def test_broken_guard_role_cannot_block_native_workload_deletion(self):
        identity=creator.safety(self.aws,h.REGION,self.a['stack'])
        self.aws.policies[identity['role'].rsplit('/',1)[1]]={}
        self.assertEqual(self.destroy()['state'],'DELETE_COMPLETE')

    def test_aws_permission_errors_are_not_proof_of_absence(self):
        def fail(service,action,args):
            if service=='ec2' and action=='describe-volumes':raise missing('AccessDenied',action)
        self.aws.before=fail
        with self.assertRaises(Exception):self.destroy()
        self.assertTrue(self.aws.schedules)
