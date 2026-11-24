"""Stateful AWS contract fixture; never contacts AWS. Faults are injected explicitly."""
from copy import deepcopy
import datetime as dt
import json
import re
import subprocess
import uuid
from pathlib import Path

from runtime import issue43_hostname_handoff as h
from runtime import issue43_gpu_acm_setup as acm
from runtime import pr65_gpu_create as creator
from runtime import pr65_one_shot_guard as guard
from runtime import workstation_inventory as inventory

ROOT = Path(__file__).resolve().parents[1]
NOW = dt.datetime(2026, 11, 16, 18, tzinfo=dt.timezone.utc)


def missing(code, operation):
    return subprocess.CalledProcessError(255, ['aws', operation], stderr=f'An error occurred ({code}): does not exist')


def tagdict(items):
    return {item['Key']: item['Value'] for item in items}


def tags(values):
    return [{'Key': key, 'Value': value} for key, value in values.items()]


def resolve(value, values):
    if isinstance(value, list):
        return [resolve(v, values) for v in value]
    if not isinstance(value, dict):
        return value
    if set(value) == {'Ref'}:
        return values[value['Ref']]
    if set(value) == {'Fn::Sub'}:
        template = value['Fn::Sub']
        if isinstance(template, list):
            template, substitutions = template
            values = {**values, **resolve(substitutions, values)}
        return re.sub(r'\$\{([^}]+)\}', lambda m: str(values.get(m[1], m[0])), template)
    if set(value) == {'Fn::Split'}:
        separator, text = resolve(value['Fn::Split'], values)
        return text.split(separator)
    if set(value) == {'Fn::Select'}:
        index, items = resolve(value['Fn::Select'], values)
        return items[index]
    if set(value) == {'Fn::GetAtt'}:
        return values['.'.join(value['Fn::GetAtt'])]
    return {key: resolve(v, values) for key, v in value.items()}


def release_stopped_hostname(aws):
    """Execute the deployed inline handler; adapt only its SDK calls to this fixture."""
    from functools import partial
    from types import SimpleNamespace
    from unittest.mock import patch
    template = json.loads((ROOT / 'infra/workstation-hostname-stop-release.json').read_text())
    properties = template['Resources']['ReleaseFunction']['Properties']
    parameters = {'AllocationIds': '--allocation-ids', 'InstanceIds': '--instance-ids',
                  'AssociationId': '--association-id', 'StackName': '--stack-name',
                  'LogicalResourceId': '--logical-resource-id'}
    def call(service, operation, **values):
        args = []
        for key, value in values.items():
            args.extend([parameters[key], *(value if isinstance(value, list) else [value])])
        return aws(h.REGION, service, operation.replace('_', '-'), *args)
    operations = {'ec2': ('describe_addresses', 'describe_instances', 'disassociate_address'),
                  'cloudformation': ('describe_stack_resource',)}
    clients = {service: SimpleNamespace(**{op: partial(call, service, op) for op in names})
               for service, names in operations.items()}
    env = resolve(properties['Environment']['Variables'], {**{k: v['Default'] for k, v in template['Parameters'].items()}, 'AWS::Region': h.REGION,
                  'AWS::AccountId': h.ACCOUNT, 'AWS::Partition': 'aws'})
    separator, lines = properties['Code']['ZipFile']['Fn::Join']
    with patch.dict('os.environ', env), patch('boto3.client', side_effect=lambda service: clients[service]):
        namespace = {}
        exec(compile(separator.join(lines), 'deployed-hostname-release.py', 'exec'), namespace)
        return namespace['handler']({}, None)


class AWS:
    def __init__(self):
        self.calls = []
        self.stacks, self.resources, self.vms, self.volumes, self.groups = {}, {}, {}, {}, {}
        self.roles, self.policies, self.schedules = {}, {}, {}
        self.before = self.after = None
        self.serial = 0
        self.restore_public_ip = True
        self.keep_volume = False
        self.address = {'AllocationId': h.EIP_ID, 'PublicIp': h.EIP_IP,
                        'Tags': tags({'StudioIssue': '43', 'StudioDcvHostname': h.HOST,
                                      'StudioOwnerStack': 'studio-independent-dcv'})}
        self.roles[acm.BASE_ROLE] = {'Arn': acm.SOURCE_ARN, 'RoleId': acm.SOURCE_ID}
        self.roles[acm.EXPORT_NAME] = {'Arn': h.EXPORT_ROLE, 'AssumeRolePolicyDocument': {
            'Version': '2012-10-17', 'Statement': [{'Effect': 'Deny',
            'Principal': {'AWS': acm.SOURCE_ARN}, 'Action': 'sts:AssumeRole'}]}}
        self.policies[acm.BASE_ROLE] = {'UnrelatedEfsRead': {'Version': '2012-10-17', 'Statement': []}}
        self.policies[acm.EXPORT_NAME] = {'StudioIssue43ExactAcMExport': {'Version': '2012-10-17',
            'Statement': [{'Sid': 'ExportOnlyOneDcvCertificate', 'Effect': 'Allow',
                          'Action': 'acm:ExportCertificate', 'Resource': h.CERT_ARN}]}}

    def __call__(self, region, service, action, *args):
        assert region == h.REGION, region
        self.calls.append((service, action, *args))
        if self.before:
            self.before(service, action, args)
        result = self.dispatch(service, action, args)
        if self.after:
            self.after(service, action, args, result)
        return deepcopy(result)

    @property
    def writes(self):
        prefixes = ('create-', 'update-', 'delete-', 'put-', 'start-', 'stop-', 'terminate-', 'associate-', 'disassociate-')
        return [c for c in self.calls if c[1].startswith(prefixes)]

    def lookup(self, selector):
        if selector in self.stacks:
            return self.stacks[selector]
        values = [s for s in self.stacks.values() if s['StackName'] == selector and s['StackStatus'] != 'DELETE_COMPLETE']
        if len(values) != 1:
            raise missing('ValidationError', 'describe-stacks')
        return values[0]

    def native_create(self, name, body, parameters, stack_tags):
        if any(s['StackName'] == name and s['StackStatus'] != 'DELETE_COMPLETE' for s in self.stacks.values()):
            raise missing('AlreadyExistsException', 'create-stack')
        self.serial += 1
        n = self.serial
        arn = f'arn:aws:cloudformation:{h.REGION}:{h.ACCOUNT}:stack/{name}/{uuid.UUID(int=n)}'
        params = {k: v['Default'] for k, v in body.get('Parameters', {}).items() if 'Default' in v}
        params.update(parameters)
        values = {**params, 'AWS::StackId': arn, 'AWS::StackName': name,
                  'AWS::AccountId': h.ACCOUNT, 'AWS::Region': h.REGION, 'AWS::Partition': 'aws'}
        stack = {'StackName': name, 'StackId': arn, 'StackStatus': 'CREATE_COMPLETE',
                 'Tags': tags(stack_tags), 'Parameters': [{'ParameterKey': k, 'ParameterValue': str(v)} for k,v in params.items()]}
        self.stacks[arn], self.resources[arn] = stack, []
        for key, resource in body['Resources'].items():
            kind = resource['Type']
            if kind == 'AWS::IAM::Role':
                physical = f'{name}-ExpirationRole-{n}'
                role_arn = f'arn:aws:iam::{h.ACCOUNT}:role/{physical}'
                values[key + '.Arn'] = role_arn
                props = resolve(resource['Properties'], values)
                self.roles[physical] = {'Arn': role_arn, 'AssumeRolePolicyDocument': props['AssumeRolePolicyDocument']}
                self.policies[physical] = {p['PolicyName']: p['PolicyDocument'] for p in props['Policies']}
            elif kind == 'AWS::EC2::SecurityGroup':
                physical = 'sg-' + f'{n:017x}'
                props = resolve(resource['Properties'], values)
                self.groups[physical] = {'GroupId': physical, 'VpcId': 'vpc-01bbe5d01838f70b2',
                                        'Tags': tags({**stack_tags, **tagdict(props.get('Tags', [])), 'aws:cloudformation:stack-id': arn})}
            elif kind == 'AWS::EC2::Instance':
                physical = 'i-' + f'{n:017x}'
                props = resolve(resource['Properties'], values)
                instance_tags = {**stack_tags, **tagdict(props['Tags']), 'aws:cloudformation:stack-id': arn,
                                 'aws:cloudformation:logical-id': key}
                vid = 'vol-' + f'{n:017x}'
                ip = f'203.0.113.{n}'
                self.vms[physical] = {'InstanceId': physical, 'State': {'Name':'running'}, 'ImageId': props['ImageId'],
                    'InstanceType': props['InstanceType'], 'VpcId': 'vpc-01bbe5d01838f70b2',
                    'IamInstanceProfile': {'Arn': f'arn:aws:iam::{h.ACCOUNT}:instance-profile/{props["IamInstanceProfile"]}'},
                    'Tags': tags(instance_tags), 'PublicIpAddress': ip,
                    'NetworkInterfaces': [{'NetworkInterfaceId': 'eni-' + f'{n:017x}', 'Attachment': {'DeviceIndex': 0},
                                           'PrivateIpAddresses': [{'Primary':True, 'PrivateIpAddress': f'172.31.0.{n}'}]}],
                    'BlockDeviceMappings': [{'DeviceName': '/dev/sda1', 'Ebs': {'VolumeId': vid,
                        'DeleteOnTermination': props['BlockDeviceMappings'][0]['Ebs']['DeleteOnTermination']}}]}
                self.volumes[vid] = {'VolumeId':vid, 'State':'in-use', 'Tags': tags(instance_tags),
                                    'Attachments':[{'InstanceId':physical, 'Device':'/dev/sda1'}]}
            elif kind == 'AWS::Scheduler::Schedule':
                props = resolve(resource['Properties'], values)
                assert 'Tags' not in props, 'AWS::Scheduler::Schedule does not accept Tags'
                physical = props['Name']
                props.setdefault('ActionAfterCompletion', 'NONE')
                self.schedules[physical] = props
                values[key + '.Arn'] = f'arn:aws:scheduler:{h.REGION}:{h.ACCOUNT}:schedule/{props["GroupName"]}/{physical}'
            else:
                raise AssertionError(kind)
            values[key] = physical
            self.resources[arn].append({'LogicalResourceId':key, 'PhysicalResourceId':physical,
                'ResourceType':kind, 'ResourceStatus':'CREATE_COMPLETE', 'StackId':arn, 'StackName':name})
        stack['Outputs'] = [{'OutputKey':key, 'OutputValue':resolve(v['Value'],values)} for key,v in body.get('Outputs',{}).items()]
        return {'StackId':arn}

    def desktop(self, name='gpu-a', profile='gpu', *, minutes=150, utc_now=None):
        # Actual renderer and native safety template; no alternate production implementation.
        import tempfile, sys
        from unittest.mock import patch
        from runtime.render_workstation import main
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)/'workstation.json'
            argv = ['render','--profile',profile,'--bucket','studio-workstation-releases-401233741942-us-east-1',
                    '--prefix','releases/issue168-nvidia-dkms-r2-20261123/res-2026.06','--output',str(path)]
            if profile=='gpu':
                argv += ['--lifecycle','persistent']
            with patch.object(sys,'argv',argv):
                main()
            params = ['ImageId=ami-0d6dd9e8265d48906','InstanceType='+inventory.PROFILES[profile]['instance_types'][0],
                      'InstanceProfileName=StudioWorkstationSSMRole','SubnetId=subnet-0986937ce71fb97cb',
                      'VpcId=vpc-01bbe5d01838f70b2','DiskSizeGiB=100','ClientCidr=0.0.0.0/0']
            if profile=='gpu':
                creator.create_guarded(self,h.REGION,name,path,params,minutes=minutes,utc_now=utc_now or (lambda:NOW))
            else:
                self.native_create(name,json.loads(path.read_text()),dict(p.split('=',1) for p in params),
                                   {'StudioManaged':'true','StudioProfile':profile})
        return inventory.context(self,h.REGION,name)

    def terminate(self, iid):
        vm = self.vms[iid]
        vm['State']['Name'] = 'terminated'
        for item in vm['BlockDeviceMappings']:
            disk = item['Ebs']
            if disk['DeleteOnTermination'] and not self.keep_volume:
                self.volumes.pop(disk['VolumeId'],None)
        if self.address.get('InstanceId') == iid:
            self.detach()
        vm.pop('PublicIpAddress',None)

    def fire_schedule(self, name):
        spec = self.schedules[name]
        assert spec['State'] == 'ENABLED'
        assert spec['Target']['Arn'] == 'arn:aws:scheduler:::aws-sdk:ec2:stopInstances'
        ids = json.loads(spec['Target']['Input'])['InstanceIds']
        self(h.REGION, 'ec2', 'stop-instances', '--instance-ids', *ids)
        if spec['ActionAfterCompletion'] == 'DELETE':
            del self.schedules[name]

    def legacy_desktop(self, name='legacy'):
        selected = self.desktop(name)
        current = self.schedules.pop('studio-stop-' + selected['token'])
        self.resources[selected['stack_id']] = [r for r in selected['resources'] if r['LogicalResourceId'] != 'AutoStopSchedule']
        for prefix, action, payload, delay in (
                ('studio-expiry-', 'cloudformation:deleteStack', {'StackName': selected['stack_id']}, 0),
                ('studio-fallback-', 'ec2:terminateInstances', {'InstanceIds': [selected['instance']]}, 5)):
            spec = deepcopy(current)
            spec.update(Name=prefix + selected['token'], State='DISABLED', ActionAfterCompletion='DELETE',
                        ScheduleExpression='at(' + (NOW + dt.timedelta(minutes=150 + delay)).strftime('%Y-%m-%dT%H:%M:%S') + ')')
            spec['Target'].update(Arn='arn:aws:scheduler:::aws-sdk:' + action, Input=json.dumps(payload))
            self.schedules[spec['Name']] = spec
        return inventory.context(self, h.REGION, name)

    def detach(self):
        iid = self.address.get('InstanceId')
        for k in ('InstanceId','AssociationId','NetworkInterfaceId'):
            self.address.pop(k,None)
        if iid and iid in self.vms:
            self.vms[iid].pop('PublicIpAddress',None)
            if self.restore_public_ip and self.vms[iid]['State']['Name']=='running':
                self.vms[iid]['PublicIpAddress']='203.0.113.'+str(int(iid[2:],16)+50)

    def dispatch(self, service, action, args):
        def val(key, default=None):
            return args[args.index(key)+1] if key in args else default
        def many(key):
            if key not in args:
                return []
            result=[]
            for x in args[args.index(key)+1:]:
                if x.startswith('--'): break
                result.append(x)
            return result
        def matches(item):
            for f in many('--filters'):
                key, value = f.split(',Values=',1)
                if key.startswith('Name=tag:') and tagdict(item.get('Tags',[])).get(key[9:]) not in value.split(','):
                    return False
            return True
        if action=='wait': return {}
        if service=='sts': return {'Account':h.ACCOUNT}
        if service=='cloudformation':
            if action=='describe-stacks':
                return {'Stacks':[self.lookup(val('--stack-name'))] if val('--stack-name') else
                        [s for s in self.stacks.values() if s['StackStatus']!='DELETE_COMPLETE']}
            if action=='list-stack-resources': return {'StackResourceSummaries':self.resources[self.lookup(val('--stack-name'))['StackId']]}
            if action=='describe-stack-resource':
                s=self.lookup(val('--stack-name'))
                return {'StackResourceDetail':next(r for r in self.resources[s['StackId']] if r['LogicalResourceId']==val('--logical-resource-id'))}
            if action=='create-stack':
                parameters={}
                for raw in many('--parameters'):
                    key,value=raw.split(',ParameterValue=',1)
                    parameters[key.split('=',1)[1]]=value
                t={}
                for raw in many('--tags'):
                    key,value=raw.split(',Value=',1);t[key.split('=',1)[1]]=value
                return self.native_create(val('--stack-name'),json.loads(Path(val('--template-body')[7:]).read_text()),parameters,t)
            if action=='delete-stack':
                s=self.lookup(val('--stack-name'))
                for r in self.resources[s['StackId']]:
                    physical=r['PhysicalResourceId']
                    if r['ResourceType']=='AWS::EC2::Instance':self.terminate(physical)
                    if r['ResourceType']=='AWS::EC2::SecurityGroup':self.groups.pop(physical,None)
                    if r['ResourceType']=='AWS::IAM::Role':self.roles.pop(physical,None);self.policies.pop(physical,None)
                    if r['ResourceType']=='AWS::Scheduler::Schedule':self.schedules.pop(physical,None)
                    r['ResourceStatus']='DELETE_COMPLETE'
                s['StackStatus']='DELETE_COMPLETE'
                return {}
        if service=='s3api' and action=='head-object':
            from scripts import studio_workstation_cli as cli
            return {'VersionId': cli.GPU_VERSIONS[val('--key').rsplit('/',1)[1]]}
        if service=='ec2':
            if action=='describe-images':
                from scripts import studio_workstation_cli as cli
                return {'Images':[{'ImageId':cli.GPU_AMI, 'State':'available', 'OwnerId':cli.GPU_AMI_OWNER}]}
            if action=='describe-instances':
                ids=many('--instance-ids')
                if any(i not in self.vms for i in ids):raise missing('InvalidInstanceID.NotFound',action)
                return {'Reservations':[{'Instances':[v for i,v in self.vms.items() if (not ids or i in ids) and matches(v)]}]}
            if action=='describe-volumes':
                ids=many('--volume-ids')
                if any(i not in self.volumes for i in ids):raise missing('InvalidVolume.NotFound',action)
                return {'Volumes':[v for i,v in self.volumes.items() if (not ids or i in ids) and matches(v)]}
            if action=='describe-security-groups':
                ids=many('--group-ids')
                if any(i not in self.groups for i in ids):raise missing('InvalidGroup.NotFound',action)
                return {'SecurityGroups':[v for i,v in self.groups.items() if not ids or i in ids]}
            if action=='describe-subnets':return {'Subnets':[{'SubnetId':'subnet-0986937ce71fb97cb','VpcId':'vpc-01bbe5d01838f70b2','MapPublicIpOnLaunch':True}]}
            if action=='describe-addresses':return {'Addresses':[self.address]}
            if action in ('stop-instances','start-instances','terminate-instances'):
                for iid in many('--instance-ids'):
                    if action=='terminate-instances':self.terminate(iid)
                    else:
                        self.vms[iid]['State']['Name']='stopped' if action=='stop-instances' else 'running'
                        if action=='start-instances':self.vms[iid]['PublicIpAddress']=h.EIP_IP if self.address.get('InstanceId')==iid else '203.0.113.200'
                return {}
            if action=='disassociate-address':
                assert val('--association-id')==self.address['AssociationId'];self.detach();return {}
            if action=='associate-address':
                assert '--no-allow-reassociation' in args and not self.address.get('AssociationId')
                vm=next(v for v in self.vms.values() if v['NetworkInterfaces'][0]['NetworkInterfaceId']==val('--network-interface-id'))
                association='eipassoc-'+vm['InstanceId'][2:]
                self.address.update(InstanceId=vm['InstanceId'],AssociationId=association,NetworkInterfaceId=val('--network-interface-id'))
                vm['PublicIpAddress']=h.EIP_IP
                return {'AssociationId':association}
            if action in ('create-tags','delete-tags'):
                assert many('--resources')==[h.EIP_ID]
                current=tagdict(self.address['Tags'])
                for raw in many('--tags'):
                    pair=raw.split(',Value=',1);key=pair[0].split('=',1)[1]
                    if action=='create-tags':current[key]=pair[1]
                    else:current.pop(key,None)
                self.address['Tags']=tags(current);return {}
        if service=='scheduler':
            if action=='get-schedule-group':return {'State':'ACTIVE'}
            if action in ('create-schedule','update-schedule'):
                spec=json.loads(val('--cli-input-json'));name=spec['Name']
                if action=='create-schedule' and name in self.schedules:raise missing('ConflictException',action)
                if action=='update-schedule' and name not in self.schedules:raise missing('ResourceNotFoundException',action)
                self.schedules[name]=spec;return {'ScheduleArn':'arn:fake:schedule/'+name}
            if action=='get-schedule':
                if val('--name') not in self.schedules:raise missing('ResourceNotFoundException',action)
                return self.schedules[val('--name')]
            if action=='delete-schedule':self.schedules.pop(val('--name'));return {}
            if action=='list-schedules':return {'Schedules':[{'Name':n} for n in self.schedules]}
        if service=='iam':
            name=val('--role-name')
            if name not in self.roles:raise missing('NoSuchEntity',action)
            if action=='get-role':return {'Role':self.roles[name]}
            if action=='list-role-policies':return {'PolicyNames':list(self.policies[name])}
            if action=='list-attached-role-policies':return {'AttachedPolicies':[]}
            if action=='get-role-policy':return {'PolicyDocument':self.policies[name][val('--policy-name')]}
            if action=='put-role-policy':self.policies[name][val('--policy-name')]=json.loads(val('--policy-document'));return {}
            if action=='delete-role-policy':self.policies[name].pop(val('--policy-name'));return {}
            if action=='update-assume-role-policy':self.roles[name]['AssumeRolePolicyDocument']=json.loads(val('--policy-document'));return {}
        if service=='route53':return {'ResourceRecordSets':[{'Name':h.HOST+'.','Type':'A','TTL':300,'ResourceRecords':[{'Value':h.EIP_IP}]}]}
        if service=='ssm':
            if action=='send-command':return {'Command':{'CommandId':str(uuid.UUID(int=99))}}
            if action=='get-command-invocation':return {'Status':'Success','ResponseCode':0,
                'StandardOutputContent':'STUDIO_ISSUE43_GPU_TLS_READY\n'+acm.MARKER+'\n','StandardErrorContent':''}
        raise AssertionError((service,action,args))
