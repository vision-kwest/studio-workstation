"""Disposable CPU acceptance only; no passwords or browser authentication."""
import json
import subprocess
import sys
import time

import boto3
from botocore.exceptions import ClientError

NAME = 'studio-issue98-auto-20261117-cpu'
IID = 'i-0bcc4fb067f27de23'
ARN = 'arn:aws:cloudformation:us-east-1:401233741942:stack/' + NAME + '/9fa35670-e257-11f1-98cc-12ffc8347fad'
REGION = 'us-east-1'
ssm = boto3.client('ssm', region_name=REGION)
ec2 = boto3.client('ec2', region_name=REGION)


def cli(*args):
    p = subprocess.run([sys.executable, 'studio', 'workstation', *args],
                       text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    print('CLI', args, 'EXIT', p.returncode, flush=True)
    print(p.stdout, flush=True)
    if p.returncode:
        raise RuntimeError('CLI failed: ' + repr(args))
    return p.stdout


def guest(commands):
    for attempt in range(24):
        try:
            cid = ssm.send_command(InstanceIds=[IID], DocumentName='AWS-RunShellScript',
                Comment='Issue98 CPU local and shared-package acceptance; no credentials',
                TimeoutSeconds=90, Parameters={'commands': commands, 'executionTimeout': ['120']})['Command']['CommandId']
            break
        except ClientError as e:
            if e.response['Error']['Code'] not in ('InvalidInstanceId', 'TargetNotConnected'):
                raise
            time.sleep(5)
    else:
        raise RuntimeError('CPU unavailable through SSM')
    for attempt in range(90):
        time.sleep(2)
        try:
            result = ssm.get_command_invocation(CommandId=cid, InstanceId=IID)
        except ssm.exceptions.InvocationDoesNotExist:
            continue
        if result['Status'] in ('Success', 'Failed', 'Cancelled', 'TimedOut'):
            print('SSM', cid, result['Status'], flush=True)
            print(result.get('StandardOutputContent', ''), result.get('StandardErrorContent', ''), flush=True)
            if result['Status'] != 'Success':
                raise RuntimeError('Guest check failed: ' + cid)
            return result['StandardOutputContent']
    raise RuntimeError('CPU SSM command timeout')


def disks():
    vm = ec2.describe_instances(InstanceIds=[IID])['Reservations'][0]['Instances'][0]
    assert {t['Key']: t['Value'] for t in vm['Tags']}['aws:cloudformation:stack-id'] == ARN
    assert all(m['Ebs']['DeleteOnTermination'] for m in vm['BlockDeviceMappings'] if 'Ebs' in m)
    return sorted(m['Ebs']['VolumeId'] for m in vm['BlockDeviceMappings'] if 'Ebs' in m)


package_check = [
    'set -eu',
    'test -x /usr/local/bin/studio-rez-env',
    'timeout 30 cat /opt/studio/packages/prod/issue98_smoke_20261117/0.1/payload.txt',
    "grep '^fs-054fd0b72429f4569:/ /opt/studio/packages efs .*ro,tls,iam' /etc/fstab",
    'findmnt -rn -t nfs4 --mountpoint /opt/studio/packages -o SOURCE,FSTYPE,OPTIONS',
    "findmnt -rn -t nfs4 --mountpoint /opt/studio/packages -o OPTIONS | grep -Eq '(^|,)ro(,|$)'",
    "timeout 30 runuser -u workstation -- /usr/local/bin/studio-rez-env issue98_smoke_20261117-0.1 -- sh -c 'cat \"$STUDIO_ISSUE98_SMOKE\"'",
]
state = json.loads(cli('status', NAME))
assert state['instance'] == IID and state['stack_id'] == ARN and state['state'] == 'running'
cli('check-ready', NAME)
original_disks = disks()
guest(package_check)
guest(['set -eu', "runuser -u workstation -- python3 - <<'PY'\nfrom pathlib import Path\np=Path('/opt/studio/packages/prod/issue98_smoke_20261117/0.1/.consumer-write-probe')\nassert not p.exists()\ntry:\n with p.open('x') as f:f.write('probe')\nexcept OSError as e:\n assert e.errno in (13,30),e;print('PACKAGE_WRITE_DENIED',e.errno)\nelse:\n p.unlink();raise SystemExit('Consumer unexpectedly wrote the test package')\nPY"])
before = json.loads(guest(['set -eu', "python3 - <<'PY'\nfrom pathlib import Path\nimport hashlib,json,os\np=Path('/var/tmp/issue98-auto-cpu-marker')\nwith p.open('x') as f:f.write('issue98-auto-cpu-20261117\\n')\np.chmod(0o600);os.sync();print(json.dumps({'hash':hashlib.sha256(p.read_bytes()).hexdigest(),'boot':Path('/proc/sys/kernel/random/boot_id').read_text().strip()}))\nPY"]))
cli('stop', NAME)
assert json.loads(cli('status', NAME))['state'] == 'stopped' and disks() == original_disks
cli('connect', 'studio-issue98-cs-20261117001929-b')
assert json.loads(cli('status', 'studio-issue98-auto-20261117-publisher'))['state'] == 'running'
cli('start', NAME)
cli('check-ready', NAME)
current = json.loads(cli('status', NAME))
assert current['instance'] == IID and current['stack_id'] == ARN and current['state'] == 'running'
assert disks() == original_disks
observed = json.loads(guest(['set -eu', "python3 - <<'PY'\nfrom pathlib import Path\nimport hashlib,json\np=Path('/var/tmp/issue98-auto-cpu-marker');print(json.dumps({'hash':hashlib.sha256(p.read_bytes()).hexdigest(),'boot':Path('/proc/sys/kernel/random/boot_id').read_text().strip()}))\nPY"]))
assert observed['hash'] == before['hash'] and observed['boot'] != before['boot']
guest(package_check)
cli('connect', NAME)
print('CPU_STOP_START_REZ_EFS_PERSISTENCE_PASS', json.dumps({'instance':IID, 'disks':original_disks, 'before':before, 'after':observed}), flush=True)
