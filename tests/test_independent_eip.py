"""One hostname owner; automatic claims and explicit transfers preserve reachability."""
import copy
import datetime as dt
import unittest
from unittest.mock import patch
from runtime import independent_eip as eip, dcv_endpoint, pr65_one_shot_guard as guard
from workstation_fake import AWS, NOW, h, acm, inventory, tagdict, tags, missing, release_stopped_hostname


class HostnameTests(unittest.TestCase):
    def setUp(self):
        self.aws=AWS();self.a=self.aws.desktop('art');self.b=self.aws.desktop('ai');self.cpu=self.aws.desktop('edit','cpu')
        self.tls=lambda host,ip:'https://'+host+':8443'
        self.patches=[]
        real_status,real_rearm=guard.status,guard.rearm
        self.add(patch.object(guard,'status',side_effect=lambda aws,c,**kw:real_status(aws,c,**{'now':NOW,**kw})))
        self.add(patch.object(guard,'rearm',side_effect=lambda aws,c,**kw:real_rearm(aws,c,**{'now':NOW,**kw})))
        self.add(patch.object(h,'check_gpu_tls'))
        self.add(patch.object(eip,'resolve',side_effect=lambda c,r,aws,tls:dcv_endpoint.resolve(c,r,aws,tls,probe=lambda *_:None)))
        real_reachable=eip._reachable
        self.add(patch.object(eip,'_reachable',side_effect=lambda aws,c,tls,**kw:real_reachable(aws,c,tls,sleep=lambda _:None,**kw)))

    def add(self,p):
        p.start();self.addCleanup(p.stop)

    def assign(self,name):return eip.assign(self.aws,name,self.tls)

    def test_gpu_gpu_cpu_transfer_preserves_every_running_endpoint(self):
        for selected in (self.a,self.b,self.cpu):
            result=self.assign(selected['stack'])
            self.assertEqual(result['owner'],selected['stack'])
            self.assertTrue(result['endpoint']['trusted'])
            for c in (self.a,self.b,self.cpu):
                fresh=inventory.context(self.aws,h.REGION,c['stack'])
                self.assertTrue(eip.resolve(fresh,h.REGION,self.aws,self.tls)['reachable'])
            self.assertEqual(acm._grants(self.aws)[0],{selected['instance']})
        self.assertFalse(any(c[1] in ('allocate-address','release-address','stop-instances','change-resource-record-sets') for c in self.aws.calls))

    def test_create_or_start_never_steals_an_existing_owner(self):
        self.assign(self.a['stack']);before=copy.deepcopy(self.aws.address)
        self.aws.calls=[]
        self.assertIsNone(eip.assign(self.aws,'ai',self.tls,only_if_unassigned=True))
        self.assertEqual(self.aws.address,before)
        self.assertEqual(self.aws.writes,[])

    def test_automatic_claim_skips_unknown_or_partial_associations(self):
        unassigned=copy.deepcopy(self.aws.address)
        for identity in ({'InstanceId':'i-0ffffffffffffffff'}, {'AssociationId':'foreign'},
                         {'NetworkInterfaceId':'eni-foreign'}):
            with self.subTest(identity=identity):
                self.aws.address={**unassigned,**identity};self.aws.calls=[]
                self.assertIsNone(eip.assign(self.aws,'ai',self.tls,only_if_unassigned=True))
                self.assertEqual(self.aws.address,{**unassigned,**identity})
                self.assertEqual(self.aws.writes,[])

    def test_competing_claim_never_detaches_winner_or_prepares_losing_certificate(self):
        unassigned=copy.deepcopy(self.aws.address)
        for owner in ('i-0ffffffffffffffff',None):
            with self.subTest(owner=owner):
                self.aws.address=copy.deepcopy(unassigned);self.aws.calls=[]
                winner={'AssociationId':'foreign','NetworkInterfaceId':'eni-foreign'}
                if owner:winner['InstanceId']=owner
                def race(service,action,args):
                    if action=='associate-address':
                        self.aws.address.update(winner)
                        raise missing('Resource.AlreadyAssociated',action)
                self.aws.before=race
                with patch.object(acm,'prepare_gpu') as prepare,self.assertRaises(RuntimeError):
                    eip.assign(self.aws,'edit',self.tls,only_if_unassigned=True)
                prepare.assert_not_called();self.aws.before=None
                self.assertEqual(self.aws.address,{**unassigned,**winner})
                self.assertFalse(any(c[1]=='disassociate-address' or c[0] in ('iam','ssm') for c in self.aws.calls))

    def test_automatic_claim_rechecks_owner_after_stale_metadata_cleanup(self):
        self.assign('art');self.aws.detach()
        real_move=eip._move
        winner={'InstanceId':'i-0ffffffffffffffff','AssociationId':'foreign','NetworkInterfaceId':'eni-foreign'}
        def race(aws,before,destination):
            result=real_move(aws,before,destination)
            if destination is None:aws.address.update(winner)
            return result
        with patch.object(eip,'_move',side_effect=race),patch.object(acm,'prepare_gpu') as prepare:
            self.assertIsNone(eip.assign(self.aws,'ai',self.tls,only_if_unassigned=True))
        prepare.assert_not_called()
        self.assertEqual({k:self.aws.address[k] for k in winner},winner)

    def test_automatic_certificate_or_tls_failure_releases_only_own_claim_and_grant(self):
        real_prepare=acm.prepare_gpu
        for failure in ('certificate','tls'):
            with self.subTest(failure=failure):
                def prepare(aws,iid):
                    self.assertEqual(aws.address.get('InstanceId'),iid)
                    if failure=='certificate':
                        acm._install_grant(aws,iid)
                        raise RuntimeError('certificate unavailable')
                    return real_prepare(aws,iid)
                def tls(host,ip):
                    if failure=='tls':raise ValueError('Trusted DCV TLS failure')
                    return self.tls(host,ip)
                with patch.object(acm,'prepare_gpu',side_effect=prepare),self.assertRaises(RuntimeError):
                    eip.assign(self.aws,'ai',tls,only_if_unassigned=True)
                self.assertIsNone(self.aws.address.get('InstanceId'))
                self.assertEqual(acm._grants(self.aws)[0],set())
                result=eip.resolve(inventory.context(self.aws,h.REGION,'ai'),h.REGION,self.aws,self.tls)
                self.assertTrue(result['reachable']);self.assertFalse(result['trusted'])

    def test_automatic_claim_lost_during_certificate_preparation_preserves_winner(self):
        def prepare(aws,iid):
            acm._install_grant(aws,iid)
            acm._install_grant(aws,self.b['instance'])
            aws.detach()
            aws.address.update(InstanceId=self.b['instance'],AssociationId='foreign',
                               NetworkInterfaceId=aws.vms[self.b['instance']]['NetworkInterfaces'][0]['NetworkInterfaceId'])
            next(t for t in aws.address['Tags'] if t['Key']=='StudioDcvInstanceId')['Value']=self.b['instance']
            raise RuntimeError('owner changed during certificate preparation')
        with patch.object(acm,'prepare_gpu',side_effect=prepare),self.assertRaisesRegex(RuntimeError,'foreign owner'):
            eip.assign(self.aws,'edit',self.tls,only_if_unassigned=True)
        self.assertEqual(self.aws.address['InstanceId'],self.b['instance'])
        self.assertFalse(any(c[1]=='disassociate-address' for c in self.aws.writes))
        self.assertEqual(acm._grants(self.aws)[0],{self.b['instance']})

    def test_release_preserves_allocation_and_native_public_access(self):
        self.assign('art');result=eip.release(self.aws,self.tls)
        self.assertIsNone(result['owner']);self.assertTrue(result['previous_endpoint']['reachable'])
        self.assertEqual(self.aws.address['AllocationId'],h.EIP_ID)
        self.assertEqual(acm._grants(self.aws)[0],set())

    def test_stop_start_fallback_only_for_previous_owner(self):
        self.assign('art');self.aws.restore_public_ip=False;self.aws.calls=[]
        result=self.assign('ai')
        self.assertTrue(result['previous_endpoint']['reachable'])
        writes=[c for c in self.aws.calls if c[1] in ('stop-instances','start-instances')]
        self.assertEqual([c[1] for c in writes],['stop-instances','start-instances'])
        self.assertTrue(all(c[-1]==self.a['instance'] for c in writes))
        self.assertFalse(any(c[1]=='allocate-address' for c in self.aws.calls))

    def test_timer_verification_failure_blocks_hostname_recovery_restart(self):
        self.assign('art')
        self.aws.restore_public_ip = False
        guard.rearm.side_effect = ValueError('cannot verify stop timer')
        self.aws.calls.clear()
        with self.assertRaises((ValueError, RuntimeError)):
            self.assign('ai')
        self.assertFalse(any(c[1] == 'start-instances' and c[-1] == self.a['instance'] for c in self.aws.calls))
        self.assertTrue(self.aws.volumes)
        self.assertFalse(any(c[1] in ('terminate-instances', 'delete-stack') for c in self.aws.calls))

    def test_failure_before_or_after_eip_mutation_restores_original_owner(self):
        self.assign('art')
        for action in ('disassociate-address','associate-address','create-tags'):
            for after in (False,True):
                fired=[]
                def fail(service,op,args,*rest):
                    if service=='ec2' and op==action and not fired:fired.append(1);raise RuntimeError('injected transport failure')
                if after:self.aws.after=fail
                else:self.aws.before=fail
                with self.subTest(action=action,after=after),self.assertRaises(RuntimeError):self.assign('ai')
                self.aws.before=self.aws.after=None
                self.assertEqual(self.aws.address.get('InstanceId'),self.a['instance'])
                self.assertEqual(acm._grants(self.aws)[0],{self.a['instance']})

    def test_failed_public_tls_rolls_back_and_checks_both_endpoints(self):
        self.assign('art')
        def bad_tls(host,ip):
            if self.aws.address.get('InstanceId')==self.b['instance']:raise ValueError('Trusted DCV TLS failure')
            return self.tls(host,ip)
        with self.assertRaisesRegex(RuntimeError,'original association restored'):eip.assign(self.aws,'ai',bad_tls)
        self.assertEqual(self.aws.address['InstanceId'],self.a['instance'])
        self.assertTrue(self.aws.vms[self.b['instance']].get('PublicIpAddress'))

    def test_foreign_owner_on_failure_is_never_seized_by_rollback(self):
        self.assign('art');fired=[]
        def conflict(service,action,args,*rest):
            if action=='disassociate-address' and not fired:
                fired.append(1);self.aws.address.update(InstanceId='i-0ffffffffffffffff',AssociationId='foreign',NetworkInterfaceId='foreign')
                raise RuntimeError('concurrent owner')
        self.aws.after=conflict
        with self.assertRaisesRegex(RuntimeError,'foreign owner'):self.assign('ai')
        self.assertEqual(self.aws.address['InstanceId'],'i-0ffffffffffffffff')

    def test_foreign_tags_and_unsupported_networking_fail_before_eip_writes(self):
        for mutation in ('owner-tag','two-enis','secondary-private','foreign-vpc','wrong-stack'):
            aws=AWS();a=aws.desktop('cpu','cpu')
            if mutation=='owner-tag':aws.address.update(InstanceId=a['instance'],AssociationId='x',NetworkInterfaceId='x')
            if mutation=='two-enis':aws.vms[a['instance']]['NetworkInterfaces']*=2
            if mutation=='secondary-private':aws.vms[a['instance']]['NetworkInterfaces'][0]['PrivateIpAddresses'].append({'Primary':False})
            if mutation=='foreign-vpc':aws.vms[a['instance']]['VpcId']='foreign'
            if mutation=='wrong-stack':next(t for t in aws.vms[a['instance']]['Tags'] if t['Key']=='aws:cloudformation:stack-id')['Value']='foreign'
            aws.calls=[]
            with self.subTest(mutation=mutation),self.assertRaises((ValueError,RuntimeError)):eip.assign(aws,'cpu',self.tls)
            self.assertFalse(any(c[1] in ('associate-address','disassociate-address') for c in aws.calls))

    def test_stale_detached_owner_recovers_before_assignment(self):
        self.assign('art');self.aws.detach();self.aws.vms[self.a['instance']].pop('PublicIpAddress',None)
        self.assign('ai')
        self.assertTrue(self.aws.vms[self.a['instance']].get('PublicIpAddress'))
        self.assertEqual(self.aws.address['InstanceId'],self.b['instance'])

    def test_stopped_nonowner_does_not_change_the_selected_hostname(self):
        self.assign('ai');before=copy.deepcopy(self.aws.address)
        self.aws.vms[self.a['instance']]['State']['Name']='stopped'
        eip.release_if_owned(self.aws,self.a,self.tls)
        self.assertEqual(self.aws.address,before)

    def test_owner_stopping_during_assignment_is_not_reported_success(self):
        self.assign('art')
        def stopped(service,action,args,result):
            if action=='create-tags':self.aws.vms[self.b['instance']]['State']['Name']='stopped'
        self.aws.after=stopped
        with self.assertRaises(RuntimeError):self.assign('ai')
        self.assertEqual(self.aws.address['InstanceId'],self.a['instance'])

    def test_failed_guest_certificate_prevents_any_eip_change(self):
        self.assign('art');before=copy.deepcopy(self.aws.address)
        for state in ('running', 'stopped'):
            self.aws.vms[self.a['instance']]['State']['Name'] = state
            with self.subTest(state=state), patch.object(acm,'prepare_gpu',side_effect=RuntimeError('guest failed')):
                with self.assertRaises(RuntimeError):self.assign('ai')
            self.assertEqual(self.aws.address,before)

    def test_unassociated_release_is_idempotent(self):
        self.assertIsNone(eip.release(self.aws,self.tls)['owner'])
        self.assertIsNone(eip.release(self.aws,self.tls)['owner'])

    def test_explicit_transfer_retries_when_stopped_owner_is_released_during_certificate_setup(self):
        self.assign('art'); self.aws.vms[self.a['instance']]['State']['Name'] = 'stopped'
        baseline, prepare = copy.deepcopy(self.aws), acm.prepare_gpu
        for phase in ('during-prepare', 'after-prepare'):
            with self.subTest(phase=phase):
                self.aws = copy.deepcopy(baseline)
                fired, checks = [], []
                def release():
                    fired.append(True); release_stopped_hostname(self.aws)
                def check(aws, iid, **kw):
                    checks.append(iid)
                    if phase == 'during-prepare' and len(checks) == 2 and not fired: release()
                def enroll(aws, iid):
                    result = prepare(aws, iid)
                    if phase == 'after-prepare' and not fired: release()
                    return result
                self.aws.calls.clear()
                with patch.object(h, 'check_gpu_tls', side_effect=check), patch.object(acm, 'prepare_gpu', side_effect=enroll):
                    result = self.assign('ai')
                self.assertTrue(fired)
                self.assertEqual(result['owner'], 'ai')
                self.assertEqual(self.aws.address['InstanceId'], self.b['instance'])
                self.assertEqual(acm._grants(self.aws)[0], {self.b['instance']})
                self.assertEqual(self.aws.vms[self.a['instance']]['State']['Name'], 'stopped')
                self.assertEqual(self.aws.volumes, baseline.volumes)
                self.assertFalse(any(c[1] == 'associate-address' and self.a['vm']['NetworkInterfaces'][0]['NetworkInterfaceId'] in c for c in self.aws.writes))
                self.assertFalse(any(c[1] in ('start-instances', 'stop-instances', 'terminate-instances') for c in self.aws.writes))

    def test_background_release_checks_current_owner_and_survives_delayed_events(self):
        self.assign('art')
        baseline = copy.deepcopy(self.aws)
        for case in ('running', 'unassigned', 'nonowner-stopped', 'restarted', 'replaced', 'owner-tag', 'eni', 'foreign-stack'):
            with self.subTest(case=case):
                self.aws = copy.deepcopy(baseline)
                vm = self.aws.vms[self.a['instance']]
                vm['State']['Name'] = 'stopped'
                if case == 'running': vm['State']['Name'] = 'running'
                if case == 'unassigned': self.aws.detach()
                if case == 'nonowner-stopped':
                    vm['State']['Name'] = 'running'; self.aws.vms[self.b['instance']]['State']['Name'] = 'stopped'
                if case == 'restarted':
                    self.aws.fire_schedule('studio-stop-' + self.a['token'])
                    self.aws(h.REGION, 'ec2', 'start-instances', '--instance-ids', self.a['instance'])
                if case == 'replaced':
                    next(r for r in self.aws.resources[self.a['stack_id']] if r['LogicalResourceId'] == 'Workstation')['PhysicalResourceId'] = self.b['instance']
                if case == 'owner-tag':
                    next(t for t in self.aws.address['Tags'] if t['Key'] == 'StudioDcvInstanceId')['Value'] = self.b['instance']
                if case == 'eni': self.aws.address['NetworkInterfaceId'] = 'eni-foreign'
                if case == 'foreign-stack':
                    next(t for t in vm['Tags'] if t['Key'] == 'aws:cloudformation:stack-id')['Value'] = 'arn:foreign'
                before = copy.deepcopy(self.aws.address); self.aws.calls.clear()
                if case in ('replaced', 'owner-tag', 'eni', 'foreign-stack'):
                    with self.assertRaises(ValueError): release_stopped_hostname(self.aws)
                else:
                    release_stopped_hostname(self.aws)
                self.assertEqual(self.aws.address, before)
                self.assertFalse(self.aws.writes)

    def test_background_release_is_idempotent_and_cannot_detach_a_new_association(self):
        from botocore.exceptions import ClientError
        self.assign('art'); baseline = copy.deepcopy(self.aws)
        for race in ('none', 'before-readback', 'before-detach', 'denied'):
            with self.subTest(race=race):
                self.aws = copy.deepcopy(baseline)
                self.aws.fire_schedule('studio-stop-' + self.a['token'])
                disks = copy.deepcopy(self.aws.volumes); grants = acm._grants(self.aws)
                def changed():
                    self.aws.address.update(InstanceId=self.b['instance'], AssociationId='new-owner',
                                            NetworkInterfaceId=self.b['vm']['NetworkInterfaces'][0]['NetworkInterfaceId'])
                    next(t for t in self.aws.address['Tags'] if t['Key'] == 'StudioDcvInstanceId')['Value'] = self.b['instance']
                def fault(service, action, args):
                    if action == 'describe-stack-resource' and race == 'before-readback': changed()
                    if action == 'disassociate-address' and race in ('before-detach', 'denied'):
                        if race == 'before-detach': changed()
                        raise ClientError({'Error': {'Code': 'InvalidAssociationID.NotFound' if race == 'before-detach' else 'AccessDenied', 'Message': race}}, action)
                self.aws.before = fault; self.aws.calls.clear()
                if race == 'denied':
                    with self.assertRaises(ClientError): release_stopped_hostname(self.aws)
                else: release_stopped_hostname(self.aws)
                self.aws.before = None
                self.assertEqual(self.aws.vms[self.a['instance']]['State']['Name'], 'stopped')
                self.assertEqual(self.aws.volumes, disks); self.assertEqual(acm._grants(self.aws), grants)
                if race == 'none':
                    self.assertIsNone(self.aws.address.get('AssociationId'))
                    release_stopped_hostname(self.aws)
                    self.assertEqual(len([c for c in self.aws.calls if c[1] == 'disassociate-address']), 1)
                elif race != 'denied': self.assertEqual(self.aws.address['AssociationId'], 'new-owner')
                self.assertFalse(any(c[1] in ('create-tags', 'delete-tags', 'terminate-instances', 'delete-stack', 'delete-volume') for c in self.aws.writes))
