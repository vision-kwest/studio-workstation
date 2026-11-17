"""Shared DCV configuration and read-only AWS/guest certificate verification."""
import json
import re
import subprocess
import time


REGION = "us-east-1"
ACCOUNT = "401233741942"
HOST = "dcv.kwesidavis.com"
ZONE = "Z02607163QPWX22FZ2Q9M"
EIP_ID = "eipalloc-0879a34aa1a1298dd"
EIP_IP = "54.159.180.104"
BASE_PROFILE = "arn:aws:iam::401233741942:instance-profile/StudioWorkstationSSMRole"
BASE_ROLE_ID = "AROAV223FBB3JOQHYPKDX"
CERT_ARN = "arn:aws:acm:us-east-1:401233741942:certificate/b753beee-5659-4311-a52f-39326b7b3243"
EXPORT_ROLE = "arn:aws:iam::401233741942:role/studio-issue43-dcv-acm-export"

def _items(aws, *args, allow_missing=False):
    data = aws(REGION, "ec2", "describe-instances", "--instance-ids", *args)
    # Only stale-reference reconciliation may accept a single absent instance.
    if allow_missing and len(args) == 1 and data.get("Reservations") == []:
        return {}
    items = [i for r in data.get("Reservations", []) for i in r.get("Instances", [])]
    if len(items) != len(args) or {i.get("InstanceId") for i in items} != set(args):
        raise ValueError("Unexpected EC2 identity, duplicate, or missing instance")
    return {x["InstanceId"]: x for x in items}

def _tags(item):
    pairs = [(x["Key"], x["Value"]) for x in item.get("Tags", [])]
    if len(set(x[0] for x in pairs)) != len(pairs):
        raise ValueError("Duplicate resource tag")
    return dict(pairs)

def _stack_iid(aws, name):
    response = aws(REGION, "cloudformation", "describe-stack-resource",
                   "--stack-name", name, "--logical-resource-id", "Workstation")
    detail = response["StackResourceDetail"]
    iid = detail.get("PhysicalResourceId")
    if detail.get("LogicalResourceId") != "Workstation" or not re.fullmatch(r"i-[a-f0-9]{8,17}", iid or ""):
        raise ValueError("Invalid CloudFormation physical workstation ID")
    if detail.get("StackName") != name or detail.get("ResourceStatus") != "CREATE_COMPLETE":
        raise ValueError("Workstation CloudFormation ownership or state mismatch")
    return iid

def _eip(aws):
    entries = aws(REGION, "ec2", "describe-addresses", "--allocation-ids", EIP_ID)["Addresses"]
    if len(entries) != 1:
        raise ValueError("EIP allocation not unique")
    item = entries[0]
    if item.get("AllocationId") != EIP_ID or item.get("PublicIp") != EIP_IP:
        raise ValueError("Unexpected EIP allocation or public IPv4")
    tags = _tags(item)
    if (tags.get("StudioIssue") != "43" or tags.get("StudioDcvHostname") != HOST
            or tags.get("StudioOwnerStack") != "studio-independent-dcv"):
        raise ValueError("Dedicated EIP ownership tags changed")
    return item

def _dns(aws):
    records = aws(REGION, "route53", "list-resource-record-sets",
                  "--hosted-zone-id", ZONE)["ResourceRecordSets"]
    matching = [x for x in records if x.get("Name") == HOST + "." and x.get("Type") == "A"]
    if (len(matching) != 1 or matching[0].get("TTL") != 300 or
            matching[0].get("ResourceRecords") != [{"Value": EIP_IP}] or
            matching[0].get("AliasTarget")):
        raise ValueError("Route 53 A record differs from dedicated stable EIP")
    return matching[0]


def check_gpu_tls(aws, instance_id, *, sleep=time.sleep):
    """Live-only SSM proof for GPU RES console and local ACM certificate.

    No private key contents are printed, and no guest files are changed.
    """
    commands = [
        "set -eu",
        "test -f /root/bootstrap/semaphore/linux_vdi_config_host_ready.lock",
        "systemctl is-active gdm3 dcvserver aws-workload-credentials-provider-acm.service >/dev/null",
        "dcv describe-session console --json | python3 -c 'import json,sys; s=json.load(sys.stdin); assert s.get(\"id\")==\"console\" and s.get(\"owner\")==\"workstation\" and s.get(\"status\")==\"running\"'",
        "test \"$(stat -Lc '%U:%G %a' /etc/dcv/dcv.pem)\" = 'dcv:dcv 600'",
        "test \"$(stat -Lc '%U:%G %a' /etc/dcv/dcv.key)\" = 'dcv:dcv 600'",
        "openssl x509 -in /etc/dcv/dcv.pem -noout -checkend 172800 >/dev/null",
        "openssl x509 -in /etc/dcv/dcv.pem -noout -checkhost " + HOST + " | grep -q 'does match'",
        "test \"$(openssl x509 -in /etc/dcv/dcv.pem -pubkey -noout | sha256sum | cut -d' ' -f1)\" = \"$(openssl pkey -in /etc/dcv/dcv.key -pubout | sha256sum | cut -d' ' -f1)\"",
        "timeout 12 openssl s_client -connect 127.0.0.1:8443 -servername " + HOST +
        " -verify_hostname " + HOST + " -verify_return_error -brief </dev/null >/dev/null 2>&1",
        "echo STUDIO_ISSUE43_GPU_TLS_READY"
    ]
    response = aws(REGION, "ssm", "send-command", "--instance-ids", instance_id,
                   "--document-name", "AWS-RunShellScript",
                   "--parameters", json.dumps({"commands": commands, "executionTimeout": ["75"]}),
                   "--timeout-seconds", "95",
                   "--comment", "Issue43 GPU cert local read-only readiness")
    cmd_id = response["Command"]["CommandId"]
    for _ in range(14):
        sleep(5)
        try:
            reply = aws(REGION, "ssm", "get-command-invocation", "--command-id", cmd_id,
                        "--instance-id", instance_id)
        except subprocess.CalledProcessError as exc:
            # SSM GetCommandInvocation is eventually consistent immediately
            # after SendCommand. Never mask AccessDenied or other failures.
            if "InvocationDoesNotExist" not in (exc.stderr or ""):
                raise
            continue
        status = reply.get("Status")
        if status == "Success":
            if (reply.get("ResponseCode") != 0 or
                    "STUDIO_ISSUE43_GPU_TLS_READY" not in reply.get("StandardOutputContent", "").splitlines()):
                raise ValueError("GPU readiness did not produce the exact safe marker")
            return
        if status in ("Failed", "Cancelled", "TimedOut"):
            raise RuntimeError("GPU DCV/ACM certificate readiness failed")
    raise RuntimeError("GPU DCV/ACM SSM readiness timeout")
