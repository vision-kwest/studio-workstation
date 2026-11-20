"""Common named lifecycle contracts: real parser/render/dispatch, stateful AWS boundary."""
import contextlib
import copy
import datetime as dt
import io
import json
import unittest
from pathlib import Path
from unittest.mock import patch
from scripts import studio_workstation_cli as cli
from runtime import dcv_endpoint, independent_eip as eip, pr65_gpu_create as creator
from runtime import pr65_one_shot_guard as guard, issue43_hostname_handoff as h
from runtime import workstation_inventory as inventory
from workstation_fake import AWS, NOW, tags


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
        self.add(patch.object(cli,'install_efs_client'))
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
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(io.StringIO()):cli.main(list(argv))
        return output.getvalue()

    def create(self,profile,name):
        args=['create','--profile',profile,'--name',name]
        if profile=='publisher':args+=['--security-group-id','sg-0123456789abcdef0']
        self.invoke(*args)
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
                    cli.install_efs_client.reset_mock()
                    cli.wait_gpu_bootstrap.reset_mock()
                    cli.install_efs_client.side_effect=lambda *_:cli.wait_gpu_bootstrap.assert_called_once()
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
                    selected=inventory.context(self.aws,h.REGION,name)
                    cli.install_efs_client.assert_called_once_with(selected['instance'],h.REGION)

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
        cli.install_efs_client.assert_not_called()

    def test_software_helper_failure_keeps_desktop_available(self):
        cli.install_efs_client.side_effect=RuntimeError('SSM unavailable')
        errors=io.StringIO()
        with contextlib.redirect_stdout(io.StringIO()),contextlib.redirect_stderr(errors):
            cli.main(['create','--name','desktop'])
        self.assertIn('desktop is available',errors.getvalue())
        self.assertEqual(inventory.context(self.aws,h.REGION,'desktop')['state'],'running')
        self.assertFalse(any(c[1]=='stop-instances' for c in self.aws.calls))

    def test_profiles_and_profile_are_read_only_without_aws(self):
        self.assertEqual(set(json.loads(self.invoke('profiles'))['profiles']),{'cpu','gpu','publisher'})
        self.assertFalse(json.loads(self.invoke('profile','publisher'))['profiles']['publisher']['desktop'])
        self.assertEqual(self.aws.calls,[])

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
                     ('hostname','release','a'),('hostname-return',),('destroy','a')]:
            with self.subTest(argv=argv),self.assertRaises(SystemExit):self.invoke(*argv)
        self.assertEqual(self.aws.writes,[])

    def test_two_gpu_and_cpu_names_select_their_own_stack_and_instance(self):
        a=self.create('gpu','art');b=self.create('gpu','ai');c=self.create('cpu','edit')
        self.assertEqual(len({x['stack_id'] for x in (a,b,c)}),3)
        self.assertEqual(len({x['instance'] for x in (a,b,c)}),3)
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
        a=self.create('cpu','edit');self.invoke('hostname','assign','edit')
        before=dict(self.aws.address);self.aws.calls=[]
        self.create('gpu','art')
        self.assertEqual(self.aws.address,before)
        self.assertFalse(any(c[1] in ('put-role-policy','update-assume-role-policy','associate-address','disassociate-address') for c in self.aws.calls))

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

    def test_dry_run_validates_without_creating_any_resource(self):
        for profile in ('cpu','gpu','publisher'):
            args=['create','--profile',profile,'--dry-run']
            if profile=='publisher':args+=['--security-group-id','sg-0123456789abcdef0']
            self.assertIn('DRY RUN',self.invoke(*args))
        self.assertEqual(self.aws.writes,[])
        cli.install_efs_client.assert_not_called()

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
        cli.install_efs_client.assert_not_called()
        self.assertIsNone(p['dcv_url'])
        self.assertEqual([r['ResourceType'] for r in p['resources']],['AWS::EC2::Instance'])
        with self.assertRaises(SystemExit):self.invoke('connect','publish')
        with patch('runtime.publisher.time.sleep'):
            self.assertIn('Success',self.invoke('check-ready','publish'))
            with self.assertRaises(SystemExit):self.invoke('run','publish','--operation','build')
        self.invoke('stop','publish');self.invoke('start','publish')
        self.assertEqual(self.aws.address.get('InstanceId'),None)
        self.invoke('destroy','publish','--delete-stack')
        self.assertEqual(self.aws.volumes,{})
        self.assertIn('sg-0123456789abcdef0',self.aws.groups)


class AutoStopLifecycleTests(CliFixture):
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
        self.invoke('create', '--profile', 'gpu', '--name', 'art', '--expires-in', '8h')
        selected = inventory.context(self.aws, h.REGION, 'art')
        name = 'studio-stop-' + selected['token']
        volume_id = selected['vm']['BlockDeviceMappings'][0]['Ebs']['VolumeId']
        self.aws.volumes[volume_id]['saved_file_sha256'] = 'saved-work-sentinel'
        disk = copy.deepcopy(self.aws.volumes[volume_id])
        self.now = NOW + dt.timedelta(minutes=481)
        self.aws.fire_schedule(name)
        stopped = json.loads(self.invoke('status', 'art'))
        self.assertEqual(stopped['state'], 'stopped')
        self.assertEqual(stopped['expiration']['status'], 'expired')
        self.aws.calls.clear()
        self.invoke('start', 'art', '--expires-in', '12h')
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
