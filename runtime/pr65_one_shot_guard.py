"""Small Task 7-style expiry guard for ONE supervised PR #65 CLI instance.

No AWS resources are created by import/plan. arm() must not be invoked until
an independently approved launch has produced a tagged instance and root EBS.
"""
import datetime as dt
import json
import re

GROUP = "studio-task7-pr61"  # existing group; a NEW scoped role is required
ROLE = "arn:aws:iam::401233741942:role/studio-pr65-cli-expiration"
TERMINATE = "arn:aws:scheduler:::aws-sdk:ec2:terminateInstances"
DELETE_VOLUME = "arn:aws:scheduler:::aws-sdk:ec2:deleteVolume"


def _tags(resource):
    result = {}
    for tag in resource.get("Tags", []):
        key, value = tag["Key"], tag["Value"]
        if key in result:
            raise ValueError("Duplicate ownership tag: " + key)
        result[key] = value
    return result


def verify_ownership(instance, volume, stack, token):
    """Fail closed before creating schedules or acting on instance/volume."""
    if not re.fullmatch(r"studio-gpu-cli-[a-z0-9-]{1,25}", stack):
        raise ValueError("Invalid scoped test stack")
    if not re.fullmatch(r"[a-z0-9-]{12,64}", token):
        raise ValueError("Invalid cleanup token")
    iid, vid = instance["InstanceId"], volume["VolumeId"]
    if not re.fullmatch(r"i-[0-9a-f]{8,17}", iid) or not re.fullmatch(r"vol-[0-9a-f]{8,17}", vid):
        raise ValueError("Invalid EC2 physical IDs")
    for resource in (instance, volume):
        tags = _tags(resource)
        if tags.get("StudioStack") != stack or tags.get("StudioCleanupToken") != token:
            raise ValueError("Instance or root EBS ownership tags do not match")
    if instance.get("State", {}).get("Name") not in ("pending", "running", "stopped", "stopping"):
        raise ValueError("Unexpected EC2 state")
    matches = [m for m in instance.get("BlockDeviceMappings", []) if m.get("DeviceName") == "/dev/sda1"]
    if len(matches) != 1 or matches[0].get("Ebs", {}).get("VolumeId") != vid:
        raise ValueError("Root EBS ID not confirmed from instance")
    if matches[0]["Ebs"].get("DeleteOnTermination") is not False:
        raise ValueError("Expected retained root EBS volume")
    if volume.get("State") != "in-use" or not any(
        a.get("InstanceId") == iid and a.get("VolumeId") == vid and a.get("Device") == "/dev/sda1"
        for a in volume.get("Attachments", [])
    ):
        raise ValueError("Root EBS attachment not independently confirmed")
    return iid, vid


def _schedule(name, action, payload, when, role):
    return {
        "Name": name,
        "GroupName": GROUP,
        "State": "ENABLED",
        "ScheduleExpression": "at(" + when.strftime("%Y-%m-%dT%H:%M:%S") + ")",
        "ScheduleExpressionTimezone": "UTC",
        "FlexibleTimeWindow": {"Mode": "OFF"},
        "ActionAfterCompletion": "DELETE",
        "Target": {
            "Arn": action,
            "RoleArn": role,
            "Input": json.dumps(payload, separators=(",", ":")),
            "RetryPolicy": {"MaximumRetryAttempts": 3, "MaximumEventAgeInSeconds": 3600},
        },
    }


def plan(instance, volume, stack, token, now, *, minutes=150, volume_delay_minutes=45, role=ROLE):
    """Plan two independent one-time schedules; do not invoke AWS."""
    iid, vid = verify_ownership(instance, volume, stack, token)
    if now.tzinfo is None or now.utcoffset() != dt.timedelta(0):
        raise ValueError("Expected timezone-aware UTC time")
    if not 60 <= minutes <= 240 or not 30 <= volume_delay_minutes <= 120:
        raise ValueError("Expiration deadline outside supervised test bounds")
    if role != ROLE:
        raise ValueError("Unexpected scheduler execution role")
    stop_at = now + dt.timedelta(minutes=minutes)
    ebs_at = stop_at + dt.timedelta(minutes=volume_delay_minutes)
    return {
        "stack": stack, "token": token, "instance_id": iid, "volume_id": vid,
        "terminate_at": stop_at.isoformat(), "volume_cleanup_at": ebs_at.isoformat(),
        "termination": _schedule("studio-pr65-i-" + iid, TERMINATE,
                                 {"InstanceIds": [iid]}, stop_at, role),
        "volume_cleanup": _schedule("studio-pr65-v-" + vid, DELETE_VOLUME,
                                    {"VolumeId": vid}, ebs_at, role),
    }


def _verify_schedule(scheduler, spec):
    got = scheduler.get_schedule(Name=spec["Name"], GroupName=spec["GroupName"])
    for field in ("State", "ScheduleExpression", "ScheduleExpressionTimezone", "ActionAfterCompletion"):
        if got.get(field) != spec[field]:
            raise RuntimeError("Expiration schedule readback mismatch: " + field)
    if got.get("FlexibleTimeWindow") != spec["FlexibleTimeWindow"]:
        raise RuntimeError("Expiration schedule flexible-time mismatch")
    target, expected = got.get("Target", {}), spec["Target"]
    if target.get("Arn") != expected["Arn"] or target.get("RoleArn") != expected["RoleArn"]:
        raise RuntimeError("Expiration schedule target or role mismatch")
    if json.loads(target.get("Input", "null")) != json.loads(expected["Input"]):
        raise RuntimeError("Expiration schedule target input mismatch")
    if target.get("RetryPolicy") != expected["RetryPolicy"]:
        raise RuntimeError("Expiration schedule retry settings mismatch")


def _verify_plan_scope(proposal):
    """Do not trust mutable plan dictionaries handed to the AWS writer."""
    iid, vid = proposal["instance_id"], proposal["volume_id"]
    expected = (
        ("termination", TERMINATE, {"InstanceIds": [iid]}, "studio-pr65-i-" + iid),
        ("volume_cleanup", DELETE_VOLUME, {"VolumeId": vid}, "studio-pr65-v-" + vid),
    )
    for label, action, payload, name in expected:
        spec = proposal[label]
        if (spec.get("Name") != name or spec.get("GroupName") != GROUP or
                spec.get("State") != "ENABLED" or spec.get("ActionAfterCompletion") != "DELETE"):
            raise ValueError("Unexpected expiration schedule identity")
        target = spec.get("Target", {})
        if (target.get("Arn") != action or target.get("RoleArn") != ROLE or
                json.loads(target.get("Input", "null")) != payload):
            raise ValueError("Unexpected expiration schedule target or input")


def arm(scheduler, ec2, proposal):
    """Arm both schedules; on error request immediate instance termination.

    An unattached EBS volume may still remain and MUST be reviewed manually.
    """
    _verify_plan_scope(proposal)
    instance_id = proposal["instance_id"]
    if not re.fullmatch(r"i-[0-9a-f]{8,17}", instance_id):
        raise ValueError("Invalid instance ID")
    try:
        for label in ("termination", "volume_cleanup"):
            spec = proposal[label]
            scheduler.create_schedule(**spec)
            _verify_schedule(scheduler, spec)
    except Exception as exc:
        try:
            ec2.terminate_instances(InstanceIds=[instance_id])
        except Exception as termination_error:
            raise RuntimeError(
                "Guard failed AND emergency termination failed; intervene manually for EC2 and retained EBS"
            ) from termination_error
        raise RuntimeError(
            "Guard failed; emergency termination requested; retained EBS must be checked and deleted manually"
        ) from exc
    return {"instance_id": instance_id, "volume_id": proposal["volume_id"],
            "schedules": [proposal[x]["Name"] for x in ("termination", "volume_cleanup")],
            "guard_armed": True}
