"""Disposable workstation teardown: native CloudFormation, exact scope, resumable checks."""
import copy
import subprocess
import unittest
from types import SimpleNamespace
from unittest.mock import Mock

from runtime import issue43_destroy as cleanup, pr65_gpu_create as creator, publisher
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


class AgedOutInstanceTests(unittest.TestCase):
    def deployment(self, profile='gpu'):
        aws = AWS()
        if profile == 'publisher':
            aws.native_create('retired', publisher.template(SimpleNamespace(security_group_id='sg-existing')),
                              {'ImageId': 'ami-reviewed', 'InstanceType': 'm7i.large',
                               'InstanceProfileName': 'StudioEfsCiPublisher', 'SubnetId': 'subnet-reviewed',
                               'DiskSizeGiB': 30}, {'StudioManaged': 'true', 'StudioProfile': profile})
            selected = inventory.context(aws, h.REGION, 'retired')
        else:
            selected = aws.desktop('retired', profile)
        return aws, selected

    def retire(self, aws, selected):
        aws(h.REGION, 'cloudformation', 'delete-stack', '--stack-name', selected['stack_id'])
        del aws.vms[selected['instance']]
        dispatch = aws.dispatch

        def aged_out(service, action, args):
            if (service, action) == ('ec2', 'describe-instances') and args == ('--instance-ids', selected['instance']):
                return {'Reservations': []}  # Successful AWS response observed after termination ages out.
            return dispatch(service, action, args)

        aws.dispatch = aged_out
        aws.calls.clear()

    def test_late_arn_retry_for_each_profile_cleans_only_its_remaining_guards(self):
        for profile in ('cpu', 'gpu', 'publisher'):
            with self.subTest(profile=profile):
                aws, selected = self.deployment(profile)
                other = aws.desktop('still-active')
                self.retire(aws, selected)
                other_vm = copy.deepcopy(aws.vms[other['instance']])
                shared = copy.deepcopy(aws.address)
                for _ in range(2):
                    result = cleanup.destroy(aws, Mock(), selected['stack_id'], h.REGION)
                    self.assertEqual(result['state'], 'DELETE_COMPLETE')
                    self.assertEqual(result['stack_id'], selected['stack_id'])
                self.assertEqual(aws.vms[other['instance']], other_vm)
                self.assertEqual(aws.address, shared)
                self.assertEqual(set(aws.schedules), {'studio-expiry-' + other['token'],
                                                      'studio-fallback-' + other['token']})
                if profile == 'gpu':
                    self.assertEqual(aws.stacks[selected['safety_stack']]['StackStatus'], 'DELETE_COMPLETE')
                self.assertFalse(any(call[1] == 'delete-volume' for call in aws.calls))

    def test_safety_only_retry_checks_aged_out_fallback_before_removing_role(self):
        aws, selected = self.deployment()
        self.retire(aws, selected)
        self.assertTrue(cleanup.destroy(aws, Mock(), selected['stack'], h.REGION)['safety_removed'])
        fallback_read = ('ec2', 'describe-instances', '--instance-ids', selected['instance'])
        self.assertIn(fallback_read, aws.calls)
        self.assertFalse(aws.schedules)
        self.assertEqual(aws.stacks[selected['safety_stack']]['StackStatus'], 'DELETE_COMPLETE')

    def test_absent_instance_requires_native_deletion_evidence(self):
        for stack_state, resource_state in (('CREATE_COMPLETE', 'CREATE_COMPLETE'),
                                            ('CREATE_COMPLETE', 'DELETE_COMPLETE'),
                                            ('DELETE_IN_PROGRESS', 'DELETE_IN_PROGRESS'),
                                            ('DELETE_FAILED', 'DELETE_FAILED'),
                                            ('DELETE_COMPLETE', 'DELETE_SKIPPED')):
            for response in ('empty', 'not-found'):
                with self.subTest(stack=stack_state, resource=resource_state, response=response):
                    aws, selected = self.deployment('cpu')
                    self.retire(aws, selected)
                    aws.stacks[selected['stack_id']]['StackStatus'] = stack_state
                    next(r for r in aws.resources[selected['stack_id']]
                         if r['LogicalResourceId'] == 'Workstation')['ResourceStatus'] = resource_state
                    if response == 'not-found':
                        def denied(service, action, args):
                            if (service, action) == ('ec2', 'describe-instances'):
                                raise missing('InvalidInstanceID.NotFound', action)
                        aws.before = denied
                    with self.assertRaises((ValueError, subprocess.CalledProcessError)):
                        inventory.context(aws, h.REGION, selected['stack_id'])
                    self.assertFalse(aws.writes)

    def test_late_retry_resumes_deletion_after_workstation_resource_is_deleted(self):
        for state in ('DELETE_FAILED', 'DELETE_IN_PROGRESS'):
            with self.subTest(state=state):
                aws, selected = self.deployment()
                self.retire(aws, selected)
                aws.stacks[selected['stack_id']]['StackStatus'] = state
                dispatch = aws.dispatch
                def finish_delete(service, action, args):
                    if service == 'cloudformation' and selected['stack_id'] in args and action in ('delete-stack', 'wait'):
                        aws.stacks[selected['stack_id']]['StackStatus'] = 'DELETE_COMPLETE'
                        return {}
                    return dispatch(service, action, args)
                aws.dispatch = finish_delete
                self.assertEqual(cleanup.destroy(aws, Mock(), selected['stack_id'], h.REGION)['state'], 'DELETE_COMPLETE')
                self.assertFalse(aws.schedules)
                self.assertEqual(aws.stacks[selected['safety_stack']]['StackStatus'], 'DELETE_COMPLETE')

    def test_completed_context_still_rejects_malformed_duplicate_or_wrong_identity(self):
        for response in ({}, {'Reservations': [{}]}, {'Reservations': [{'Instances': []}]},
                         {'Reservations': [{'Instances': [{'InstanceId': 'i-fffffffffffffffff'}]}]},
                         {'Reservations': [{'Instances': [{}, {}]}]}):
            with self.subTest(response=response):
                aws, selected = self.deployment('cpu')
                self.retire(aws, selected)
                def malformed(service, action, args, result):
                    if (service, action) == ('ec2', 'describe-instances'):
                        result.clear()
                        result.update(copy.deepcopy(response))
                aws.after = malformed
                with self.assertRaises(ValueError):
                    cleanup.destroy(aws, Mock(), selected['stack_id'], h.REGION)
                self.assertFalse(aws.writes)

    def test_permission_errors_at_each_instance_read_preserve_safety_role(self):
        for denied_read in (1, 2, 3):  # Context, post-delete confirmation, fallback target.
            with self.subTest(denied_read=denied_read):
                aws, selected = self.deployment()
                self.retire(aws, selected)
                count = 0
                def denied(service, action, args):
                    nonlocal count
                    if (service, action) == ('ec2', 'describe-instances') and '--instance-ids' in args:
                        count += 1
                        if count == denied_read:
                            raise missing('AccessDenied', action)
                aws.before = denied
                with self.assertRaises(subprocess.CalledProcessError):
                    cleanup.destroy(aws, Mock(), selected['stack_id'], h.REGION)
                self.assertEqual(aws.stacks[selected['safety_stack']]['StackStatus'], 'CREATE_COMPLETE')
                self.assertIn('studio-fallback-' + selected['token'], aws.schedules)

    def test_completed_stack_with_surviving_disk_preserves_safety(self):
        aws, selected = self.deployment()
        aws.keep_volume = True
        self.retire(aws, selected)
        with self.assertRaisesRegex(RuntimeError, 'remaining instances/disks'):
            cleanup.destroy(aws, Mock(), selected['stack_id'], h.REGION, sleep=lambda _: None)
        self.assertEqual(aws.stacks[selected['safety_stack']]['StackStatus'], 'CREATE_COMPLETE')
        self.assertEqual(len(aws.schedules), 2)

    def test_completed_stack_does_not_skip_live_instance_disk_policy(self):
        aws, selected = self.deployment()
        aws.stacks[selected['stack_id']]['StackStatus'] = 'DELETE_COMPLETE'
        for resource in aws.resources[selected['stack_id']]:
            resource['ResourceStatus'] = 'DELETE_COMPLETE'
        aws.vms[selected['instance']]['BlockDeviceMappings'][0]['Ebs']['DeleteOnTermination'] = False
        aws.calls.clear()
        with self.assertRaisesRegex(ValueError, 'DeleteOnTermination'):
            cleanup.destroy(aws, Mock(), selected['stack_id'], h.REGION)
        self.assertFalse(aws.writes)
