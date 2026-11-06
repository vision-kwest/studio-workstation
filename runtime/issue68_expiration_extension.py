"""Issue #68: exact-scope supervised GPU expiration extension.

Uses the existing PR65 ownership/IAM and Issue43 schedule preflights.
Never runs on import. The --additional-minutes CLI option authorizes the guarded update.
Extends retained-EBS cleanup first so it can never precede an extended
termination deadline. On failure, reconciles/rolls back when verifiable.
"""
import copy
import datetime as dt
import json

from runtime import issue43_hostname_handoff as h
from runtime import pr65_gpu_create

MIN_CURRENT_HEADROOM = dt.timedelta(minutes=15)
MIN_NEW_HEADROOM = dt.timedelta(minutes=80)
MAX_NEW_HEADROOM = dt.timedelta(minutes=240)
MAX_ADDITIONAL_MINUTES = 180
FIELDS = ("Name", "GroupName", "State", "ActionAfterCompletion",
          "ScheduleExpression", "ScheduleExpressionTimezone",
          "FlexibleTimeWindow", "Target", "Description")


def _request(schedule):
    """Preserve every supported Schedule update field, never silently reset it."""
    if any(schedule.get(k) is not None for k in ("StartDate", "EndDate", "KmsKeyArn")):
        raise ValueError("Unexpected custom schedule options; refuse extension")
    fields = {k: copy.deepcopy(schedule[k]) for k in FIELDS if k in schedule}
    if set(fields) - {"Description"} != set(FIELDS) - {"Description"}:
        raise ValueError("Incomplete expiration schedule; refuse extension")
    target = fields["Target"]
    if set(target) != {"Arn", "RoleArn", "Input", "RetryPolicy"}:
        raise ValueError("Unexpected expiration target configuration")
    return fields


def _read(aws, name):
    result = aws(h.REGION, "scheduler", "get-schedule",
                 "--group-name", h.guard.GROUP, "--name", name)
    return _request(result)


def _verify(aws, expected):
    if _read(aws, expected["Name"]) != expected:
        raise RuntimeError("Expiration schedule update/readback differs: " + expected["Name"])


def _write(aws, request):
    # AWS UpdateSchedule resets omitted optional fields to defaults; the
    # request above carries all recognized fields from GetSchedule.
    aws(h.REGION, "scheduler", "update-schedule",
        "--cli-input-json", json.dumps(request))


def _prepare(aws, stack, region, additional_minutes, now):
    if (stack, region) != (h.GPU_STACK, h.REGION):
        raise ValueError("Extension limited to exact PR65 GPU stack and region")
    if type(additional_minutes) is not int or not 1 <= additional_minutes <= MAX_ADDITIONAL_MINUTES:
        raise ValueError("Additional minutes must be an integer in 1..180")
    if now.tzinfo is None or now.utcoffset() != dt.timedelta(0):
        raise ValueError("Require UTC-aware time")
    iid = h._stack_iid(aws, stack)
    gpu = h._items(aws, iid)[iid]
    if gpu.get("State", {}).get("Name") not in ("running", "stopped"):
        raise ValueError("GPU must be running or stopped to extend expiration")
    pr65_gpu_create.preflight(aws, region, stack, h.GPU_TOKEN)
    deadlines = h._gpu_guard(aws, gpu, now, require_headroom=False)
    term = dt.datetime.fromisoformat(deadlines["terminate_at"])
    cleanup = dt.datetime.fromisoformat(deadlines["volume_cleanup_at"])
    if term - now < MIN_CURRENT_HEADROOM:
        raise ValueError("Expiration too near or elapsed; need at least 15 minutes to extend safely")
    new_term = term + dt.timedelta(minutes=additional_minutes)
    new_cleanup = cleanup + dt.timedelta(minutes=additional_minutes)
    if not MIN_NEW_HEADROOM <= new_term - now <= MAX_NEW_HEADROOM:
        raise ValueError("Extended termination must be 80–240 minutes from now")
    if not dt.timedelta(minutes=30) <= new_cleanup - new_term <= dt.timedelta(minutes=120):
        raise ValueError("Retained EBS delay outside PR65 bounds")
    names = ("studio-pr65-i-" + iid, "studio-pr65-v-" + deadlines["root_volume_id"])
    old_term, old_ebs = (_read(aws, name) for name in names)
    if (old_term["ScheduleExpression"] != "at(" + term.strftime("%Y-%m-%dT%H:%M:%S") + ")" or
            old_ebs["ScheduleExpression"] != "at(" + cleanup.strftime("%Y-%m-%dT%H:%M:%S") + ")"):
        raise ValueError("Expiration schedule changed during preflight")
    next_term, next_ebs = copy.deepcopy(old_term), copy.deepcopy(old_ebs)
    next_term["ScheduleExpression"] = "at(" + new_term.strftime("%Y-%m-%dT%H:%M:%S") + ")"
    next_ebs["ScheduleExpression"] = "at(" + new_cleanup.strftime("%Y-%m-%dT%H:%M:%S") + ")"
    summary = {
        "stack": stack, "instance_id": iid, "root_volume_id": deadlines["root_volume_id"],
        "additional_minutes": additional_minutes,
        "previous_termination_utc": term.isoformat(),
        "previous_ebs_cleanup_utc": cleanup.isoformat(),
        "proposed_termination_utc": new_term.isoformat(),
        "proposed_ebs_cleanup_utc": new_cleanup.isoformat(),
    }
    return (old_term, old_ebs), (next_term, next_ebs), summary


def extend(aws, stack, region, additional_minutes, *, now=None):
    """Update EBS first, termination second; verify or reconcile both."""
    now = now or dt.datetime.now(dt.timezone.utc)
    original, proposed, summary = _prepare(aws, stack, region, additional_minutes, now)
    old_term, old_ebs = original
    next_term, next_ebs = proposed

    # Re-read both exact schedules immediately before any write.
    _verify(aws, old_term)
    _verify(aws, old_ebs)
    try:
        _write(aws, next_ebs)
        _verify(aws, next_ebs)
        _verify(aws, old_term)  # Abort if termination changed independently.
        _write(aws, next_term)
        _verify(aws, next_term)
        _verify(aws, next_ebs)
    except BaseException as exc:
        try:
            actual_term = _read(aws, old_term["Name"])
            actual_ebs = _read(aws, old_ebs["Name"])
        except BaseException as verify_exc:
            raise RuntimeError(
                "Extension interrupted; schedule state unverified. "
                "Inspect both schedules before any further operation.") from verify_exc
        if actual_term == next_term and actual_ebs == next_ebs:
            return {**summary, "extended": True, "recovered_after_error": True}
        if actual_term == old_term:
            if actual_ebs == next_ebs:
                try:
                    _write(aws, old_ebs)
                    _verify(aws, old_ebs)
                except BaseException as rollback_exc:
                    raise RuntimeError(
                        "Termination remains at original deadline but EBS rollback "
                        "unverified; inspect both schedules manually.") from rollback_exc
            elif actual_ebs != old_ebs:
                raise RuntimeError(
                    "Termination unchanged, EBS schedule unexpected; "
                    "inspect exact schedules manually.") from exc
            raise RuntimeError(
                "Extension aborted; original termination and EBS deadlines verified.") from exc
        raise RuntimeError(
            "Partial expiration extension: EC2 and EBS schedule deadlines differ; "
            "do not resume until manually reconciled.") from exc
    return {**summary, "extended": True, "recovered_after_error": False}
