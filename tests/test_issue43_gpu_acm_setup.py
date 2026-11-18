"""Exact current-owner/pending-destination IAM; pinned guest provisioning stays separate."""
import copy
import json
import subprocess
import unittest
from unittest.mock import patch
from runtime import issue43_gpu_acm_setup as setup
from workstation_fake import AWS, h, acm, missing, tagdict, tags


class GrantTests(unittest.TestCase):
    def setUp(self):
        self.aws=AWS();self.a=self.aws.desktop('desktop-a','cpu');self.b=self.aws.desktop('desktop-b','cpu')
        self.iid=self.a['instance'];self.other=self.b['instance']

    def prepare(self, iid=None):
        with patch.object(h,'check_gpu_tls'):
            return setup.prepare_gpu(self.aws,iid or self.iid,sleep=lambda _:None)

    def test_destination_uses_exact_session_and_shared_policy_is_untouched(self):
        unrelated=copy.deepcopy(self.aws.policies[setup.BASE_ROLE])
        self.prepare()
        policy=self.aws.policies[setup.BASE_ROLE][setup.POLICY_PREFIX+self.iid]
        self.assertEqual(policy,setup._policy(setup.SOURCE_ID+':'+self.iid))
        for name,doc in unrelated.items():self.assertEqual(self.aws.policies[setup.BASE_ROLE][name],doc)
        before=len(self.aws.writes);self.prepare();self.assertEqual(len(self.aws.writes),before)

    def test_current_owner_and_pending_target_only_then_revoke_old(self):
        setup._install_grant(self.aws,self.iid)
        self.aws.address['InstanceId']=self.iid
        self.prepare(self.other)
        self.assertEqual(setup._grants(self.aws)[0],{self.iid,self.other})
        self.aws.address['InstanceId']=self.other
        setup.revoke_gpu(self.aws,self.iid)
        self.assertEqual(setup._grants(self.aws)[0],{self.other})
        with self.assertRaisesRegex(ValueError,'current hostname owner'):setup.revoke_gpu(self.aws,self.other)

    def test_third_session_is_not_allowed(self):
        for iid in (self.iid,self.other):setup._install_grant(self.aws,iid)
        with self.assertRaisesRegex(ValueError,'stale hostname grants'):
            setup._install_grant(self.aws,'i-0ffffffffffffffff')

    def test_old_dead_cpu_grant_is_reconciled_only_on_explicit_assignment(self):
        old='i-0bbfbdc878d3a2320'
        policy=setup._policy(setup.SOURCE_ID+':'+old)
        policy['Statement'][0]['Sid']='AssumeOnlyIssue43ExportRoleFromExactCpuEc2Session'
        self.aws.policies[setup.BASE_ROLE][setup.CPU_POLICY]=policy
        self.aws.roles[setup.EXPORT_NAME]['AssumeRolePolicyDocument']={'Version':'2012-10-17','Statement':[setup._statement(setup.SOURCE_ID+':'+old)]}
        self.prepare()
        self.assertNotIn(setup.CPU_POLICY,self.aws.policies[setup.BASE_ROLE])
        self.assertEqual(setup._grants(self.aws)[0],{self.iid})

    def test_failed_new_guest_enrollment_revokes_only_destination(self):
        setup._install_grant(self.aws,self.iid);self.aws.address['InstanceId']=self.iid
        with patch.object(h,'check_gpu_tls',side_effect=RuntimeError('not ready')),patch.object(setup,'_ssm',side_effect=RuntimeError('install failed')):
            with self.assertRaisesRegex(RuntimeError,'install failed'):setup.prepare_gpu(self.aws,self.other)
        self.assertEqual(setup._grants(self.aws)[0],{self.iid})

    def test_partial_selected_grant_and_revocation_can_be_retried(self):
        fired=[]
        def fail(service,action,args):
            if action=='update-assume-role-policy' and not fired:fired.append(1);raise RuntimeError('injected')
        self.aws.before=fail
        with self.assertRaises(RuntimeError):setup._install_grant(self.aws,self.iid)
        self.aws.before=None
        self.assertTrue(setup._install_grant(self.aws,self.iid))
        fired=[]
        def fail_delete(service,action,args):
            if action=='delete-role-policy' and not fired:fired.append(1);raise RuntimeError('injected')
        self.aws.before=fail_delete
        with self.assertRaises(RuntimeError):setup._revoke_grant(self.aws,self.iid)
        self.aws.before=None;setup._revoke_grant(self.aws,self.iid)
        self.assertFalse(setup._get_role_state(self.aws,self.iid))

    def test_foreign_or_expanded_permissions_are_not_overwritten(self):
        for mutation in ('trust','export','base-id','unrelated-partial','extra-export-policy'):
            aws=AWS();a=aws.desktop('cpu','cpu')
            if mutation=='trust':aws.roles[setup.EXPORT_NAME]['AssumeRolePolicyDocument']['Statement']=[{'Effect':'Allow','Principal':{'AWS':setup.SOURCE_ARN},'Action':'sts:AssumeRole'}]
            if mutation=='export':aws.policies[setup.EXPORT_NAME]['StudioIssue43ExactAcMExport']['Statement'][0]['Resource']='*'
            if mutation=='base-id':aws.roles[setup.BASE_ROLE]['RoleId']='foreign'
            if mutation=='unrelated-partial':aws.policies[setup.BASE_ROLE][setup.POLICY_PREFIX+'i-0ffffffffffffffff']=setup._policy(setup.SOURCE_ID+':i-0ffffffffffffffff')
            if mutation=='extra-export-policy':aws.policies[setup.EXPORT_NAME]['Extra']={}
            aws.calls=[]
            with self.subTest(mutation=mutation),self.assertRaises(ValueError):setup._install_grant(aws,a['instance'])
            self.assertFalse(aws.writes)

    def test_permission_denial_is_not_a_missing_previous_instance(self):
        old='i-0bbfbdc878d3a2320';setup._install_grant(self.aws,old)
        def fail(service,action,args):
            if action=='describe-instances' and old in args:raise missing('AccessDenied',action)
        self.aws.before=fail;self.aws.calls=[]
        with self.assertRaises(subprocess.CalledProcessError):self.prepare()
        self.assertFalse(self.aws.writes)

    def test_refresh_reuses_pinned_provider_before_attempting_reinstallation(self):
        with patch.object(h,'check_gpu_tls',side_effect=[RuntimeError('provider needs refresh'),None]),patch.object(setup,'_ssm') as ssm:
            setup.prepare_gpu(self.aws,self.iid,sleep=lambda _:None)
        self.assertEqual(ssm.call_count,1)
        self.assertEqual(ssm.call_args.args[2],setup._refresh_script())
        self.assertNotIn('--enroll-default','\n'.join(ssm.call_args.args[2]))

    def test_installer_and_refresh_are_pinned_and_never_print_keys(self):
        for commands in (setup._guest_script(),setup._refresh_script()):
            text='\n'.join(commands)
            self.assertIn(setup.AWCP_SHA256,text)
            self.assertIn(setup.ARTIFACTS['activate.py'][1],text)
            self.assertNotIn('BEGIN PRIVATE KEY',text)
            self.assertNotIn('associate-address',text)
            subprocess.run(['bash','-n'],input=text,text=True,check=True)

    def test_ssm_requires_exact_success_marker_and_rejects_access_denied(self):
        def bad_result(service,action,args,result):
            if action=='get-command-invocation':result['StandardOutputContent']='almost '+setup.MARKER
        self.aws.after=bad_result
        with self.assertRaisesRegex(RuntimeError,'marker'):setup._ssm(self.aws,self.iid,['safe'],sleep=lambda _:None)
        self.aws.after=None
        def denied(service,action,args):
            if action=='get-command-invocation':raise missing('AccessDenied',action)
        self.aws.before=denied
        with self.assertRaises(subprocess.CalledProcessError):setup._ssm(self.aws,self.iid,['safe'],sleep=lambda _:None)

    def absent_response(self, iid, response):
        """Model successful EC2 replies that differ from the NotFound fixture."""
        def call(region, service, action, *args):
            if (service, action) == ('ec2', 'describe-instances') and iid in args:
                self.aws.calls.append((service, action, *args))
                return copy.deepcopy(response)
            return self.aws(region, service, action, *args)
        return call

    def test_old_cpu_empty_response_reconciles_only_its_exact_grant(self):
        old = 'i-0bbfbdc878d3a2320'
        setup._install_grant(self.aws, old)
        unrelated = copy.deepcopy(self.aws.policies[setup.BASE_ROLE]['UnrelatedEfsRead'])
        before = copy.deepcopy(self.aws.address)
        call = self.absent_response(old, {'Reservations': []})
        with patch.object(h, 'check_gpu_tls'):
            setup.prepare_gpu(call, self.iid, sleep=lambda _: None)
        self.assertEqual(setup._grants(self.aws)[0], {self.iid})
        self.assertNotIn(setup.POLICY_PREFIX + old, self.aws.policies[setup.BASE_ROLE])
        self.assertEqual(self.aws.policies[setup.BASE_ROLE]['UnrelatedEfsRead'], unrelated)
        self.assertEqual(self.aws.address, before)

    def test_missing_destination_still_fails_before_any_write(self):
        call = self.absent_response(self.iid, {'Reservations': []})
        self.aws.calls.clear()
        with self.assertRaisesRegex(ValueError, 'EC2 identity'):
            setup.prepare_gpu(call, self.iid, sleep=lambda _: None)
        self.assertFalse(self.aws.writes)

    def test_empty_result_requires_explicit_single_stale_id_opt_in(self):
        call = self.absent_response(self.iid, {'Reservations': []})
        self.assertEqual(h._items(call, self.iid, allow_missing=True), {})
        for ids, options in (((self.iid,), {}),
                             ((self.iid, self.other), {'allow_missing': True})):
            with self.subTest(ids=ids), self.assertRaisesRegex(ValueError, 'EC2 identity'):
                h._items(call, *ids, **options)

    def test_missing_opt_in_never_accepts_malformed_duplicate_or_foreign_results(self):
        matching = {'InstanceId': self.iid}
        for response in ({}, {'Reservations': [{'Instances': []}]},
                         {'Reservations': [{'Instances': [matching, matching]}]},
                         {'Reservations': [{'Instances': [{'InstanceId': self.other}]}]}):
            with self.subTest(response=response), self.assertRaisesRegex(ValueError, 'EC2 identity'):
                h._items(self.absent_response(self.iid, response), self.iid, allow_missing=True)

    def test_absent_detached_owner_metadata_can_be_released(self):
        from runtime import independent_eip
        old = 'i-0bbfbdc878d3a2320'
        setup._install_grant(self.aws, old)
        self.aws.address['Tags'].append({'Key': 'StudioDcvInstanceId', 'Value': old})
        call = self.absent_response(old, {'Reservations': []})
        result = independent_eip.release(call)
        self.assertIsNone(result['owner'])
        self.assertEqual(setup._grants(self.aws)[0], set())
        self.assertNotIn('StudioDcvInstanceId', h._tags(self.aws.address))
        self.assertEqual(self.aws.address['AllocationId'], h.EIP_ID)
