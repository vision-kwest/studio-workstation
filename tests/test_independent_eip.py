"""One explicit hostname owner; all running desktops remain reachable after transfers."""
import copy
import datetime as dt
import unittest
from unittest.mock import patch
from runtime import independent_eip as eip, dcv_endpoint, pr65_one_shot_guard as guard
from workstation_fake import AWS, NOW, h, acm, inventory, tagdict, tags, missing


class HostnameTests(unittest.TestCase):
    def setUp(self):
        self.aws=AWS();self.a=self.aws.desktop('art');self.b=self.aws.desktop('ai');self.cpu=self.aws.desktop('edit','cpu')
        self.tls=lambda host,ip:'https://'+host+':8443'
        self.patches=[]
        real_status=guard.status
        self.add(patch.object(guard,'status',side_effect=lambda aws,c,**kw:real_status(aws,c,now=NOW,**kw)))
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
        self.aws.desktop('third-gpu')
        self.assertEqual(self.aws.address,before)

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

    def test_expired_restart_window_rolls_back_without_extending_deadlines(self):
        self.assign('art');self.aws.restore_public_ip=False
        original=guard.status.side_effect
        def near_expiry(aws,c,**kw):
            if c['instance']==self.a['instance'] and kw.get('headroom')==80:raise ValueError('too little restart time')
            return original(aws,c,**kw)
        guard.status.side_effect=near_expiry
        with self.assertRaisesRegex(RuntimeError,'original association restored'):self.assign('ai')
        self.assertEqual(self.aws.address['InstanceId'],self.a['instance'])
        self.assertFalse(any(c[1]=='stop-instances' and c[-1]==self.a['instance'] for c in self.aws.calls))
        self.assertTrue(eip.resolve(inventory.context(self.aws,h.REGION,'ai'),h.REGION,self.aws,self.tls)['reachable'])

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
        with patch.object(acm,'prepare_gpu',side_effect=RuntimeError('guest failed')):
            with self.assertRaises(RuntimeError):self.assign('ai')
        self.assertEqual(self.aws.address,before)

    def test_unassociated_release_is_idempotent(self):
        self.assertIsNone(eip.release(self.aws,self.tls)['owner'])
        self.assertIsNone(eip.release(self.aws,self.tls)['owner'])
