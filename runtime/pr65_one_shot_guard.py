"""Native stack expiration with an independent, instance-only cost fallback."""
import datetime as dt
import json
import re

GROUP = "studio-task7-pr61"
TERMINATE = "arn:aws:scheduler:::aws-sdk:ec2:terminateInstances"
DELETE_STACK = "arn:aws:scheduler:::aws-sdk:cloudformation:deleteStack"
FALLBACK_MINUTES = 5


def _tags(resource):
    result = {}
    for tag in resource.get("Tags", []):
        key, value = tag["Key"], tag["Value"]
        if key in result:
            raise ValueError("Duplicate ownership tag: " + key)
        result[key] = value
    return result


def verify_ownership(instance, stack, token, stack_id):
    """One disposable workstation: all local EBS must follow instance termination."""
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9-]{0,127}", stack) or not re.fullmatch(r"[a-f0-9-]{36}", token):
        raise ValueError("Invalid workstation identity")
    iid = instance.get("InstanceId", "")
    tags = _tags(instance)
    if (not re.fullmatch(r"i-[0-9a-f]{8,17}", iid) or
            (tags.get("StudioStack"), tags.get("StudioCleanupToken"),
             tags.get("aws:cloudformation:stack-id")) != (stack, token, stack_id)):
        raise ValueError("Workstation ownership mismatch")
    disks = [m["Ebs"] for m in instance.get("BlockDeviceMappings", []) if "Ebs" in m]
    if not disks or any(d.get("DeleteOnTermination") is not True for d in disks):
        raise ValueError("Every local EBS disk must be deleted on instance termination")
    return iid


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


def validate_durations(minutes):
    if type(minutes) is not int or not 60 <= minutes <= 240:
        raise ValueError("Expiration must be 60–240 whole minutes")


def plan(instance, stack, token, now, *, minutes=150, role, stack_id, terminate_at=None):
    iid = verify_ownership(instance, stack, token, stack_id)
    if now.tzinfo is None or now.utcoffset() != dt.timedelta(0):
        raise ValueError("Expected timezone-aware UTC time")
    validate_durations(minutes)
    deadline = terminate_at or now + dt.timedelta(minutes=minutes)
    fallback = deadline + dt.timedelta(minutes=FALLBACK_MINUTES)
    return {"stack": stack, "token": token, "stack_id": stack_id, "role": role,
            "instance_id": iid, "terminate_at": deadline.isoformat(),
            "termination": _schedule("studio-expiry-" + token, DELETE_STACK,
                                     {"StackName": stack_id}, deadline, role),
            "fallback": _schedule("studio-fallback-" + token, TERMINATE,
                                  {"InstanceIds": [iid]}, fallback, role)}


def _verify_schedule(scheduler, spec):
    got = scheduler.get_schedule(Name=spec["Name"], GroupName=spec["GroupName"])
    for field in ("Name", "GroupName", "State", "ScheduleExpression", "ScheduleExpressionTimezone", "ActionAfterCompletion"):
        if got.get(field) != spec[field]:
            raise RuntimeError("Expiration schedule readback mismatch: " + field)
    if got.get("FlexibleTimeWindow") != spec["FlexibleTimeWindow"]:
        raise RuntimeError("Expiration schedule flexible-time mismatch")
    target, expected = got.get("Target", {}), spec["Target"]
    if set(target) != set(expected) or any(got.get(k) is not None for k in ("StartDate", "EndDate", "KmsKeyArn")):
        raise RuntimeError("Unexpected expiration schedule options")
    if target.get("Arn") != expected["Arn"] or target.get("RoleArn") != expected["RoleArn"]:
        raise RuntimeError("Expiration schedule target or role mismatch")
    if json.loads(target.get("Input", "null")) != json.loads(expected["Input"]):
        raise RuntimeError("Expiration schedule target input mismatch")
    if target.get("RetryPolicy") != expected["RetryPolicy"]:
        raise RuntimeError("Expiration schedule retry settings mismatch")


def _verify_plan_scope(proposal):
    expected = (
        ("termination", DELETE_STACK, {"StackName": proposal["stack_id"]}, "studio-expiry-"),
        ("fallback", TERMINATE, {"InstanceIds": [proposal["instance_id"]]}, "studio-fallback-"),
    )
    for label, action, payload, prefix in expected:
        spec = proposal[label]
        target = spec.get("Target", {})
        if (spec.get("Name") != prefix + proposal["token"] or spec.get("GroupName") != GROUP or
                target.get("Arn") != action or target.get("RoleArn") != proposal["role"] or
                json.loads(target.get("Input", "null")) != payload):
            raise ValueError("Unexpected expiration schedule identity or target")


def arm(scheduler, ec2, proposal):
    """Primary stack deletion is already armed before billing; now pin IDs and fallback."""
    _verify_plan_scope(proposal)
    iid = proposal["instance_id"]
    if not re.fullmatch(r"i-[0-9a-f]{8,17}", iid):
        raise ValueError("Invalid instance ID")
    try:
        scheduler.create_schedule(**proposal["fallback"])
        _verify_schedule(scheduler, proposal["fallback"])
        scheduler.update_schedule(**proposal["termination"])
        _verify_schedule(scheduler, proposal["termination"])
    except Exception as exc:
        try:
            ec2.terminate_instances(InstanceIds=[iid])
        except Exception as stop_error:
            raise RuntimeError("Guard AND emergency termination failed; intervene for the exact workstation") from stop_error
        raise RuntimeError("Guard verification failed; emergency termination requested; verify native stack teardown") from exc
    return {"instance_id": iid, "schedules": [proposal[x]["Name"] for x in ("termination", "fallback")],
            "guard_armed": True}


def status(aws, context, *, now=None, headroom=0):
    """Read both workstation safeguards; a volume has no separate deadline."""
    from runtime import pr65_gpu_create as creator
    if context["profile"] != "gpu" or not context.get("safety_stack"):
        raise ValueError("Expected GPU expiration association")
    identity = creator.safety(aws, "us-east-1", context["stack"], arn=context["safety_stack"])
    if identity["token"] != context.get("token"):
        raise ValueError("Workstation and safety-stack deployment identities differ")
    verify_ownership(context["vm"], context["stack"], identity["token"], context["stack_id"])
    scheduler = creator.Scheduler(aws, "us-east-1")
    name = "studio-expiry-" + identity["token"]
    got = scheduler.get_schedule(Name=name, GroupName=GROUP)
    expr = got.get("ScheduleExpression", "")
    if not re.fullmatch(r"at\(20\d\d-\d\d-\d\dT\d\d:\d\d:\d\d\)", expr):
        raise ValueError("Unexpected expiration expression")
    deadline = dt.datetime.strptime(expr[3:-1], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=dt.timezone.utc)
    now = now or dt.datetime.now(dt.timezone.utc)
    remaining = (deadline - now).total_seconds()
    if remaining > 240 * 60 or (headroom and remaining < headroom * 60):
        raise ValueError("Expiration outside safe remaining-time bounds")
    proposal = plan(context["vm"], context["stack"], identity["token"], now,
                    role=identity["role"], stack_id=context["stack_id"], terminate_at=deadline)
    for label in ("termination", "fallback"):
        _verify_schedule(scheduler, proposal[label])
    return {**proposal, "remaining_seconds": max(0, int(remaining)),
            "status": "expired" if remaining <= 0 else "scheduled"}
