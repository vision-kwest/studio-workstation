"""One native auto-stop schedule; saved EBS work survives ordinary expiration."""
import copy
import datetime as dt
import json
import re

GROUP = "studio-task7-pr61"
STOP = "arn:aws:scheduler:::aws-sdk:ec2:stopInstances"
FIELDS = ("Name", "GroupName", "State", "ScheduleExpression", "ScheduleExpressionTimezone",
          "FlexibleTimeWindow", "ActionAfterCompletion", "Target", "Description")


def _tags(resource):
    result = {}
    for tag in resource.get("Tags", []):
        key, value = tag["Key"], tag["Value"]
        if key in result:
            raise ValueError("Duplicate ownership tag: " + key)
        result[key] = value
    return result


def verify_ownership(instance, stack, token, stack_id):
    """Stopping requires the owned EBS-backed instance, not a disk deletion policy."""
    if (not isinstance(stack, str) or not isinstance(token, str) or
            not re.fullmatch(r"[A-Za-z][A-Za-z0-9-]{0,127}", stack) or not re.fullmatch(r"[a-f0-9-]{36}", token)):
        raise ValueError("Invalid workstation identity")
    iid = instance.get("InstanceId", "")
    tags = _tags(instance)
    if (not re.fullmatch(r"i-[0-9a-f]{8,17}", iid) or
            (tags.get("StudioStack"), tags.get("StudioCleanupToken"),
             tags.get("aws:cloudformation:stack-id")) != (stack, token, stack_id)):
        raise ValueError("Workstation ownership mismatch")
    if not any("Ebs" in m for m in instance.get("BlockDeviceMappings", [])):
        raise ValueError("Automatic stop requires an EBS-backed workstation")
    return iid


def _utc(value=None):
    value = value if value is not None else dt.datetime.now(dt.timezone.utc)
    if not isinstance(value, dt.datetime) or value.tzinfo is None or value.utcoffset() != dt.timedelta(0):
        raise ValueError("Expected timezone-aware UTC time")
    return value


def validate_durations(minutes):
    if type(minutes) is not int or minutes <= 0:
        raise ValueError("Auto-stop duration must be a positive number of whole minutes")


def deadline(now, minutes):
    """Reject malformed/overflowing dates, without imposing a product duration cap."""
    validate_durations(minutes)
    now = _utc(now)
    try:
        return (now + dt.timedelta(minutes=minutes)).replace(microsecond=0)
    except OverflowError as exc:
        raise ValueError("Auto-stop duration exceeds the supported calendar range") from exc


def _schedule(name, when, role, iid, minutes):
    validate_durations(minutes)
    when = _utc(when)
    return {"Name": name, "GroupName": GROUP, "State": "ENABLED",
            "ScheduleExpression": "at(" + when.strftime("%Y-%m-%dT%H:%M:%S") + ")",
            "ScheduleExpressionTimezone": "UTC", "FlexibleTimeWindow": {"Mode": "OFF"},
            "ActionAfterCompletion": "NONE", "Description": f"Studio auto-stop after {minutes} minutes",
            "Target": {"Arn": STOP, "RoleArn": role,
                       "Input": json.dumps({"InstanceIds": [iid]}, separators=(",", ":")),
                       "RetryPolicy": {"MaximumRetryAttempts": 2, "MaximumEventAgeInSeconds": 60}}}


def plan(instance, stack, token, now, *, minutes=150, role, stack_id, stop_at=None):
    iid = verify_ownership(instance, stack, token, stack_id)
    now = _utc(now)
    when = _utc(stop_at) if stop_at is not None else deadline(now, minutes)
    return {"stack": stack, "token": token, "stack_id": stack_id, "role": role,
            "instance_id": iid, "stop_at": when.isoformat(), "duration_minutes": minutes,
            "schedule": _schedule("studio-stop-" + token, when, role, iid, minutes)}


def _request(schedule):
    if any(schedule.get(k) is not None for k in ("StartDate", "EndDate", "KmsKeyArn")):
        raise ValueError("Unexpected auto-stop schedule options")
    if any(k not in schedule for k in FIELDS):
        raise ValueError("Incomplete auto-stop schedule")
    return {k: copy.deepcopy(schedule[k]) for k in FIELDS}


def _check_schedule(got, expected):
    request = _request(got)
    for field in FIELDS:
        if field != "Target" and request[field] != expected[field]:
            raise RuntimeError("Auto-stop schedule readback mismatch: " + field)
    target, wanted = request["Target"], expected["Target"]
    if (set(target) != set(wanted) or
            any(target[k] != wanted[k] for k in wanted if k != "Input") or
            json.loads(target.get("Input", "null")) != json.loads(wanted["Input"])):
        raise RuntimeError("Auto-stop schedule target, ownership or retry policy mismatch")
    return request


def _verify_schedule(scheduler, spec):
    return _check_schedule(scheduler.get_schedule(Name=spec["Name"], GroupName=spec["GroupName"]), spec)


def has_expiration(context):
    """Require verification when any timer identity remains, including partial metadata."""
    return bool(context["profile"] == "gpu" or context.get("safety_stack") or context.get("token") or
                any(r.get("LogicalResourceId") == "AutoStopSchedule" for r in context["resources"]))


def status(aws, context, *, now=None):
    """Inspect the retained stop timer, including ordinary disabled/expired states."""
    from runtime import pr65_gpu_create as creator
    from runtime.workstation_inventory import PROFILES
    if context["profile"] not in PROFILES or not context.get("safety_stack"):
        raise ValueError("Automatic stop is not configured; expected a verified safety-stack association")
    resources = [r for r in context["resources"] if r.get("LogicalResourceId") == "AutoStopSchedule"]
    if not resources:
        return {"status": "legacy", "stop_at": None, "remaining_seconds": None, "duration_minutes": None,
                "warning": "Legacy workstation: automatic stop is not configured; external timers may still "
                           "destroy it. Review migration before start or extension; no timers were changed."}
    verify_ownership(context["vm"], context["stack"], context["token"], context["stack_id"])
    name = "studio-stop-" + context["token"]
    if (len(resources) != 1 or resources[0].get("ResourceType") != "AWS::Scheduler::Schedule" or
            resources[0].get("PhysicalResourceId") != name):
        raise ValueError("Auto-stop schedule is not owned by the workstation stack")
    identity = creator.safety(aws, "us-east-1", context["stack"], arn=context["safety_stack"])
    if identity["token"] != context.get("token"):
        raise ValueError("Workstation and safety-stack deployment identities differ")
    scheduler = creator.Scheduler(aws, "us-east-1")
    got = scheduler.get_schedule(Name=name, GroupName=GROUP)
    expr, description = got.get("ScheduleExpression", ""), got.get("Description", "")
    duration = re.fullmatch(r"Studio auto-stop after ([1-9][0-9]*) minutes", description)
    if (not re.fullmatch(r"at\(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\)", expr) or
            not duration or got.get("State") not in ("ENABLED", "DISABLED")):
        raise ValueError("Unexpected auto-stop time, duration or state")
    when = dt.datetime.strptime(expr[3:-1], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=dt.timezone.utc)
    now = _utc(now)
    proposal = plan(context["vm"], context["stack"], identity["token"], now,
                    minutes=int(duration[1]), role=identity["role"], stack_id=context["stack_id"], stop_at=when)
    proposal["schedule"]["State"] = got["State"]
    proposal["schedule"] = _check_schedule(got, proposal["schedule"])
    remaining = (when - now).total_seconds()
    return {**proposal, "remaining_seconds": max(0, int(remaining)),
            "status": "disabled" if got["State"] == "DISABLED" else ("expired" if remaining <= 0 else "scheduled")}


def _update(aws, original, proposed, *, now=None):
    """Reconcile one native update by readback; never overwrite an uncertain result."""
    from runtime import pr65_gpu_create as creator
    scheduler = creator.Scheduler(aws, "us-east-1")
    _verify_schedule(scheduler, original)
    when = dt.datetime.fromisoformat(proposed["ScheduleExpression"][3:-1]).replace(tzinfo=dt.timezone.utc)
    if when <= _utc(now):
        raise ValueError("Stop deadline has already passed; choose a future deadline")
    try:
        scheduler.update_schedule(**proposed)
        _verify_schedule(scheduler, proposed)
    except Exception as exc:
        try:
            actual = scheduler.get_schedule(Name=original["Name"], GroupName=GROUP)
        except Exception as read_error:
            raise RuntimeError("Auto-stop update state is unverified; inspect the schedule before starting") from read_error
        try:
            _check_schedule(actual, proposed)
        except Exception:
            try:
                _check_schedule(actual, original)
            except Exception:
                raise RuntimeError("Auto-stop schedule changed unexpectedly; inspect it before starting") from exc
            raise RuntimeError("Auto-stop update failed; original schedule is unchanged") from exc
        return True
    return False


def rearm(aws, context, *, minutes=None, now=None):
    """Set and verify a fresh stop deadline before the caller starts stopped EC2."""
    if context["state"] != "stopped":
        raise ValueError("Expected a stopped workstation before rearming for start")
    current = status(aws, context, now=now)
    if current["status"] == "legacy":
        raise ValueError(current["warning"])
    minutes = current["duration_minutes"] if minutes is None else minutes
    when = deadline(_utc(now), minutes)
    proposed = copy.deepcopy(current["schedule"])
    proposed.update(State="ENABLED", ScheduleExpression="at(" + when.strftime("%Y-%m-%dT%H:%M:%S") + ")",
                    Description=f"Studio auto-stop after {minutes} minutes")
    recovered = _update(aws, current["schedule"], proposed, now=now)
    remaining = (when - _utc(now)).total_seconds()
    if remaining <= 0:
        raise RuntimeError("Auto-stop deadline elapsed while rearming; choose a future deadline before starting")
    return {**current, "schedule": proposed, "stop_at": when.isoformat(), "duration_minutes": minutes,
            "remaining_seconds": int(remaining), "status": "scheduled",
            "recovered_after_error": recovered}
