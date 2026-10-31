"""Independent, bounded cleanup reconciler for ONE disposable GPU CLI test stack.

Run as a scheduled Lambda with a dedicated IAM role. No deployment in this module.
A stack must carry StudioCleanupToken=<token>; EC2 and EBS cleanup requires
the same token. Never enumerate or delete arbitrary account resources.
"""
import os

ACTIVE = {"CREATE_IN_PROGRESS", "UPDATE_IN_PROGRESS", "UPDATE_COMPLETE_CLEANUP_IN_PROGRESS",
          "UPDATE_ROLLBACK_IN_PROGRESS", "UPDATE_ROLLBACK_COMPLETE_CLEANUP_IN_PROGRESS",
          "DELETE_IN_PROGRESS", "REVIEW_IN_PROGRESS", "IMPORT_IN_PROGRESS"}
FAILED = {"CREATE_FAILED", "ROLLBACK_FAILED", "UPDATE_ROLLBACK_FAILED",
          "DELETE_FAILED", "IMPORT_ROLLBACK_FAILED"}
GONE = {"DELETE_COMPLETE", "ROLLBACK_COMPLETE"}


def reconcile(cf, ec2, stack, token):
    """One bounded attempt. Retry on future scheduler invocation until complete."""
    if not stack or not token or not stack.startswith("studio-gpu-cli-"):
        raise ValueError("Missing or invalid exact test stack/token")
    try:
        result = cf.describe_stacks(StackName=stack)["Stacks"]
    except cf.exceptions.ClientError as exc:
        if exc.response.get("Error", {}).get("Code") == "ValidationError" and "does not exist" in str(exc):
            result = []
        else:
            raise
    if result:
        info = result[0]
        if info["StackName"] != stack:
            raise RuntimeError("Stack identity mismatch")
        tags = {t["Key"]: t["Value"] for t in info.get("Tags", [])}
        if tags.get("StudioCleanupToken") != token:
            raise RuntimeError("Refusing to touch stack without matching cleanup token")
        state = info["StackStatus"]
        if state in ACTIVE:
            return {"complete": False, "reason": "stack busy", "status": state}
        if state not in GONE:
            # DELETE_FAILED is retried, with retained resources reconciled below.
            try:
                cf.delete_stack(StackName=stack)
            except Exception as exc:
                if state not in FAILED:
                    raise
                return {"complete": False, "reason": "delete retry pending", "error": str(exc)}
            return {"complete": False, "reason": "stack deletion requested", "status": state}

    # CloudFormation is absent or terminal; only explicitly tagged resources.
    instances = ec2.describe_instances(Filters=[
        {"Name": "tag:StudioCleanupToken", "Values": [token]},
        {"Name": "tag:StudioStack", "Values": [stack]},
    ])
    pending = []
    for reservation in instances.get("Reservations", []):
        for instance in reservation.get("Instances", []):
            state = instance["State"]["Name"]
            for mapping in instance.get("BlockDeviceMappings", []):
                vol = mapping.get("Ebs", {}).get("VolumeId")
                if vol:
                    ec2.create_tags(Resources=[vol], Tags=[
                        {"Key": "StudioCleanupToken", "Value": token},
                        {"Key": "StudioStack", "Value": stack},
                    ])
            if state not in ("terminated", "shutting-down"):
                ec2.terminate_instances(InstanceIds=[instance["InstanceId"]])
                pending.append(instance["InstanceId"])
            elif state == "shutting-down":
                pending.append(instance["InstanceId"])
    if pending:
        return {"complete": False, "reason": "instance termination pending", "instances": pending}

    volumes = ec2.describe_volumes(Filters=[
        {"Name": "tag:StudioCleanupToken", "Values": [token]},
        {"Name": "tag:StudioStack", "Values": [stack]},
    ])
    for volume in volumes.get("Volumes", []):
        if volume["State"] != "available":
            return {"complete": False, "reason": "EBS not detached", "volume": volume["VolumeId"]}
        ec2.delete_volume(VolumeId=volume["VolumeId"])

    groups = ec2.describe_security_groups(Filters=[
        {"Name": "tag:StudioCleanupToken", "Values": [token]},
        {"Name": "tag:StudioStack", "Values": [stack]},
    ])
    for group in groups.get("SecurityGroups", []):
        ec2.delete_security_group(GroupId=group["GroupId"])

    if volumes.get("Volumes") or groups.get("SecurityGroups"):
        return {"complete": False, "reason": "resource deletion verification pending"}
    return {"complete": True, "reason": "all tagged test resources absent"}


def handler(event, context):
    """Requires a dedicated scheduled invocation; no client-provided arbitrary scope."""
    stack = os.environ["STUDIO_TEST_STACK"]
    token = os.environ["STUDIO_CLEANUP_TOKEN"]
    import boto3
    return reconcile(boto3.client("cloudformation"), boto3.client("ec2"), stack, token)
