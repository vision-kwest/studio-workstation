"""Issue #43: guarded single-EIP CPU/GPU handoff, explicitly invoked by CLI.

There is no DNS daemon, extra IP, new certificate, Image Builder change,
or change to PR #65's independent EC2/EBS expiration schedules.

The read-only plan uses the existing Studio CLI AWS invocation interface.
The live write path requires explicit --execute, strict per-instance TLS
readiness, independent expiration guards and a manager-side trust check.

GPU preinstallation of AWCP and the instance-scoped IAM trust are separate,
reviewed prerequisites. Never attach the EIP before the GPU has a validated
DCV console and trusted certificate.
"""
import datetime as dt
import json
import re
import subprocess
import time

from runtime import pr65_one_shot_guard as guard
from runtime import pr65_gpu_create

REGION = "us-east-1"
ACCOUNT = "401233741942"
HOST = "dcv.kwesidavis.com"
ZONE = "Z02607163QPWX22FZ2Q9M"
EIP_ID = "eipalloc-0879a34aa1a1298dd"
EIP_IP = "54.159.180.104"
CPU_STACK = "studio-workstation-cpu-v013"
CPU_ID = "i-0bbfbdc878d3a2320"
CPU_ENI = "eni-0719a8758ddd51d4c"
GPU_STACK = "studio-gpu-cli-accept65"
GPU_TOKEN = "pr65-b23c8e5fdf67b683cd35"
GPU_AMI = "ami-027c98f85b155c2ac"
BASE_PROFILE = "arn:aws:iam::401233741942:instance-profile/StudioWorkstationSSMRole"
BASE_ROLE_ID = "AROAV223FBB3JOQHYPKDX"
CERT_ARN = "arn:aws:acm:us-east-1:401233741942:certificate/b753beee-5659-4311-a52f-39326b7b3243"
EXPORT_ROLE = "arn:aws:iam::401233741942:role/studio-issue43-dcv-acm-export"
SAFETY_MARGIN = dt.timedelta(minutes=60)  # Reserve a full hour for user login and EIP return
LIVE_HANDOFF_ENABLED = True  # Explicit --execute and all fail-closed preflights still required.

def _items(aws, *args):
    data = aws(REGION, "ec2", "describe-instances", "--instance-ids", *args)
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
            or tags.get("StudioOwnerStack") not in (CPU_STACK, "studio-independent-dcv")):
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

def _guard_schedule(aws, name, iid, action, payload, now):
    sched = aws(REGION, "scheduler", "get-schedule", "--group-name", guard.GROUP, "--name", name)
    target = sched.get("Target", {})
    if (sched.get("Name") != name or sched.get("GroupName") != guard.GROUP or
            sched.get("State") != "ENABLED" or sched.get("ActionAfterCompletion") != "DELETE" or
            sched.get("ScheduleExpressionTimezone") != "UTC" or
            sched.get("FlexibleTimeWindow") != {"Mode": "OFF"} or
            target.get("Arn") != action or target.get("RoleArn") != guard.ROLE or
            target.get("RetryPolicy") != {"MaximumRetryAttempts": 3, "MaximumEventAgeInSeconds": 3600} or
            json.loads(target.get("Input", "null")) != payload):
        raise ValueError("PR #65 independent expiration guard mismatch")
    expr = sched.get("ScheduleExpression", "")
    if not re.fullmatch(r"at\(20\d\d-\d\d-\d\dT\d\d:\d\d:\d\d\)", expr):
        raise ValueError("Unexpected GPU termination schedule")
    when = dt.datetime.strptime(expr[3:-1], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=dt.timezone.utc)
    if when - now < SAFETY_MARGIN:
        raise ValueError("GPU expiration is too near for a safe handoff")
    if when - now > dt.timedelta(minutes=240):
        raise ValueError("GPU expiration exceeds PR #65 supervised guard bound")
    return when

def _gpu_guard(aws, gpu, now):
    if _tags(gpu).get("StudioStack") != GPU_STACK or _tags(gpu).get("StudioCleanupToken") != GPU_TOKEN:
        raise ValueError("GPU ownership tags or token mismatch")
    mappings = [m for m in gpu.get("BlockDeviceMappings", []) if m.get("DeviceName") == "/dev/sda1"]
    if len(mappings) != 1 or not mappings[0].get("Ebs", {}).get("VolumeId"):
        raise ValueError("GPU root EBS not discovered")
    vid = mappings[0]["Ebs"]["VolumeId"]
    volumes = aws(REGION, "ec2", "describe-volumes", "--volume-ids", vid)["Volumes"]
    if len(volumes) != 1:
        raise ValueError("Unexpected GPU EBS inventory")
    guard.verify_ownership(gpu, volumes[0], GPU_STACK, GPU_TOKEN)
    term = _guard_schedule(aws, "studio-pr65-i-" + gpu["InstanceId"], gpu["InstanceId"],
                           guard.TERMINATE, {"InstanceIds": [gpu["InstanceId"]]}, now)
    cleanup = _guard_schedule(aws, "studio-pr65-v-" + vid, gpu["InstanceId"],
                              guard.DELETE_VOLUME, {"VolumeId": vid}, now)
    if cleanup <= term + dt.timedelta(minutes=29) or cleanup > term + dt.timedelta(minutes=120):
        raise ValueError("GPU volume-cleanup timing outside PR #65 guard bounds")
    return {"terminate_at": term.isoformat(), "volume_cleanup_at": cleanup.isoformat(),
            "root_volume_id": vid}

def review_gpu_iam(instance_id):
    """Exact proposed GPU-only policy and export-role trust addition, no AWS calls."""
    if not re.fullmatch(r"i-[0-9a-f]{8,17}", instance_id):
        raise ValueError("GPU instance ID not valid")
    session = BASE_ROLE_ID + ":" + instance_id
    cond = {"StringEquals": {"aws:userid": session}}
    return {
        "gpu_instance_id": instance_id,
        "base_role": "StudioWorkstationSSMRole",
        "new_inline_policy_name": "StudioIssue43GpuExactInstanceAssume",
        "new_inline_policy": {"Version": "2012-10-17", "Statement": [{
            "Effect": "Allow", "Action": "sts:AssumeRole",
            "Resource": EXPORT_ROLE, "Condition": cond}]},
        "export_role": EXPORT_ROLE,
        "additional_trust_statement": {
            "Effect": "Allow", "Principal": {"AWS": "arn:aws:iam::" + ACCOUNT + ":role/StudioWorkstationSSMRole"},
            "Action": "sts:AssumeRole", "Condition": cond},
        "certificate_arn": CERT_ARN,
        "warning": "Review as additive-only. Preserve the CPU session trust; do not grant role-wide export."
    }

def gpu_preview(aws, *, now=None):
    """Read-only, exact-scope CPU->GPU handoff preview. No AWS writes."""
    now = now or dt.datetime.now(dt.timezone.utc)
    if now.tzinfo is None or now.utcoffset() != dt.timedelta(0):
        raise ValueError("Require UTC-aware time")
    gpu_id = _stack_iid(aws, GPU_STACK)
    if _stack_iid(aws, CPU_STACK) != CPU_ID:
        raise ValueError("Original CPU stack identity changed")
    cpu, gpu = (lambda x: (x[CPU_ID], x[gpu_id]))(_items(aws, CPU_ID, gpu_id))
    if cpu.get("State", {}).get("Name") != "stopped":
        raise ValueError("CPU source must be stopped to prevent dual hostname owners")
    if cpu.get("NetworkInterfaces", [{}])[0].get("NetworkInterfaceId") != CPU_ENI:
        raise ValueError("CPU interface identity changed")
    if gpu.get("State", {}).get("Name") != "running":
        raise ValueError("GPU must be running and prepared before IP transfer")
    if (gpu.get("ImageId") != GPU_AMI or gpu.get("InstanceType") != "g4dn.xlarge" or
            gpu.get("IamInstanceProfile", {}).get("Arn") != BASE_PROFILE or
            gpu.get("VpcId") != cpu.get("VpcId")):
        raise ValueError("GPU AMI, profile, or VPC mismatch")
    if len(gpu.get("NetworkInterfaces", [])) != 1:
        raise ValueError("GPU has unexpected ENI inventory")
    gpu_eni = gpu["NetworkInterfaces"][0]
    if (gpu_eni.get("Attachment", {}).get("DeviceIndex") != 0 or
            gpu_eni.get("Attachment", {}).get("Status") != "attached"):
        raise ValueError("GPU primary ENI attachment mismatch")
    if gpu_eni.get("Association", {}).get("AllocationId"):
        raise ValueError("GPU already has an associated Elastic IP; do not replace it")
    address = _eip(aws)
    if (address.get("InstanceId") != CPU_ID or address.get("NetworkInterfaceId") != CPU_ENI or
            address.get("AssociationId") is None or
            _tags(address).get("StudioDcvInstanceId") != CPU_ID):
        raise ValueError("Current EIP owner is not the approved CPU")
    _dns(aws)
    # Reuse PR #65's original read-only IAM/Scheduler preflight, not a
    # parallel hand-written policy check. A changed expiration execution
    # role must block the EIP transfer even if the schedules still exist.
    pr65_gpu_create.preflight(aws, REGION, GPU_STACK, GPU_TOKEN)
    expiry = _gpu_guard(aws, gpu, now)
    return {"from": CPU_ID, "to": gpu_id, "from_stack": CPU_STACK, "to_stack": GPU_STACK,
            "allocation_id": EIP_ID, "ip": EIP_IP, "hostname": HOST,
            "old_association": address["AssociationId"], "gpu_eni": gpu["NetworkInterfaces"][0]["NetworkInterfaceId"],
            "guard": expiry, "gpu_cert_prep": "REQUIRES SEPARATELY APPROVED IAM/AWCP + LIVE SSM TLS PROBE",
            "writes_enabled": LIVE_HANDOFF_ENABLED}

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

def handoff_gpu(aws, verify_public_tls, *, now=None, allow_live=False,
                verify_guest=check_gpu_tls, clock=lambda: dt.datetime.now(dt.timezone.utc)):
    """Explicit live handoff; preflight and rollback are always mandatory.

    Failure after disassociation attempts recovery of original stopped CPU
    association. If another owner has intervened, never steal the allocation.
    """
    if not (LIVE_HANDOFF_ENABLED and allow_live):
        raise RuntimeError("GPU EIP handoff requires explicit execution authorization")
    pre = gpu_preview(aws, now=now or clock())
    verify_guest(aws, pre["to"])
    # Refresh the guard, source ownership and deadline immediately before writes.
    refreshed = gpu_preview(aws, now=clock())
    if (refreshed["to"] != pre["to"] or refreshed["old_association"] != pre["old_association"]):
        raise RuntimeError("Handoff preflight changed during guest preparation")
    new_assoc = None
    try:
        # The API may report an error after applying a disassociation. Keep
        # this operation inside the rollback boundary and read current state
        # before attempting any restoration.
        aws(REGION, "ec2", "disassociate-address", "--association-id", pre["old_association"])
        resp = aws(REGION, "ec2", "associate-address", "--allocation-id", EIP_ID,
                   "--instance-id", pre["to"], "--no-allow-reassociation")
        new_assoc = resp["AssociationId"]
        current = _eip(aws)
        if current.get("AssociationId") != new_assoc or current.get("InstanceId") != pre["to"]:
            raise RuntimeError("Handoff EIP readback mismatch")
        verify_public_tls(HOST, EIP_IP)
        aws(REGION, "ec2", "create-tags", "--resources", EIP_ID,
            "--tags", "Key=StudioDcvInstanceId,Value=" + pre["to"])
        if _tags(_eip(aws)).get("StudioDcvInstanceId") != pre["to"]:
            raise RuntimeError("Handoff EIP owner tag verification failed")
        _dns(aws)
        return {"owner": pre["to"], "hostname": HOST, "address": EIP_IP, "requires_return_before": refreshed["guard"]["terminate_at"]}
    except BaseException as exc:
        # Do not seize a foreign association if another actor touched the EIP.
        try:
            current = _eip(aws)
            owner = current.get("InstanceId")
            if owner == CPU_ID and current.get("AssociationId") == pre["old_association"]:
                # Disassociation was rejected before it took effect.
                # Already-safe CPU ownership is the correct rollback outcome.
                if _tags(current).get("StudioDcvInstanceId") != CPU_ID:
                    raise RuntimeError("Unchanged CPU EIP has conflicting ownership tag")
            else:
                if owner not in (None, pre["to"]):
                    raise RuntimeError("Foreign EIP owner; manual intervention mandatory")
                if owner == pre["to"]:
                    aws(REGION, "ec2", "disassociate-address", "--association-id", current["AssociationId"])
                restore = aws(REGION, "ec2", "associate-address", "--allocation-id", EIP_ID,
                              "--instance-id", CPU_ID, "--no-allow-reassociation")
                if not restore.get("AssociationId") or _eip(aws).get("InstanceId") != CPU_ID:
                    raise RuntimeError("CPU EIP restoration not verified")
                aws(REGION, "ec2", "create-tags", "--resources", EIP_ID,
                    "--tags", "Key=StudioDcvInstanceId,Value=" + CPU_ID)
                if _tags(_eip(aws)).get("StudioDcvInstanceId") != CPU_ID:
                    raise RuntimeError("CPU tag restoration not verified")
        except BaseException as restore_error:
            raise RuntimeError("GPU handoff failed AND CPU EIP restoration unverified; manual remediation required") from restore_error
        raise RuntimeError("GPU handoff failed, dedicated EIP restored to CPU") from exc

def cpu_return_preview(aws, gpu_instance_id):
    """Read-only return plan when GPU stopped/terminated or EIP unassociated."""
    if not re.fullmatch(r"i-[0-9a-f]{8,17}", gpu_instance_id):
        raise ValueError("Expected exact former GPU ID")
    if _stack_iid(aws, CPU_STACK) != CPU_ID:
        raise ValueError("Original CPU identity changed")
    cpu = _items(aws, CPU_ID)[CPU_ID]
    if cpu["State"]["Name"] != "stopped" or cpu["NetworkInterfaces"][0]["NetworkInterfaceId"] != CPU_ENI:
        raise ValueError("CPU must be stopped and exact original instance")
    addr = _eip(aws)
    if _tags(addr).get("StudioDcvInstanceId") != gpu_instance_id:
        raise ValueError("GPU does not own the EIP according to its tag")
    if addr.get("InstanceId") not in (None, gpu_instance_id):
        raise ValueError("Foreign EIP association; never steal")
    if addr.get("InstanceId"):
        gpu = _items(aws, gpu_instance_id)[gpu_instance_id]
        if gpu.get("State", {}).get("Name") not in ("stopped", "terminated"):
            raise ValueError("GPU must be stopped/terminated before returning EIP")
    _dns(aws)
    return {"from": gpu_instance_id, "to": CPU_ID, "association": addr.get("AssociationId"),
            "allocation_id": EIP_ID, "hostname": HOST, "ip": EIP_IP,
            "writes_enabled": LIVE_HANDOFF_ENABLED, "cpu_will_remain_stopped": True}

def restore_cpu(aws, gpu_instance_id, *, allow_live=False):
    if not (LIVE_HANDOFF_ENABLED and allow_live):
        raise RuntimeError("CPU EIP return requires explicit execution authorization")
    plan = cpu_return_preview(aws, gpu_instance_id)
    if plan["association"]:
        aws(REGION, "ec2", "disassociate-address", "--association-id", plan["association"])
    result = aws(REGION, "ec2", "associate-address", "--allocation-id", EIP_ID,
                 "--instance-id", CPU_ID, "--no-allow-reassociation")
    if not result.get("AssociationId") or _eip(aws).get("InstanceId") != CPU_ID:
        raise RuntimeError("CPU EIP reassociation not verified; manual remediation required")
    aws(REGION, "ec2", "create-tags", "--resources", EIP_ID,
        "--tags", "Key=StudioDcvInstanceId,Value=" + CPU_ID)
    if _tags(_eip(aws)).get("StudioDcvInstanceId") != CPU_ID:
        raise RuntimeError("CPU EIP ownership tag not restored; manual remediation required")
    return {"owner": CPU_ID, "cpu_state": "stopped", "address": EIP_IP,
            "instruction": "Start the CPU separately and verify DCV and ACM before connecting"}
