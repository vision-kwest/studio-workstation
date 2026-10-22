"""Control an existing EC2 workstation through AWS APIs; no SSH or control host."""
import argparse
import json
import sys
import time
import boto3
from botocore.exceptions import ClientError, NoCredentialsError


def describe(ec2, instance_id):
    result = ec2.describe_instances(InstanceIds=[instance_id])
    instances = [i for r in result['Reservations'] for i in r['Instances']]
    if len(instances) != 1:
        raise RuntimeError('Expected exactly one instance')
    return instances[0]


def main(argv=None):
    p = argparse.ArgumentParser(description='Manage a single EC2/DCV workstation')
    p.add_argument('action', choices=['status', 'start', 'stop', 'connect', 'check'])
    p.add_argument('--instance-id', required=True)
    p.add_argument('--region', default='us-east-1')
    p.add_argument('--profile', help='AWS SSO profile name (optional)')
    p.add_argument('--dcv-user', required=False, help='Linux login username (for DCV URL only)')
    p.add_argument('--wait', action='store_true', help='Wait for instance state transition')
    a = p.parse_args(argv)
    try:
        session = boto3.Session(profile_name=a.profile, region_name=a.region)
        ec2 = session.client('ec2')
        i = describe(ec2, a.instance_id)
        state = i['State']['Name']
        if a.action == 'check':
            identity = session.client('sts').get_caller_identity()
            print(json.dumps({'account': identity['Account'], 'principal': identity['Arn'], 'region': a.region, 'instance': a.instance_id, 'state': state}, indent=2))
        elif a.action == 'status':
            print(json.dumps({'instance': a.instance_id, 'state': state, 'type': i['InstanceType'], 'public_ip': i.get('PublicIpAddress'), 'private_ip': i.get('PrivateIpAddress')}, indent=2))
        elif a.action == 'start':
            if state == 'stopped':
                ec2.start_instances(InstanceIds=[a.instance_id]); print('Start requested')
                if a.wait: ec2.get_waiter('instance_running').wait(InstanceIds=[a.instance_id]); print('Running')
            else: print(f'No action: state is {state}')
        elif a.action == 'stop':
            if state == 'running':
                ec2.stop_instances(InstanceIds=[a.instance_id]); print('Stop requested')
                if a.wait: ec2.get_waiter('instance_stopped').wait(InstanceIds=[a.instance_id]); print('Stopped')
            else: print(f'No action: state is {state}')
        elif a.action == 'connect':
            if state != 'running': raise RuntimeError('Instance must be running')
            ip = i.get('PublicIpAddress')
            if not ip: raise RuntimeError('No public IP: use VPN/private routing or an approved secure gateway')
            print(f'https://{ip}:8443/')
            print('Requires DCV server, a running session, valid Linux credentials, and inbound TCP 8443 limited to your IP.')
            print('If the instance was stopped, its public IP may have changed. A stable DNS name or Elastic IP can help (additional cost).')
    except (ClientError, NoCredentialsError, RuntimeError) as e:
        print(f'Error: {e}', file=sys.stderr)
        return 1
    return 0

if __name__ == '__main__':
    raise SystemExit(main())
