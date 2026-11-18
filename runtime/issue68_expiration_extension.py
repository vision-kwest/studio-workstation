"""Extend native stack expiration and its instance fallback, with verified rollback."""
import copy
import datetime as dt
import json

from runtime import issue43_hostname_handoff as h
from runtime import pr65_one_shot_guard as guard
from runtime.workstation_inventory import context

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
                 "--group-name", guard.GROUP, "--name", name)
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
    if type(additional_minutes) is not int or not 1 <= additional_minutes <= MAX_ADDITIONAL_MINUTES:
        raise ValueError("Additional minutes must be an integer in 1..180")
    if now.tzinfo is None or now.utcoffset() != dt.timedelta(0):
        raise ValueError("Require UTC-aware time")
    selected = context(aws, region, stack)
    if selected["state"] not in ("running", "stopped"):
        raise ValueError("GPU must be running or stopped to extend expiration")
    current = guard.status(aws, selected, now=now, headroom=15)
    term = dt.datetime.fromisoformat(current["terminate_at"])
    new_term = term + dt.timedelta(minutes=additional_minutes)
    if not MIN_NEW_HEADROOM <= new_term - now <= MAX_NEW_HEADROOM:
        raise ValueError("Extended termination must be 80–240 minutes from now")
    original = tuple(_read(aws, current[k]["Name"]) for k in ("termination", "fallback"))
    for schedule, key in zip(original, ("termination", "fallback")):
        if {k: v for k, v in schedule.items() if k != "Description"} != current[key]:
            raise RuntimeError("Expiration changed while preparing extension")
    proposed = copy.deepcopy(original)
    for schedule, offset in zip(proposed, (0, guard.FALLBACK_MINUTES)):
        when = new_term + dt.timedelta(minutes=offset)
        schedule["ScheduleExpression"] = "at(" + when.strftime("%Y-%m-%dT%H:%M:%S") + ")"
    return original, proposed, {"stack": stack, "instance_id": selected["instance"],
                               "previous_termination_utc": term.isoformat(),
                               "proposed_termination_utc": new_term.isoformat(),
                               "additional_minutes": additional_minutes}


def extend(aws, stack, region, additional_minutes, *, now=None):
    """Update fallback first, termination second; verify or reconcile both."""
    now = now or dt.datetime.now(dt.timezone.utc)
    original, proposed, summary = _prepare(aws, stack, region, additional_minutes, now)
    old_term, old_fallback = original
    next_term, next_fallback = proposed

    # Re-read both exact schedules immediately before any write.
    _verify(aws, old_term)
    _verify(aws, old_fallback)
    try:
        _write(aws, next_fallback)
        _verify(aws, next_fallback)
        _verify(aws, old_term)  # Abort if termination changed independently.
        _write(aws, next_term)
        _verify(aws, next_term)
        _verify(aws, next_fallback)
    except BaseException as exc:
        try:
            actual_term = _read(aws, old_term["Name"])
            actual_fallback = _read(aws, old_fallback["Name"])
        except BaseException as verify_exc:
            raise RuntimeError(
                "Extension interrupted; schedule state unverified. "
                "Inspect both schedules before any further operation.") from verify_exc
        if actual_term == next_term and actual_fallback == next_fallback:
            return {**summary, "extended": True, "recovered_after_error": True}
        if actual_term == old_term:
            if actual_fallback == next_fallback:
                try:
                    _write(aws, old_fallback)
                    _verify(aws, old_fallback)
                except BaseException as rollback_exc:
                    raise RuntimeError(
                        "Termination remains at original deadline but fallback rollback "
                        "unverified; inspect both schedules manually.") from rollback_exc
            elif actual_fallback != old_fallback:
                raise RuntimeError(
                    "Termination unchanged, fallback schedule unexpected; "
                    "inspect exact schedules manually.") from exc
            raise RuntimeError(
                "Extension aborted; original termination and fallback deadlines verified.") from exc
        raise RuntimeError(
            "Partial expiration extension: Stack and fallback schedule deadlines differ; "
            "do not resume until manually reconciled.") from exc
    return {**summary, "extended": True, "recovered_after_error": False}
