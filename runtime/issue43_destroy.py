"""CloudFormation owns the disposable workstation; local disks have no separate lifecycle."""
import json
import re
import subprocess
import time

from runtime import issue43_hostname_handoff as h
from runtime import pr65_one_shot_guard as guard
from runtime import pr65_gpu_create as creator
from runtime.workstation_inventory import context, stack_record


def _absent(call, code):
    try:
        return call()
    except subprocess.CalledProcessError as exc:
        if code in (exc.stderr or ""):
            return None
        raise


def _remaining(aws, region, filters):
    instances = aws(region, "ec2", "describe-instances", "--filters", *filters)["Reservations"]
    active = [vm for r in instances for vm in r["Instances"] if vm["State"]["Name"] != "terminated"]
    volumes = aws(region, "ec2", "describe-volumes", "--filters", *filters)["Volumes"]
    if active or volumes:
        raise RuntimeError("Native teardown incomplete; remaining instances/disks: " +
                           json.dumps({"instances": [v["InstanceId"] for v in active],
                                       "volumes": [v["VolumeId"] for v in volumes]}))


def _remove_safety(aws, region, stack, identity, selected=None):
    """Remove guards only after independent absence checks, including interrupted creates."""
    token = identity["token"]
    filters = ["Name=tag:StudioStack,Values=" + stack, "Name=tag:StudioCleanupToken,Values=" + token]
    _remaining(aws, region, filters)
    for prefix, action, field in (("studio-expiry-", guard.DELETE_STACK, "StackName"),
                                  ("studio-fallback-", guard.TERMINATE, "InstanceIds")):
        name = prefix + token
        spec = _absent(lambda: aws(region, "scheduler", "get-schedule", "--group-name", guard.GROUP,
                                   "--name", name), "ResourceNotFoundException")
        if spec is None:
            continue  # Native ActionAfterCompletion may already have removed it.
        target = spec.get("Target", {})
        payload = json.loads(target.get("Input", "null"))
        if target.get("Arn") != action or target.get("RoleArn") != identity["role"] or not isinstance(payload, dict) or set(payload) != {field}:
            raise ValueError("Unexpected safety schedule target; preserve it for review")
        if field == "StackName":
            expected = selected["stack_id"] if selected else None
            if payload[field] != stack and payload[field] != expected and not (
                    expected is None and payload[field].startswith(f"arn:aws:cloudformation:{region}:{h.ACCOUNT}:stack/{stack}/")):
                raise ValueError("Expiration schedule belongs to another stack")
        else:
            ids = payload[field]
            if not isinstance(ids, list) or len(ids) != 1 or not re.fullmatch(r"i-[0-9a-f]{8,17}", ids[0]):
                raise ValueError("Invalid fallback instance")
            if selected and selected["instance"] and ids != [selected["instance"]]:
                raise ValueError("Fallback belongs to another workstation")
            vm = _absent(lambda: h._items(aws, ids[0])[ids[0]], "InvalidInstanceID.NotFound")
            if vm and (vm["State"]["Name"] != "terminated" or
                       (h._tags(vm).get("StudioStack"), h._tags(vm).get("StudioCleanupToken")) != (stack, token)):
                raise ValueError("Fallback instance deletion/ownership is not verified")
        aws(region, "scheduler", "delete-schedule", "--group-name", guard.GROUP, "--name", name)
        if _absent(lambda: aws(region, "scheduler", "get-schedule", "--group-name", guard.GROUP,
                                "--name", name), "ResourceNotFoundException") is not None:
            raise RuntimeError("Schedule deletion not verified")
    if identity["state"] != "DELETE_COMPLETE":
        if identity["state"] != "DELETE_IN_PROGRESS":
            aws(region, "cloudformation", "delete-stack", "--stack-name", identity["arn"])
        aws(region, "cloudformation", "wait", "stack-delete-complete", "--stack-name", identity["arn"])
    if identity["role"] and _absent(lambda: aws(region, "iam", "get-role", "--role-name", identity["role"].rsplit("/", 1)[-1]),
               "NoSuchEntity") is not None:
        raise RuntimeError("Safety role deletion not verified")


def destroy(aws, run, stack, region, *, sleep=time.sleep):
    """Retry native DeleteStack, never a volume-only delete or force/retain operation."""
    if region != h.REGION:
        raise ValueError("Only validated us-east-1 is supported")
    record = stack_record(aws, region, stack)
    if record is None:
        identity = creator.safety(aws, region, stack, cleanup=True)
        _remove_safety(aws, region, stack, identity)
        return {"stack": stack, "state": "DELETE_COMPLETE", "safety_removed": True}
    selected = context(aws, region, stack)
    stack = selected["stack"]
    allowed = {"Workstation": "AWS::EC2::Instance", "WorkstationSG": "AWS::EC2::SecurityGroup"}
    for r in selected["resources"]:
        if allowed.get(r.get("LogicalResourceId")) != r.get("ResourceType"):
            raise ValueError("Unexpected resource in disposable workstation stack")
    vm = selected["vm"]
    disks = [m["Ebs"] for m in vm.get("BlockDeviceMappings", []) if "Ebs" in m]
    if vm.get("State", {}).get("Name") not in (None, "terminated") and (
            not disks or any(d.get("DeleteOnTermination") is not True for d in disks)):
        raise ValueError("All workstation disks must have DeleteOnTermination=true")
    identity = None
    # Optional hostname errors cannot prevent native cost-stopping destruction.
    from runtime import independent_eip
    warnings = []
    if selected["profile"] != "publisher":
        try:
            independent_eip.release_if_owned(aws, selected, None, destroying=True)
        except Exception as exc:
            warnings.append(str(exc))
    if selected["stack_status"] != "DELETE_COMPLETE":
        if selected["stack_status"] != "DELETE_IN_PROGRESS":
            aws(region, "cloudformation", "delete-stack", "--stack-name", selected["stack_id"])
        aws(region, "cloudformation", "wait", "stack-delete-complete", "--stack-name", selected["stack_id"])
    done = stack_record(aws, region, selected["stack_id"])
    if not done or done["StackStatus"] != "DELETE_COMPLETE":
        raise RuntimeError("Immutable workstation DELETE_COMPLETE not verified")
    filters = ["Name=tag:StudioStackId,Values=" + selected["stack_id"]]
    for attempt in range(12):
        try:
            _remaining(aws, region, filters)
            break
        except RuntimeError:
            if attempt == 11:
                raise
            sleep(3)
    if selected["instance"]:
        terminated = _absent(lambda: h._items(aws, selected["instance"])[selected["instance"]], "InvalidInstanceID.NotFound")
        if terminated and terminated.get("State", {}).get("Name") != "terminated":
            raise RuntimeError("Original instance is not terminated")
    for resource in selected["resources"]:
        if resource["ResourceType"] == "AWS::EC2::SecurityGroup" and resource.get("PhysicalResourceId"):
            if _absent(lambda: aws(region, "ec2", "describe-security-groups", "--group-ids", resource["PhysicalResourceId"]),
                       "InvalidGroup.NotFound") is not None:
                raise RuntimeError("Workstation security group remains after native deletion")
    for disk in disks:
        if _absent(lambda: aws(region, "ec2", "describe-volumes", "--volume-ids", disk["VolumeId"]),
                   "InvalidVolume.NotFound") is not None:
            raise RuntimeError("Original attached disk still exists after native teardown")
    if selected["profile"] != "publisher" and selected["instance"]:
        independent_eip.release_if_owned(aws, selected, None, destroying=True)
    if selected["profile"] == "gpu":
        identity = creator.safety(aws, region, stack, arn=selected["safety_stack"], cleanup=True)
        if identity["token"] != selected["token"]:
            raise ValueError("Safety-stack ownership differs from workstation; workload removed, guard preserved")
        _remove_safety(aws, region, stack, identity, selected)
    return {"stack": stack, "stack_id": selected["stack_id"], "state": "DELETE_COMPLETE",
            "instance": selected["instance"], "native_disks_removed": True, "recovered_warnings": warnings}
