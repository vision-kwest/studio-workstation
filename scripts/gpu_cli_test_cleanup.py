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

RESOURCE_TYPES = {
    "AWS::EC2::Instance": "Instances",
    "AWS::EC2::SecurityGroup": "SecurityGroups",
    "AWS::EC2::LaunchTemplate": "LaunchTemplates",
}
INVENTORY_KEYS = ("Instances", "SecurityGroups", "LaunchTemplates", "Volumes")

def durable_inventory(ddb, table, cf, stack, token, info, instances, volumes, groups, templates):
    """Accumulate independently retained physical IDs before CloudFormation deletion.

    Missing or changed ownership tags are never used as authorization to delete.
    """
    scope = stack + "#" + token
    key = {"Scope": {"S": scope}}
    existing = ddb.get_item(TableName=table, Key=key, ConsistentRead=True).get("Item", {})
    recorded = {name: set(existing.get(name, {}).get("SS", [])) for name in INVENTORY_KEYS}
    stack_id = info.get("StackId") if info else None
    previous_id = existing.get("StackId", {}).get("S")
    if previous_id and stack_id and previous_id != stack_id:
        raise RuntimeError("CloudFormation stack ID changed under cleanup scope")
    found = {name: set() for name in INVENTORY_KEYS}
    if info:
        next_token = None
        while True:
            args = {"StackName": stack}
            if next_token:
                args["NextToken"] = next_token
            response = cf.list_stack_resources(**args)
            for resource in response.get("StackResourceSummaries", []):
                category = RESOURCE_TYPES.get(resource.get("ResourceType"))
                if category and resource.get("PhysicalResourceId"):
                    found[category].add(resource["PhysicalResourceId"])
            next_token = response.get("NextToken")
            if not next_token:
                break
    for category, resources, field in (
        ("Instances", instances, "InstanceId"),
        ("Volumes", volumes, "VolumeId"),
        ("SecurityGroups", groups, "GroupId"),
        ("LaunchTemplates", templates, "LaunchTemplateId"),
    ):
        found[category].update(x[field] for x in resources)
    for instance in instances:
        for mapping in instance.get("BlockDeviceMappings", []):
            volume_id = mapping.get("Ebs", {}).get("VolumeId")
            if volume_id:
                found["Volumes"].add(volume_id)
    # Capture all observed IDs durably, including CFN-discovered IDs with absent tags.
    # Such resources will block cleanup until ownership is independently verified.
    for category in INVENTORY_KEYS:
        new = found[category] - recorded[category]
        if new:
            ddb.update_item(
                TableName=table, Key=key,
                UpdateExpression="ADD #ids :ids",
                ExpressionAttributeNames={"#ids": category},
                ExpressionAttributeValues={":ids": {"SS": sorted(new)}},
            )
            recorded[category].update(new)
    if stack_id and not previous_id:
        ddb.update_item(
            TableName=table, Key=key,
            UpdateExpression="SET #sid = if_not_exists(#sid, :sid)",
            ExpressionAttributeNames={"#sid": "StackId"},
            ExpressionAttributeValues={":sid": {"S": stack_id}},
        )
    return recorded

def verify_inventory(ec2, recorded, instances, volumes, groups, templates):
    observed = {
        "Instances": {x["InstanceId"]: x for x in instances},
        "Volumes": {x["VolumeId"]: x for x in volumes},
        "SecurityGroups": {x["GroupId"]: x for x in groups},
        "LaunchTemplates": {x["LaunchTemplateId"]: x for x in templates},
    }
    # An ID missing from the tagged inventory must not silently disappear:
    # verify via direct, unfiltered Describe; mismatch is a hard error.
    methods = {
        "Instances": ("describe_instances", "InstanceIds"),
        "Volumes": ("describe_volumes", "VolumeIds"),
        "SecurityGroups": ("describe_security_groups", "GroupIds"),
        "LaunchTemplates": ("describe_launch_templates", "LaunchTemplateIds"),
    }
    for category, ids in recorded.items():
        for physical_id in ids - observed[category].keys():
            method, argument = methods[category]
            try:
                result = getattr(ec2, method)(**{argument: [physical_id]})
            except Exception as exc:
                # An unknown API error cannot be interpreted as absence.
                code = getattr(exc, "response", {}).get("Error", {}).get("Code")
                absent = {
                    "Instances": "InvalidInstanceID.NotFound",
                    "Volumes": "InvalidVolume.NotFound",
                    "SecurityGroups": "InvalidGroup.NotFound",
                    "LaunchTemplates": "InvalidLaunchTemplateId.NotFound",
                }
                if code == absent[category]:
                    continue
                raise
            if category == "Instances":
                items = [x for reservation in result.get("Reservations", [])
                         for x in reservation.get("Instances", [])]
            else:
                items = result.get(category, [])
            if items:
                raise RuntimeError("Recorded resource missing ownership tags: " + physical_id)
            # A successful but empty Describe response is not reliable absence.
            raise RuntimeError("Recorded resource not verifiably absent: " + physical_id)

def reconcile(cf, ec2, stack, token, ddb=None, table=None):
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
    if ddb is not None:
        if not table:
            raise ValueError("Durable inventory table is required")
        recorded = durable_inventory(ddb, table, cf, stack, token, info,
                                     instances, volumes, groups, templates)
        verify_inventory(ec2, recorded, instances, volumes, groups, templates)
        # Never initiate stack deletion until all resource classes were
        # inventoried, including the retained root EBS volume.
        if info and info["StackStatus"] in DELETABLE and any(
                not recorded[name] for name in INVENTORY_KEYS):
            return {"complete": False, "reason": "durable inventory incomplete"}
        if not info and any(not recorded[name] for name in INVENTORY_KEYS):
            return {"complete": False, "reason": "durable inventory incomplete"}

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
    result = reconcile(boto3.client("cloudformation"), boto3.client("ec2"), stack, token,
                       boto3.client("dynamodb"), os.environ["STUDIO_INVENTORY_TABLE"])
    # A successful invocation must mean independently verified absence.
    # Pending after the deadline raises to trigger Lambda Errors alarm/retries.
    if not result["complete"] and os.environ.get("STUDIO_FAIL_PENDING") == "true":
        raise RuntimeError("Deadline cleanup incomplete: " + str(result))
    return result
