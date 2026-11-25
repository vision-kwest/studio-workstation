"""Common named lifecycle contracts: real parser/render/dispatch, stateful AWS boundary."""
import contextlib
import copy
import datetime as dt
import io
import json
import subprocess
import unittest
from pathlib import Path
from unittest.mock import patch
from scripts import studio_workstation_cli as cli
from runtime import dcv_endpoint, independent_eip as eip, pr65_gpu_create as creator
from runtime import pr65_one_shot_guard as guard, issue43_hostname_handoff as h
from runtime import workstation_inventory as inventory
from workstation_fake import AWS, NOW, tags, missing, release_stopped_hostname


class CliFixture(unittest.TestCase):
    def setUp(self):
        self.aws=AWS()
        self.now=NOW
        self.templates=[]
        self.aws.groups['sg-0123456789abcdef0']={'GroupId':'sg-0123456789abcdef0','VpcId':cli.DEFAULT_VPC}
        real_run, real_create, real_status, real_rearm=cli.run, creator.create_guarded, guard.status, guard.rearm
        def run(args, **kw):
            if args[0]=='aws':
                self.assertEqual(args[1:3],['cloudformation','validate-template'])
                self.templates.append(json.loads(Path(args[args.index('--template-body')+1][7:]).read_text()))
                return '{}'
            return real_run(args,**kw)
        self.add(patch.object(cli,'aws',self.aws))
        self.add(patch.object(cli,'run',side_effect=run))
        self.add(patch.object(cli,'wait_gpu_bootstrap'))
        self.add(patch.object(cli,'verified_dcv_hostname',side_effect=lambda host,ip:'https://'+host+':8443'))
        self.add(patch.object(dcv_endpoint,'_probe'))
        self.add(patch.object(h,'check_gpu_tls'))
        self.add(patch.object(creator,'create_guarded',side_effect=lambda *a,**kw:real_create(*a,**{'utc_now':lambda:NOW,**kw})))
        self.add(patch.object(guard,'status',side_effect=lambda *a,**kw:real_status(*a,**{'now':self.now,**kw})))
        self.add(patch.object(guard,'rearm',side_effect=lambda *a,**kw:real_rearm(*a,**{'now':self.now,**kw})))

    def add(self,p):
        obj=p.start();self.addCleanup(p.stop);return obj

    def invoke(self,*argv):
        output=io.StringIO()
        self.errors=io.StringIO()
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(self.errors):cli.main(list(argv))
        return output.getvalue()

    def create(self,profile,name,*options):
        args=['create','--profile',profile,'--name',name]
        if profile=='publisher':args+=['--security-group-id','sg-0123456789abcdef0']
        self.invoke(*args,*options)
        return inventory.context(self.aws,h.REGION,name)


class NamedLifecycleTests(CliFixture):
    def test_desktop_software_defaults_opt_out_and_complete_override(self):
        approved=(cli.DEFAULT_REZ_VERSION_ID,cli.DEFAULT_REZ_SHA256)
        cases=[('default',[],approved),('off',['--no-studio-software'],None),
               ('override',['--rez-version-id','reviewed-v','--rez-sha256','a'*64],('reviewed-v','a'*64)),
               ('pinned',['--rez-version-id',approved[0],'--rez-sha256',approved[1]],approved)]
        for profile in ('cpu','gpu'):
            for mode,options,pins in cases:
                with self.subTest(profile=profile,mode=mode):
                    cli.wait_gpu_bootstrap.reset_mock()
                    name=profile+'-'+mode
                    self.invoke('create','--profile',profile,'--name',name,*options)
                    props=self.templates[-1]['Resources']['Workstation']['Properties']
                    if mode=='default':default_props=props
                    if mode=='pinned':self.assertEqual(props,default_props)
                    data=props['UserData']['Fn::Base64']['Fn::Sub']
                    self.assertIn('studio-software-setup.service',data)
                    self.assertIn('sg-058f2babb18747976',props['SecurityGroupIds'])
                    self.assertEqual('cat > /etc/studio-software-release.env' in data,pins is not None)
                    if pins:
                        for key,value in zip(('STUDIO_REZ_VERSION_ID','STUDIO_REZ_SHA256'),pins):
                            self.assertIn(key+'='+value+'\n',data)
                    self.assertIn('EfsClientAssociation',self.templates[-1]['Resources'])
                    cli.wait_gpu_bootstrap.assert_called_once()

    def test_invalid_software_options_never_mutate_aws(self):
        cases=[['--rez-version-id','reviewed-v'],['--rez-sha256','a'*64],
               ['--rez-version-id',''],['--rez-version-id','','--rez-sha256',''],
               ['--rez-version-id','','--rez-sha256','a'*64],
               ['--no-studio-software','--rez-version-id','reviewed-v'],
               ['--no-studio-software','--rez-version-id','reviewed-v','--rez-sha256','a'*64],
               ['--rez-version-id','bad;version','--rez-sha256','a'*64],
               ['--rez-version-id','reviewed-v','--rez-sha256','invalid']]
        for profile in ('cpu','gpu'):
            for options in cases:
                with self.subTest(profile=profile,options=options),self.assertRaises(SystemExit):
                    self.invoke('create','--profile',profile,*options)
        for options in (['--no-studio-software'],['--rez-version-id','reviewed-v','--rez-sha256','a'*64]):
            with self.subTest(publisher=options),self.assertRaises(SystemExit):
                self.invoke('create','--profile','publisher','--security-group-id','sg-0123456789abcdef0',*options)
        self.assertEqual(self.aws.calls,[])

    def test_profiles_and_profile_are_read_only_without_aws(self):
        self.assertEqual(set(json.loads(self.invoke('profiles'))['profiles']),{'cpu','gpu','publisher'})
        self.assertFalse(json.loads(self.invoke('profile','publisher'))['profiles']['publisher']['desktop'])
        self.assertEqual(self.aws.calls,[])

    def test_instance_type_must_match_profile_before_any_aws_call(self):
        for profile, instance_type in (('cpu', 'g4dn.2xlarge'), ('publisher', 'g4dn.2xlarge'),
                                       ('gpu', 'm7i.large'), ('gpu', 'g4dn.4xlarge')):
            with self.subTest(profile=profile, instance_type=instance_type), self.assertRaises(SystemExit):
                self.invoke('create', '--profile', profile, '--instance-type', instance_type)
        self.assertEqual(self.aws.calls, [])

    def test_generated_names_for_every_profile_are_unique(self):
        with patch.object(cli,'command') as dispatch:
            for profile in ('cpu','gpu','publisher'):
                for _ in range(2):self.invoke('create','--profile',profile)
            names=[c.args[0].stack for c in dispatch.call_args_list]
        self.assertEqual(len(set(names)),6)
        self.assertTrue(all(n.startswith('studio-') for n in names))

    def test_removed_flags_missing_or_conflicting_targets_are_rejected(self):
        for argv in [('create','--volume-cleanup-delay','45m'),('create','--cleanup-token','anything'),
                     ('create','--allow-public-dcv'),('stop',),('stop','a','--stack','b'),
                     ('hostname','release','a'),('hostname-return',),('destroy',),
                     ('destroy','a','--stack','b')]:
            with self.subTest(argv=argv),self.assertRaises(SystemExit):self.invoke(*argv)
        self.assertEqual(self.aws.writes,[])

    def test_two_gpu_and_cpu_names_select_their_own_stack_and_instance(self):
        a=self.create('gpu','art');b=self.create('gpu','ai');c=self.create('cpu','edit')
        self.assertEqual(len({x['stack_id'] for x in (a,b,c)}),3)
        self.assertEqual(len({x['instance'] for x in (a,b,c)}),3)
        self.assertEqual(self.aws.address.get('InstanceId'),a['instance'])
        self.assertEqual(sum(r['hostname_owner'] for r in json.loads(self.invoke('list'))),1)
        self.invoke('stop','art')
        self.assertEqual(self.aws.vms[a['instance']]['State']['Name'],'stopped')
        self.assertEqual(self.aws.vms[b['instance']]['State']['Name'],'running')
        self.assertEqual(json.loads(self.invoke('status','--stack',b['stack_id']))['instance'],b['instance'])
        rows=json.loads(self.invoke('list'))
        self.assertEqual({r['name'] for r in rows},{'art','ai','edit'})
        self.assertEqual(sum(r['hostname_owner'] for r in rows),0)

    def test_stop_start_preserves_disks_and_does_not_transfer_hostname(self):
        a=self.create('gpu','art');b=self.create('gpu','ai')
        self.invoke('hostname','assign','ai')
        owner=dict(self.aws.address)
        disks=list(self.aws.volumes)
        self.invoke('stop','art');self.invoke('start','art')
        self.assertEqual(list(self.aws.volumes),disks)
        self.assertEqual(self.aws.address,owner)
        self.assertNotIn(h.HOST,self.invoke('connect','art'))
        self.assertIn(h.HOST,self.invoke('connect','ai'))

    def test_create_does_not_steal_hostname_or_request_acm(self):
        a=self.create('cpu','edit')
        before=dict(self.aws.address);self.aws.calls=[]
        for profile in ('cpu','gpu'):
            result=json.loads(self.invoke('create','--profile',profile,'--name',profile+'-other'))
            self.assertNotIn(h.HOST,result['dcv_url'])
            self.assertIn('already assigned',self.errors.getvalue())
        self.assertEqual(self.aws.address,before)
        self.assertFalse(any(c[1] in ('put-role-policy','update-assume-role-policy','associate-address','disassociate-address') for c in self.aws.calls))

    def test_first_cpu_and_gpu_claim_free_hostname_and_reclaim_after_stop(self):
        for profile in ('cpu','gpu'):
            with self.subTest(profile=profile):
                name=profile+'-first'
                created=json.loads(self.invoke('create','--profile',profile,'--name',name))
                self.assertEqual(created['dcv_url'],'https://'+h.HOST+':8443')
                self.assertEqual(self.aws.address['InstanceId'],created['instance'])
                self.assertIn(h.HOST,self.invoke('connect',name))
                self.invoke('stop',name)
                self.assertIsNone(self.aws.address.get('InstanceId'))
                if profile=='cpu':self.aws.lookup(name)['StackStatus']='UPDATE_COMPLETE'
                started=json.loads(self.invoke('start',name))
                self.assertEqual(started['dcv_url'],created['dcv_url'])
                self.assertEqual(self.aws.address['InstanceId'],created['instance'])
                self.invoke('stop',name)

    def test_optional_hostname_failure_preserves_checked_desktop_on_create_and_start(self):
        with patch.object(eip.acm,'prepare_gpu',side_effect=RuntimeError('certificate unavailable')):
            for action in ('create','start'):
                with self.subTest(action=action):
                    args=('create','--profile','cpu','--name','desktop') if action=='create' else ('start','desktop')
                    result=json.loads(self.invoke(*args))
                    self.assertEqual(result['state'],'running')
                    self.assertNotIn(h.HOST,result['dcv_url'])
                    self.assertIn('hostname assign desktop',self.errors.getvalue())
                    self.assertIsNone(self.aws.address.get('InstanceId'))
                    self.assertFalse(any(c[1]=='stop-instances' for c in self.aws.calls))
                    self.assertIn(result['dcv_url'],self.invoke('connect','desktop'))
                    self.invoke('stop','desktop');self.aws.calls=[]

    def test_failed_endpoint_after_hostname_error_is_not_reported_as_ready(self):
        with (patch.object(eip,'assign',side_effect=RuntimeError('hostname unavailable')),
              patch.object(cli,'wait_dcv',side_effect=[{'url':'https://203.0.113.1:8443'},ValueError('DCV HTTPS unreachable')])):
            with self.assertRaises(SystemExit):self.create('cpu','failed')
        self.assertEqual(inventory.context(self.aws,h.REGION,'failed')['state'],'stopped')

    def test_publisher_and_dry_runs_never_attempt_hostname_assignment(self):
        with patch.object(eip,'assign') as assign:
            self.create('publisher','publisher-only')
            self.invoke('stop','publisher-only');self.invoke('start','publisher-only')
            for profile in ('cpu','gpu'):
                self.invoke('create','--profile',profile,'--dry-run')
        assign.assert_not_called()

    def test_failed_readiness_stops_only_the_selected_desktop(self):
        a=self.create('cpu','edit')
        cli.wait_gpu_bootstrap.side_effect=RuntimeError('not ready')
        with self.assertRaises(SystemExit):self.create('gpu','art')
        b=inventory.context(self.aws,h.REGION,'art')
        self.assertEqual(b['state'],'stopped')
        self.assertEqual(self.aws.vms[a['instance']]['State']['Name'],'running')
        self.assertEqual(len(self.aws.schedules),1)

    def test_missing_guard_blocks_restart_but_does_not_block_stop(self):
        a=self.create('gpu','art');self.aws.schedules.clear()
        self.invoke('stop','art');self.aws.calls=[]
        with self.assertRaises(SystemExit):self.invoke('start','art')
        self.assertFalse(any(c[1]=='start-instances' for c in self.aws.calls))

    def test_foreign_eip_does_not_prevent_cost_stopping(self):
        a=self.create('gpu','art')
        self.aws.address.update(InstanceId='i-0ffffffffffffffff',AssociationId='foreign',NetworkInterfaceId='foreign')
        self.invoke('stop','art')
        self.assertEqual(self.aws.vms[a['instance']]['State']['Name'],'stopped')
        self.assertEqual(self.aws.address['InstanceId'],'i-0ffffffffffffffff')

    def test_duplicate_name_never_updates_existing_workstation(self):
        self.create('gpu','art');self.aws.calls=[]
        with self.assertRaises(SystemExit):self.create('gpu','art')
        self.assertEqual(self.aws.writes,[])

    def test_quota_rollback_reports_resource_failure_and_safe_retry_to_cli(self):
        native_create, dispatch = self.aws.native_create, self.aws.dispatch
        reason = 'You have requested more vCPU capacity than your current vCPU limit of 8 allows.'
        for denied in (False, True):
            name = 'editor-' + ('denied' if denied else 'events')
            arn = f'arn:aws:cloudformation:{h.REGION}:{h.ACCOUNT}:stack/{name}/quota-failure'
            def rejected_create(stack, body, parameters, stack_tags):
                if stack != name:
                    return native_create(stack, body, parameters, stack_tags)
                self.aws.stacks[arn] = {'StackName': name, 'StackId': arn, 'StackStatus': 'DELETE_COMPLETE',
                                        'Tags': tags(stack_tags)}
                self.aws.resources[arn] = [{'LogicalResourceId': 'Workstation', 'ResourceType': 'AWS::EC2::Instance',
                                            'ResourceStatus': 'DELETE_COMPLETE', 'PhysicalResourceId': ''}]
                return {'StackId': arn}
            def failed_call(service, action, args):
                if service == 'cloudformation' and arn in args:
                    if action == 'wait':
                        raise subprocess.CalledProcessError(255, ['aws', 'cloudformation', 'wait'],
                                                            stderr='Waiter failed: DELETE_COMPLETE')
                    if action == 'describe-stack-events':
                        if denied:
                            raise RuntimeError('AccessDenied reading events')
                        return {'StackEvents': [{'LogicalResourceId': 'Workstation', 'ResourceType': 'AWS::EC2::Instance',
                                                 'ResourceStatus': 'CREATE_FAILED', 'ResourceStatusReason': reason,
                                                 'ResourceProperties': 'private-user-data'}]}
                return dispatch(service, action, args)
            with (self.subTest(events_denied=denied),
                  patch.object(self.aws, 'native_create', side_effect=rejected_create),
                  patch.object(self.aws, 'dispatch', side_effect=failed_call), self.assertRaises(SystemExit)):
                self.invoke('create', '--profile', 'gpu', '--name', name, '--instance-type', 'g4dn.2xlarge', '--expires', '4h')
            message = self.errors.getvalue()
            self.assertIn('CloudFormation=DELETE_COMPLETE, EC2=missing', message)
            self.assertIn('Waiter failed: DELETE_COMPLETE', message)
            self.assertEqual('Workstation: ' + reason in message, not denied)
            self.assertIn(f'python3 studio workstation destroy {name}', message)
            self.assertNotIn('--delete-stack', message)
            self.assertNotIn('preserved workstation', message)
            self.assertNotIn('private-user-data', message)
            self.assertFalse(self.aws.vms)
            self.assertFalse(any(c[1] in ('stop-instances', 'terminate-instances', 'delete-stack') for c in self.aws.calls))

    def test_dry_run_validates_without_creating_any_resource(self):
        for profile in ('cpu','gpu','publisher'):
            args=['create','--profile',profile,'--dry-run']
            if profile=='publisher':args+=['--security-group-id','sg-0123456789abcdef0']
            self.assertIn('DRY RUN',self.invoke(*args))
        self.assertEqual(self.aws.writes,[])

    def test_wrong_identity_or_profile_fails_before_mutation(self):
        for kind in ('stack-id','profile','instance-type','iam-profile'):
            with self.subTest(kind=kind):
                aws=AWS();c=aws.desktop('art');vm=aws.vms[c['instance']]
                if kind=='stack-id':next(t for t in vm['Tags'] if t['Key']=='aws:cloudformation:stack-id')['Value']='foreign'
                if kind=='profile':next(t for t in aws.stacks[c['stack_id']]['Tags'] if t['Key']=='StudioProfile')['Value']='cpu'
                if kind=='instance-type':vm['InstanceType']='unapproved'
                if kind=='iam-profile':vm['IamInstanceProfile']['Arn']='foreign'
                aws.calls=[]
                with patch.object(cli,'aws',aws),self.assertRaises(SystemExit):self.invoke('stop','art')
                self.assertEqual(aws.writes,[])

    def test_publisher_cf_lifecycle_has_no_dcv_or_implicit_promotion(self):
        p=self.create('publisher','publish')
        self.assertIsNone(p['dcv_url'])
        self.assertEqual([r['ResourceType'] for r in p['resources']],['AWS::EC2::Instance'])
        with self.assertRaises(SystemExit):self.invoke('connect','publish')
        with patch('runtime.publisher.time.sleep'):
            self.assertIn('Success',self.invoke('check-ready','publish'))
            with self.assertRaises(SystemExit):self.invoke('run','publish','--operation','build')
        self.invoke('stop','publish');self.invoke('start','publish')
        self.assertEqual(self.aws.address.get('InstanceId'),None)
        self.invoke('destroy','publish','--delete-stack')  # Legacy commands remain accepted.
        self.assertEqual(self.aws.volumes,{})
        self.assertIn('sg-0123456789abcdef0',self.aws.groups)


class StopWaiterRecoveryTests(CliFixture):
    @staticmethod
    def fail_stop_waiter(service, action, args):
        if service == 'ec2' and action == 'wait' and args[0] == 'instance-stopped':
            raise cli.subprocess.CalledProcessError(255, ['aws', 'ec2', 'wait'], stderr='injected stop timeout')

    def test_stopped_readback_and_retry_complete_only_owned_cleanup(self):
        for profile, mode in (('cpu', 'timeout'), ('gpu', 'timeout'),
                              ('gpu', 'already-stopped'), ('gpu', 'foreign')):
            with self.subTest(profile=profile, mode=mode):
                self.aws.before = None
                name = profile + '-' + mode
                selected = self.create(profile, name)
                self.invoke('hostname', 'assign', name)
                self.assertIn(selected['instance'], eip.acm._grants(self.aws)[0])
                if mode == 'foreign':
                    self.create('cpu', 'other-owner')
                    self.invoke('hostname', 'assign', 'other-owner')
                if mode == 'already-stopped':
                    self.aws.vms[selected['instance']]['State']['Name'] = 'stopped'
                disks, address = copy.deepcopy(self.aws.volumes), copy.deepcopy(self.aws.address)
                grants = eip.acm._grants(self.aws)
                self.aws.calls.clear()
                self.aws.before = self.fail_stop_waiter
                self.assertEqual(json.loads(self.invoke('stop', name))['state'], 'stopped')
                self.assertEqual(self.aws.volumes, disks)
                self.assertEqual(sum(c[1] == 'stop-instances' for c in self.aws.calls), mode != 'already-stopped')
                sessions, policies = eip.acm._grants(self.aws)
                self.assertNotIn(selected['instance'], sessions)
                self.assertNotIn(selected['instance'], policies)
                if mode == 'foreign':
                    self.assertEqual(self.aws.address, address)
                    self.assertEqual((sessions, policies), grants)
                else:
                    self.assertIsNone(self.aws.address.get('InstanceId'))

    def test_unconfirmed_or_replaced_instance_never_releases_hostname(self):
        for mode in ('stopping', 'running', 'read-failure', 'replacement'):
            with self.subTest(mode=mode):
                self.aws.before = None
                selected = self.create('cpu', mode)
                self.invoke('hostname', 'assign', mode)
                address, grants = copy.deepcopy(self.aws.address), eip.acm._grants(self.aws)
                self.aws.calls.clear()
                def fail_readback(*args):
                    raise cli.subprocess.CalledProcessError(254, ['aws'], stderr='injected readback failure')
                def fault(service, action, args):
                    if service != 'ec2' or action != 'wait':
                        return
                    vm = self.aws.vms[selected['instance']]
                    if mode in ('stopping', 'running'):
                        vm['State']['Name'] = mode
                    if mode == 'replacement':
                        replacement = copy.deepcopy(vm)
                        replacement['InstanceId'] = 'i-0ffffffffffffffff'
                        self.aws.vms[replacement['InstanceId']] = replacement
                        resources = self.aws.resources[selected['stack_id']]
                        next(r for r in resources if r['LogicalResourceId'] == 'Workstation')['PhysicalResourceId'] = replacement['InstanceId']
                    self.aws.before = fail_readback if mode == 'read-failure' else None
                    self.fail_stop_waiter(service, action, args)
                self.aws.before = fault
                with patch.object(eip, 'release_if_owned', wraps=eip.release_if_owned) as cleanup, self.assertRaises(SystemExit):
                    self.invoke('stop', mode)
                cleanup.assert_not_called()
                self.aws.before = None
                self.assertEqual(self.aws.address, address)
                self.assertEqual(eip.acm._grants(self.aws), grants)
                self.assertEqual([c[1] for c in self.aws.writes], ['stop-instances'])

    def test_stop_request_and_start_waiter_failures_still_propagate(self):
        for command, operation in (('stop', 'stop-instances'), ('start', 'wait')):
            with self.subTest(command=command):
                self.aws.before = None
                selected = self.create('cpu', command)
                if command == 'start':
                    self.invoke('stop', command)
                def fail(service, action, args):
                    if service == 'ec2' and action == operation:
                        if command == 'start':
                            self.aws.vms[selected['instance']]['State']['Name'] = 'stopped'
                        raise cli.subprocess.CalledProcessError(254, ['aws'], stderr='injected original failure')
                self.aws.before = fail
                with (patch.object(eip, 'release_if_owned') as cleanup,
                      patch.object(cli, 'ready_desktop', return_value={'url': 'https://example.test'}) as ready,
                      self.assertRaises(SystemExit)):
                    self.invoke(command, command)
                cleanup.assert_not_called()
                ready.assert_not_called()
                self.assertIn('injected original failure', self.errors.getvalue())


class AutoStopLifecycleTests(CliFixture):
    def test_manual_stop_completes_when_background_release_wins_the_detach(self):
        selected = self.create('gpu', 'art')
        volumes = copy.deepcopy(self.aws.volumes)
        raced = []
        def release_first(service, action, args):
            if action == 'disassociate-address' and not raced:
                raced.append(True)
                with contextlib.redirect_stdout(io.StringIO()):  # Lambda logs are separate from CLI stdout.
                    release_stopped_hostname(self.aws)
                raise missing('InvalidAssociationID.NotFound', action)
        self.aws.before = release_first; self.aws.calls.clear()
        stopped = json.loads(self.invoke('stop', 'art'))
        self.assertTrue(raced)
        self.assertEqual((stopped['instance'], stopped['state']), (selected['instance'], 'stopped'))
        self.assertEqual(self.aws.volumes, volumes)
        self.assertIsNone(self.aws.address.get('InstanceId'))
        self.assertEqual(eip.acm._grants(self.aws)[0], set())
        self.assertFalse(any(c[1] in ('associate-address', 'start-instances', 'terminate-instances', 'delete-stack', 'delete-volume') for c in self.aws.writes))

    def test_create_and_start_accept_minutes_hours_days_without_arbitrary_caps(self):
        with patch.object(cli, 'command') as dispatch:
            for value, expected in (('1m', 1), ('59m', 59), ('480m', 480), ('8h', 480), ('2d', 2880), ('10000m', 10000)):
                for command in ('create', 'start'):
                    with self.subTest(value=value, command=command):
                        args = [command, '--name', 'art', '--expires-in', value]
                        if command == 'create':
                            args += ['--profile', 'gpu']
                        self.invoke(*args)
                        self.assertEqual(dispatch.call_args.args[0].expires_in, expected)
            self.invoke('extend-expiration', 'art', '--additional-minutes', '10000')
            self.assertEqual(dispatch.call_args.args[0].additional_minutes, 10000)
        self.assertFalse(self.aws.calls)

    def test_invalid_duration_syntax_never_reaches_aws(self):
        for value in ('0m', '-1m', '0h', '1.5h', 'foo', '1', '1w', '1h30m'):
            for command in ('create', 'start'):
                args = [command, '--name', 'art', '--expires-in', value]
                if command == 'create':
                    args += ['--profile', 'gpu']
                with self.subTest(value=value, command=command), self.assertRaises(SystemExit):
                    self.invoke(*args)
        self.assertFalse(self.aws.calls)

    def test_timeout_preserves_stack_disk_and_saved_data_then_start_rearms_before_compute(self):
        created = json.loads(self.invoke('create', '--profile', 'gpu', '--name', 'art', '--expires-in', '8h'))
        self.assertEqual(created['dcv_url'], 'https://' + h.HOST + ':8443')
        self.assertEqual(created['expiration']['duration_minutes'], 480)
        selected = inventory.context(self.aws, h.REGION, 'art')
        name = 'studio-stop-' + selected['token']
        owner = copy.deepcopy(self.aws.address)
        grants = eip.acm._grants(self.aws)
        volume_id = selected['vm']['BlockDeviceMappings'][0]['Ebs']['VolumeId']
        self.aws.volumes[volume_id]['saved_file_sha256'] = 'saved-work-sentinel'
        disk = copy.deepcopy(self.aws.volumes[volume_id])
        self.now = NOW + dt.timedelta(minutes=481)
        self.aws.fire_schedule(name)
        release_stopped_hostname(self.aws)
        stopped = json.loads(self.invoke('status', 'art'))
        self.assertEqual(stopped['state'], 'stopped')
        self.assertEqual(stopped['expiration']['status'], 'expired')
        self.assertIsNone(self.aws.address.get('InstanceId'))
        self.assertIn({'Key': 'StudioDcvInstanceId', 'Value': selected['instance']}, self.aws.address['Tags'])
        self.assertEqual(eip.acm._grants(self.aws), grants)
        self.aws.calls.clear()
        contender = json.loads(self.invoke('create', '--profile', 'cpu', '--name', 'edit'))
        self.assertIn(h.HOST, contender['dcv_url'])
        self.invoke('stop', 'edit')
        restarted = json.loads(self.invoke('start', 'edit'))
        self.assertIn(h.HOST, restarted['dcv_url'])
        self.assertEqual(self.aws.address['InstanceId'], contender['instance'])
        self.assertEqual(eip.acm._grants(self.aws)[0], {contender['instance']})
        self.invoke('stop', 'edit')
        self.aws.calls.clear()
        started = json.loads(self.invoke('start', 'art', '--expires-in', '12h'))
        self.assertEqual(started['dcv_url'], created['dcv_url'])
        self.assertEqual(started['expiration']['duration_minutes'], 720)
        self.assertIn(h.HOST, self.invoke('connect', 'art'))
        self.assertEqual(self.aws.address, owner)
        self.assertTrue(any(c[1] == 'associate-address' for c in self.aws.calls))
        self.assertEqual(self.aws.volumes[volume_id], disk)
        fresh = inventory.context(self.aws, h.REGION, 'art')
        self.assertEqual((fresh['stack_id'], fresh['instance'], fresh['state']),
                         (selected['stack_id'], selected['instance'], 'running'))
        status = guard.status(self.aws, fresh)
        self.assertEqual(status['duration_minutes'], 720)
        self.assertEqual(dt.datetime.fromisoformat(status['stop_at']), self.now + dt.timedelta(minutes=720))
        update = next(i for i, c in enumerate(self.aws.calls) if c[:2] == ('scheduler', 'update-schedule'))
        start = next(i for i, c in enumerate(self.aws.calls) if c[:2] == ('ec2', 'start-instances'))
        self.assertLess(update, start)
        self.assertTrue(any(c[:2] == ('scheduler', 'get-schedule') for c in self.aws.calls[update + 1:start]))
        self.assertFalse(any(c[1] in ('delete-stack', 'terminate-instances', 'delete-volume') for c in self.aws.calls))
        self.invoke('stop', 'art')
        self.assertIsNone(self.aws.address.get('InstanceId'))
        self.now += dt.timedelta(days=1)
        self.invoke('start', 'art')
        self.assertEqual(dt.datetime.fromisoformat(guard.status(self.aws, fresh)['stop_at']), self.now + dt.timedelta(minutes=720))

    def test_failed_timer_rearm_never_starts_compute_or_deletes_work(self):
        selected = self.create('gpu', 'art')
        self.invoke('stop', 'art')
        disk = copy.deepcopy(self.aws.volumes)
        updated = []
        def after(service, action, args, result):
            if (service, action) == ('scheduler', 'update-schedule'):
                updated.append(True)
        def before(service, action, args):
            if (service, action) == ('scheduler', 'get-schedule') and updated:
                raise RuntimeError('cannot verify timer')
        self.aws.before, self.aws.after = before, after
        self.aws.calls.clear()
        with self.assertRaises(SystemExit):
            self.invoke('start', 'art', '--expires-in', '8h')
        self.assertEqual(self.aws.vms[selected['instance']]['State']['Name'], 'stopped')
        self.assertEqual(self.aws.volumes, disk)
        self.assertFalse(any(c[1] in ('start-instances', 'terminate-instances', 'delete-stack') for c in self.aws.calls))

    def test_disabled_legacy_deployment_is_visible_and_not_implicitly_migrated(self):
        selected = self.aws.legacy_desktop('legacy')
        before = copy.deepcopy(self.aws.schedules)
        self.aws.calls.clear()
        status = json.loads(self.invoke('status', 'legacy'))
        self.assertEqual(status['expiration']['status'], 'legacy')
        self.assertFalse(self.aws.writes)
        self.invoke('stop', 'legacy')
        self.aws.calls.clear()
        with self.assertRaises(SystemExit):
            self.invoke('start', 'legacy', '--expires-in', '8h')
        self.assertFalse(self.aws.writes)
        self.assertEqual(self.aws.schedules, before)
        self.assertEqual(self.aws.vms[selected['instance']]['State']['Name'], 'stopped')


class ProfileAutoStopTests(CliFixture):
    def test_every_profile_supports_four_hour_stop_extension_restart_and_destroy(self):
        cases = [(profile, None) for profile in inventory.PROFILES] + [('gpu', 'g4dn.2xlarge')]
        for profile, instance_type in cases:
            with self.subTest(profile=profile, instance_type=instance_type):
                self.now = NOW
                name = 'ayo' if profile == 'cpu' else profile + '-timer' + ('-2x' if instance_type else '')
                self.aws.calls.clear()
                options = ('--instance-type', instance_type) if instance_type else ()
                selected = self.create(profile, name, '--expires-in', '4h', *options)
                self.assertEqual(selected['profile'], profile)
                expected_type = instance_type or ('g4dn.xlarge' if profile == 'gpu' else
                                                  inventory.PROFILES[profile]['instance_types'][0])
                self.assertEqual(selected['vm']['InstanceType'], expected_type)
                listed = next(r for r in json.loads(self.invoke('list')) if r['name'] == name)
                self.assertEqual((listed['profile'], listed['instance'], listed['state']),
                                 (profile, selected['instance'], 'running'))
                if profile == 'publisher':
                    self.assertIsNone(selected['dcv_url'])
                    self.assertFalse(any(c[0] == 'ssm' or c[1] in ('associate-address', 'disassociate-address')
                                         for c in self.aws.calls))
                status = json.loads(self.invoke('status', name))
                self.assertEqual(status['expiration']['duration_minutes'], 240)
                timer = 'studio-stop-' + selected['token']
                spec = self.aws.schedules[timer]
                self.assertEqual(spec['Target']['Arn'], guard.STOP)
                self.assertEqual(json.loads(spec['Target']['Input']), {'InstanceIds': [selected['instance']]})
                with patch.object(guard, '_utc', side_effect=lambda value=None: self.now if value is None else value):
                    extended = json.loads(self.invoke('extend-expiration', name, '--additional-minutes', '60'))
                self.assertEqual(dt.datetime.fromisoformat(extended['proposed_stop_utc']), NOW + dt.timedelta(hours=5))
                disk = selected['vm']['BlockDeviceMappings'][0]['Ebs']['VolumeId']
                self.aws.volumes[disk]['saved_file_sha256'] = 'saved-work'
                volumes = copy.deepcopy(self.aws.volumes)
                self.now += dt.timedelta(hours=6)
                self.aws.fire_schedule(timer)
                release_stopped_hostname(self.aws)
                stopped = json.loads(self.invoke('status', name))
                self.assertEqual((stopped['state'], stopped['expiration']['status']), ('stopped', 'expired'))
                self.assertIsNone(self.aws.address.get('InstanceId'))
                self.assertEqual(self.aws.volumes, volumes)
                self.assertEqual(self.aws.stacks[selected['stack_id']]['StackStatus'], 'CREATE_COMPLETE')
                for options, minutes in (((), 240), (('--expires-in', '8h'), 480)):
                    self.aws.calls.clear()
                    started = json.loads(self.invoke('start', name, *options))
                    self.assertEqual((started['state'], started['instance']), ('running', selected['instance']))
                    self.assertEqual(started['expiration']['duration_minutes'], minutes)
                    self.assertEqual(dt.datetime.fromisoformat(started['expiration']['stop_at']),
                                     self.now + dt.timedelta(minutes=minutes))
                    update = next(i for i, c in enumerate(self.aws.calls) if c[:2] == ('scheduler', 'update-schedule'))
                    start = next(i for i, c in enumerate(self.aws.calls) if c[:2] == ('ec2', 'start-instances'))
                    self.assertLess(update, start)
                    self.assertTrue(any(c[:2] == ('scheduler', 'get-schedule') for c in self.aws.calls[update + 1:start]))
                    self.assertEqual(self.aws.volumes, volumes)
                    self.invoke('stop', name)
                    self.now += dt.timedelta(days=1)
                self.assertFalse(any(c[1] in ('delete-stack', 'terminate-instances', 'delete-volume') for c in self.aws.calls))
                workload = self.aws.stacks.pop(selected['stack_id'])
                try:
                    orphan = next(r for r in json.loads(self.invoke('list')) if r['name'] == name)
                    self.assertEqual((orphan['state'], orphan['profile']), ('safety-stack-only', profile))
                finally:
                    self.aws.stacks[selected['stack_id']] = workload
                self.invoke('destroy', name)
                self.assertNotIn(timer, self.aws.schedules)
                self.assertNotIn(disk, self.aws.volumes)
                self.assertNotIn(spec['Target']['RoleArn'].rsplit('/', 1)[-1], self.aws.roles)
                self.assertEqual(self.aws.stacks[selected['safety_stack']]['StackStatus'], 'DELETE_COMPLETE')

    def test_missing_timer_or_association_blocks_restart_for_every_profile(self):
        for profile in inventory.PROFILES:
            selected = self.create(profile, profile + '-missing', '--expires-in', '4h')
            self.invoke('stop', selected['stack'])
            timer = 'studio-stop-' + selected['token']
            spec = self.aws.schedules[timer]
            stack = self.aws.stacks[selected['stack_id']]
            original_tags = copy.deepcopy(stack['Tags'])
            volumes = copy.deepcopy(self.aws.volumes)
            for missing in ('schedule', 'StudioSafetyStack', 'StudioCleanupToken'):
                with self.subTest(profile=profile, missing=missing):
                    if missing == 'schedule':
                        del self.aws.schedules[timer]
                    else:
                        stack['Tags'] = [t for t in original_tags if t['Key'] != missing]
                    self.aws.calls.clear()
                    with self.assertRaises(SystemExit):
                        self.invoke('start', selected['stack'])
                    self.assertFalse(self.aws.writes)
                    self.assertEqual(self.aws.volumes, volumes)
                    self.aws.schedules[timer], stack['Tags'] = spec, copy.deepcopy(original_tags)

    def test_untimed_cpu_and_publisher_preserve_their_existing_start_behavior(self):
        for profile in ('cpu', 'publisher'):
            selected = self.create(profile, profile + '-untimed')
            self.assertNotIn('expiration', json.loads(self.invoke('status', selected['stack'])))
            self.invoke('stop', selected['stack'])
            self.aws.calls.clear()
            with self.assertRaises(SystemExit):
                self.invoke('start', selected['stack'], '--expires-in', '4h')
            self.assertFalse(self.aws.writes)
            self.assertEqual(json.loads(self.invoke('start', selected['stack']))['state'], 'running')
            self.assertFalse(self.aws.schedules)

    def test_timed_cpu_hostname_recovery_rearms_before_restarting_compute(self):
        selected = self.create('cpu', 'timed-owner', '--expires-in', '4h')
        self.create('cpu', 'next-owner')
        self.aws.restore_public_ip = False
        self.aws.calls.clear()
        reachable = eip._reachable
        with patch.object(eip, '_reachable', side_effect=lambda aws, c, tls, **kw:
                          reachable(aws, c, tls, sleep=lambda _: None, **kw)):
            result = json.loads(self.invoke('hostname', 'assign', 'next-owner'))
        self.assertTrue(result['previous_endpoint']['reachable'])
        update = next(i for i, c in enumerate(self.aws.calls) if c[:2] == ('scheduler', 'update-schedule'))
        start = next(i for i, c in enumerate(self.aws.calls) if c[:2] == ('ec2', 'start-instances'))
        self.assertLess(update, start)
        self.assertEqual(self.aws.calls[start][-1], selected['instance'])
