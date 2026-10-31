"""Independent fail-closed cleanup for one GPU CLI acceptance stack."""
import os
import re
from datetime import datetime, timezone

BUSY = {"CREATE_IN_PROGRESS", "ROLLBACK_IN_PROGRESS", "DELETE_IN_PROGRESS",
        "UPDATE_IN_PROGRESS", "UPDATE_COMPLETE_CLEANUP_IN_PROGRESS",
        "UPDATE_ROLLBACK_IN_PROGRESS", "UPDATE_ROLLBACK_COMPLETE_CLEANUP_IN_PROGRESS",
        "IMPORT_IN_PROGRESS", "IMPORT_ROLLBACK_IN_PROGRESS",
        "IMPORT_ROLLBACK_COMPLETE_CLEANUP_IN_PROGRESS", "REVIEW_IN_PROGRESS"}
DELETABLE = {"CREATE_COMPLETE", "CREATE_FAILED", "ROLLBACK_COMPLETE",
             "ROLLBACK_FAILED", "UPDATE_COMPLETE", "UPDATE_ROLLBACK_COMPLETE",
             "UPDATE_ROLLBACK_FAILED", "DELETE_FAILED", "IMPORT_COMPLETE",
             "IMPORT_ROLLBACK_FAILED"}

def verify(resource, stack, token):
    tags = {}
    for item in resource.get("Tags", []):
        key, value = item["Key"], item["Value"]
        if key in tags and tags[key] != value:
            raise RuntimeError("Conflicting ownership tags")
        tags[key] = value
    if tags.get("StudioCleanupToken") != token or tags.get("StudioStack") != stack:
        raise RuntimeError("Resource ownership mismatch")

def pages(ec2, method, key, filters):
    seen, next_token = set(), None
    while True:
        kwargs = {"Filters": filters}
        if next_token:
            kwargs["NextToken"] = next_token
        response = getattr(ec2, method)(**kwargs)
        yield from response.get(key, [])
        next_token = response.get("NextToken")
        if not next_token:
            break
        if next_token in seen:
            raise RuntimeError("Repeated pagination token")
        seen.add(next_token)

def reconcile(cf, ec2, stack, token):
    if not re.fullmatch(r"studio-gpu-cli-[a-z0-9-]{1,110}", stack or ""):
        raise ValueError("Invalid test stack")
    if not re.fullmatch(r"[a-z0-9-]{12,64}", token or ""):
        raise ValueError("Invalid cleanup token")
    try:
        records = cf.describe_stacks(StackName=stack)["Stacks"]
    except Exception as exc:
        error = getattr(exc, "response", {}).get("Error", {})
        if error.get("Code") != "ValidationError" or "does not exist" not in error.get("Message", ""):
            raise
        records = []
    if len(records) > 1:
        raise RuntimeError("Ambiguous stack")
    info = records[0] if records else None
    if info:
        if info["StackName"] != stack:
            raise RuntimeError("Stack name mismatch")
        verify(info, stack, token)
    filters = [{"Name": "tag:StudioCleanupToken", "Values": [token]},
               {"Name": "tag:StudioStack", "Values": [stack]}]
    instances = [i for r in pages(ec2, "describe_instances", "Reservations", filters)
                 for i in r.get("Instances", [])]
    volumes = list(pages(ec2, "describe_volumes", "Volumes", filters))
    groups = list(pages(ec2, "describe_security_groups", "SecurityGroups", filters))
    templates = list(pages(ec2, "describe_launch_templates", "LaunchTemplates", filters))
    for item in instances + volumes + groups + templates:
        verify(item, stack, token)
    if info:
        state = info["StackStatus"]
        if state in BUSY:
            return {"complete": False, "reason": "stack busy", "status": state}
        if state in DELETABLE:
            cf.delete_stack(StackName=stack)
            return {"complete": False, "reason": "stack deletion requested", "status": state}
        if state != "DELETE_COMPLETE":
            raise RuntimeError("Unrecognized stack status: " + state)
    pending = []
    for instance in instances:
        state = instance["State"]["Name"]
        if state not in ("terminated", "shutting-down"):
            ec2.terminate_instances(InstanceIds=[instance["InstanceId"]])
            pending.append(instance["InstanceId"])
        else:
            # Even terminated EC2 instances can remain visible for a while.
            # Never report complete while an owned instance is still listed.
            pending.append(instance["InstanceId"])
    if pending:
        return {"complete": False, "reason": "instance termination pending"}
    if any(v["State"] != "available" for v in volumes):
        return {"complete": False, "reason": "volume not detached"}
    for volume in volumes:
        ec2.delete_volume(VolumeId=volume["VolumeId"])
    if volumes:
        return {"complete": False, "reason": "volume deletion verification pending"}
    for group in groups:
        ec2.delete_security_group(GroupId=group["GroupId"])
    if groups:
        return {"complete": False, "reason": "security group deletion verification pending"}
    for template in templates:
        ec2.delete_launch_template(LaunchTemplateId=template["LaunchTemplateId"])
    if templates:
        return {"complete": False, "reason": "launch template deletion verification pending"}
    if info:
        return {"complete": False, "reason": "stack deletion verification pending"}
    return {"complete": True, "reason": "stack and scoped resources absent"}

def handler(event, context):
    stack = os.environ["STUDIO_TEST_STACK"]
    token = os.environ["STUDIO_CLEANUP_TOKEN"]
    deadline = datetime.fromisoformat(os.environ["STUDIO_NOT_BEFORE_UTC"].replace("Z", "+00:00"))
    if deadline.tzinfo is None:
        raise ValueError("UTC deadline must be timezone-aware")
    if datetime.now(timezone.utc) < deadline:
        return {"complete": False, "reason": "deadline not reached"}
    import boto3
    result = reconcile(boto3.client("cloudformation"), boto3.client("ec2"), stack, token)
    # A successful invocation must mean independently verified absence.
    # Pending after the deadline raises to trigger Lambda Errors alarm/retries.
    if not result["complete"] and os.environ.get("STUDIO_FAIL_PENDING") == "true":
        raise RuntimeError("Deadline cleanup incomplete: " + str(result))
    return result
