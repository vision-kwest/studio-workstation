"""Issue #43: guarded destruction of the one PR #65 GPU acceptance stack.

No generic discovery/deletion: every physical ID is checked against its stack,
root block mapping, ownership tags, and independent expiry schedule before use.
The shared CPU/EIP/DNS/ACM resources are never deleted.
"""
import json
import re
import subprocess
import time

from runtime import issue43_hostname_handoff as h
from runtime import issue43_gpu_acm_setup as acm
from runtime import pr65_one_shot_guard as guard

ROOT = "/dev/sda1"
TYPES = {"Workstation": "AWS::EC2::Instance",
         "WorkstationSG": "AWS::EC2::SecurityGroup",
         "CleanupVolumeTagLaunchTemplate": "AWS::EC2::LaunchTemplate"}
MISSING = {"volume": "InvalidVolume.NotFound",
           "instance": "InvalidInstanceID.NotFound",
           "sg": "InvalidGroup.NotFound",
           "lt": "InvalidLaunchTemplateId.NotFound",
           "schedule": "ResourceNotFoundException"}


def _absent(call, code):
    try:
        return call()
    except subprocess.CalledProcessError as exc:
        if code in (exc.stderr or ""):
            return None
        raise


def _one(aws, service, operation, key, identity):
    result = aws(h.REGION, service, operation, key, identity)
    field = {"describe-volumes": "Volumes",
             "describe-security-groups": "SecurityGroups",
             "describe-launch-templates": "LaunchTemplates"}[operation]
    items = result.get(field, [])
    if len(items) != 1:
        raise ValueError("Ambiguous or missing " + operation + " identity")
    return items[0]


def _owned(item):
    tags = h._tags(item)
    if (tags.get("StudioStack"), tags.get("StudioCleanupToken")) != (h.GPU_STACK, h.GPU_TOKEN):
        raise ValueError("Unexpected GPU resource ownership")
    return tags


def _stack_and_resources(aws):
    stacks = aws(h.REGION, "cloudformation", "describe-stacks",
                 "--stack-name", h.GPU_STACK)["Stacks"]
    if len(stacks) != 1:
        raise ValueError("Unexpected GPU CloudFormation stack count")
    stack = stacks[0]
    arn = stack.get("StackId", "")
    if (stack.get("StackName") != h.GPU_STACK or
            not arn.startswith("arn:aws:cloudformation:" + h.REGION + ":" + h.ACCOUNT +
                               ":stack/" + h.GPU_STACK + "/") or
            stack.get("StackStatus") != "CREATE_COMPLETE"):
        raise ValueError("GPU stack identity/status changed")
    _owned(stack)
    records = aws(h.REGION, "cloudformation", "list-stack-resources",
                  "--stack-name", arn)["StackResourceSummaries"]
    if len(records) != len(TYPES):
        raise ValueError("Unexpected GPU stack resource count")
    ids = {}
    for record in records:
        name = record.get("LogicalResourceId")
        if (name not in TYPES or name in ids or record.get("ResourceType") != TYPES[name] or
                record.get("ResourceStatus") != "CREATE_COMPLETE"):
            raise ValueError("Unexpected GPU stack logical resource")
        ids[name] = record.get("PhysicalResourceId")
    if set(ids) != set(TYPES):
        raise ValueError("Missing GPU CloudFormation physical resource")
    patterns = {"Workstation": r"i-[0-9a-f]{8,17}",
                "WorkstationSG": r"sg-[0-9a-f]{8,17}",
                "CleanupVolumeTagLaunchTemplate": r"lt-[0-9a-f]{8,17}"}
    if any(not re.fullmatch(pat, ids[key] or "") for key, pat in patterns.items()):
        raise ValueError("Invalid CloudFormation GPU physical ID")
    return arn, ids


def _schedule(aws, name, target, payload):
    value = _absent(lambda: aws(h.REGION, "scheduler", "get-schedule",
                                "--group-name", guard.GROUP, "--name", name),
                    MISSING["schedule"])
    if value is None:
        raise RuntimeError("Independent PR65 expiration schedule missing; abort before deletion")
    spec = value.get("Target", {})
    if (value.get("Name") != name or value.get("GroupName") != guard.GROUP or
            value.get("State") != "ENABLED" or
            value.get("ActionAfterCompletion") != "DELETE" or
            value.get("ScheduleExpressionTimezone") != "UTC" or
            value.get("FlexibleTimeWindow") != {"Mode": "OFF"} or
            spec.get("Arn") != target or spec.get("RoleArn") != guard.ROLE or
            spec.get("RetryPolicy") != {"MaximumRetryAttempts": 3,
                                         "MaximumEventAgeInSeconds": 3600} or
            json.loads(spec.get("Input", "null")) != payload):
        raise ValueError("Unexpected PR65 expiration schedule target/scope")
    return name


def _preflight(aws):
    arn, ids = _stack_and_resources(aws)
    iid = ids["Workstation"]
    vm = h._items(aws, iid)[iid]
    _owned(vm)
    if vm.get("State", {}).get("Name") not in ("running", "stopped", "stopping", "terminated"):
        raise ValueError("GPU instance state not safe for teardown")
    block = [x for x in vm.get("BlockDeviceMappings", [])
             if x.get("DeviceName") == ROOT]
    if (len(block) != 1 or block[0].get("Ebs", {}).get("DeleteOnTermination") is not False):
        raise ValueError("Retained GPU root EBS identity not verified")
    vid = block[0]["Ebs"].get("VolumeId")
    if not re.fullmatch(r"vol-[0-9a-f]{8,17}", vid or ""):
        raise ValueError("Unexpected retained GPU volume ID")
    volume = _one(aws, "ec2", "describe-volumes", "--volume-ids", vid)
    _owned(volume)
    if volume.get("VolumeId") != vid or not volume.get("Encrypted"):
        raise ValueError("Encrypted GPU root EBS identity changed")
    if volume.get("State") == "in-use":
        if not any(a.get("InstanceId") == iid and a.get("Device") == ROOT
                   for a in volume.get("Attachments", [])):
            raise ValueError("GPU root EBS attached to unexpected instance")
    elif volume.get("State") != "available" or volume.get("Attachments"):
        raise ValueError("Unexpected GPU root EBS lifecycle/attachment")
    for logical, kind, operation, key in (
        ("WorkstationSG", "sg", "describe-security-groups", "--group-ids"),
        ("CleanupVolumeTagLaunchTemplate", "lt", "describe-launch-templates",
         "--launch-template-ids"),
    ):
        item = _one(aws, "ec2", operation, key, ids[logical])
        actual_id = item["GroupId"] if kind == "sg" else item["LaunchTemplateId"]
        if actual_id != ids[logical]:
            raise ValueError("GPU security group/template identity mismatch")
        _owned(item)
    schedules = [
        _schedule(aws, "studio-pr65-i-" + iid, guard.TERMINATE,
                  {"InstanceIds": [iid]}),
        _schedule(aws, "studio-pr65-v-" + vid, guard.DELETE_VOLUME,
                  {"VolumeId": vid}),
    ]
    cpu = h._items(aws, h.CPU_ID)[h.CPU_ID]
    if (cpu.get("State", {}).get("Name") != "stopped" or
            cpu.get("NetworkInterfaces", [{}])[0].get("NetworkInterfaceId") != h.CPU_ENI):
        raise ValueError("Shared original CPU must remain stopped during GPU deletion")
    if h._stack_iid(aws, h.CPU_STACK) != h.CPU_ID:
        raise ValueError("Original CPU stack changed")
    eip = h._eip(aws)
    owner = eip.get("InstanceId")
    tagged = h._tags(eip).get("StudioDcvInstanceId")
    if not ((owner == iid and tagged == iid) or
            (owner is None and tagged == iid) or
            (owner == h.CPU_ID and tagged == h.CPU_ID)):
        raise ValueError("Unexpected shared EIP owner; no GPU resources modified")
    h._dns(aws)
    return {"arn": arn, "iid": iid, "vid": vid, "sg": ids["WorkstationSG"],
            "lt": ids["CleanupVolumeTagLaunchTemplate"],
            "schedules": schedules, "state": vm["State"]["Name"], "eip_owner": owner}


def _tagged_inventory(aws, service, operation, field):
    filters = ("Name=tag:StudioStack,Values=" + h.GPU_STACK,
               "Name=tag:StudioCleanupToken,Values=" + h.GPU_TOKEN)
    result = aws(h.REGION, service, operation, "--filters", *filters)
    if operation == "describe-instances":
        return [i for r in result.get("Reservations", []) for i in r.get("Instances", [])]
    return result.get(field, [])


def _verify_no_orphans(aws, ids):
    for operation, field, identity in (
        ("describe-volumes", "Volumes", ids["vid"]),
        ("describe-security-groups", "SecurityGroups", ids["sg"]),
        ("describe-launch-templates", "LaunchTemplates", ids["lt"]),
    ):
        values = _tagged_inventory(aws, "ec2", operation, field)
        if values:
            raise RuntimeError("GPU owned " + field + " remain after destroy")
    for vm in _tagged_inventory(aws, "ec2", "describe-instances", "Reservations"):
        if vm["InstanceId"] != ids["iid"] or vm["State"]["Name"] != "terminated":
            raise RuntimeError("Unaccounted GPU instance in tagged inventory")


def _gone(aws, kind, service, operation, key, physical_id):
    found = _absent(lambda: aws(h.REGION, service, operation, key, physical_id),
                    MISSING[kind])
    if found is None:
        return
    if kind == "instance":
        items = [x for r in found.get("Reservations", []) for x in r.get("Instances", [])]
        if len(items) == 1 and items[0]["InstanceId"] == physical_id and items[0]["State"]["Name"] == "terminated":
            return
    raise RuntimeError("GPU " + kind + " not verifiably removed")


def destroy_gpu(aws, run, stack, region, *, sleep=time.sleep):
    """Synchronous, fail-closed cleanup of the exact accepted PR65 GPU scope."""
    if stack != h.GPU_STACK or region != h.REGION:
        raise ValueError("GPU teardown restricted to exact PR65 stack and region")
    ids = _preflight(aws)  # Must complete before any EC2/EIP/IAM write.
    iid = ids["iid"]
    if ids["state"] in ("running", "stopping"):
        if ids["state"] == "running":
            run(["aws", "ec2", "stop-instances", "--instance-ids", iid,
                 "--region", region], capture=False)
        run(["aws", "ec2", "wait", "instance-stopped", "--instance-ids", iid,
             "--region", region], capture=False)
    if ids["eip_owner"] != h.CPU_ID:
        response = h.restore_cpu(aws, iid, allow_live=True)
        if response["owner"] != h.CPU_ID:
            raise RuntimeError("CPU EIP return not confirmed")
    eip = h._eip(aws)
    if (eip.get("InstanceId") != h.CPU_ID or
            h._tags(eip).get("StudioDcvInstanceId") != h.CPU_ID):
        raise RuntimeError("Shared EIP not back on original CPU")
    h._dns(aws)
    acm.revoke_gpu(aws, iid)  # Exact GPU session only, original CPU grant preserved.
    run(["aws", "cloudformation", "delete-stack", "--stack-name", ids["arn"],
         "--region", region], capture=False)
    run(["aws", "cloudformation", "wait", "stack-delete-complete",
         "--stack-name", ids["arn"], "--region", region], capture=False)
    stacks = aws(region, "cloudformation", "describe-stacks",
                 "--stack-name", ids["arn"])["Stacks"]
    if len(stacks) != 1 or stacks[0]["StackId"] != ids["arn"] or stacks[0]["StackStatus"] != "DELETE_COMPLETE":
        raise RuntimeError("Exact GPU CloudFormation DELETE_COMPLETE not verified")
    _gone(aws, "instance", "ec2", "describe-instances", "--instance-ids", iid)
    _gone(aws, "sg", "ec2", "describe-security-groups", "--group-ids", ids["sg"])
    _gone(aws, "lt", "ec2", "describe-launch-templates",
          "--launch-template-ids", ids["lt"])
    vol = _absent(lambda: _one(aws, "ec2", "describe-volumes",
                               "--volume-ids", ids["vid"]), MISSING["volume"])
    if vol is not None:
        _owned(vol)
        if vol["VolumeId"] != ids["vid"] or vol["State"] != "available" or vol.get("Attachments"):
            raise RuntimeError("Retained EBS not detached; do not delete other volumes")
        run(["aws", "ec2", "delete-volume", "--volume-id", ids["vid"],
             "--region", region], capture=False)
    for attempt in range(12):
        if _absent(lambda: _one(aws, "ec2", "describe-volumes",
                                "--volume-ids", ids["vid"]), MISSING["volume"]) is None:
            break
        if attempt == 11:
            raise RuntimeError("Retained GPU EBS deletion not independently verified")
        sleep(3)
    _verify_no_orphans(aws, ids)
    # Keep both independent PR65 schedules until the instance, EBS and stack are gone.
    for name, action, payload in (
        (ids["schedules"][0], guard.TERMINATE, {"InstanceIds": [iid]}),
        (ids["schedules"][1], guard.DELETE_VOLUME, {"VolumeId": ids["vid"]}),
    ):
        _schedule(aws, name, action, payload)
        run(["aws", "scheduler", "delete-schedule", "--group-name", guard.GROUP,
             "--name", name, "--region", region], capture=False)
        if _absent(lambda: aws(region, "scheduler", "get-schedule",
                               "--group-name", guard.GROUP, "--name", name),
                   MISSING["schedule"]) is not None:
            raise RuntimeError("GPU expiration schedule removal not verified: " + name)
    remaining = aws(region, "scheduler", "list-schedules", "--group-name", guard.GROUP).get("Schedules", [])
    if any(x.get("Name") in ids["schedules"] for x in remaining):
        raise RuntimeError("GPU expiration schedule remains")
    eip = h._eip(aws)
    if (eip.get("InstanceId") != h.CPU_ID or
            h._tags(eip).get("StudioDcvInstanceId") != h.CPU_ID):
        raise RuntimeError("Original CPU EIP owner changed after cleanup")
    h._dns(aws)
    if acm._get_role_state(aws, iid):
        raise RuntimeError("GPU ACM permission remained after cleanup")
    return {"stack": stack, "state": "DELETE_COMPLETE", "instance": iid,
            "volume_removed": ids["vid"], "schedules_removed": ids["schedules"],
            "eip_owner": h.CPU_ID, "cpu_certificate_access": "preserved"}
