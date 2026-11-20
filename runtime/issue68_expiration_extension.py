"""Extend a native auto-stop deadline without deleting work or imposing a duration cap."""
import copy
import datetime as dt

from runtime import pr65_one_shot_guard as guard
from runtime.workstation_inventory import context


def extend(aws, stack, region, additional_minutes, *, now=None):
    guard.validate_durations(additional_minutes)
    selected = context(aws, region, stack)
    if selected["state"] not in ("running", "stopped"):
        raise ValueError("Workstation is already stopping or unavailable; wait for it to stop, then start it")
    current = guard.status(aws, selected, now=now)
    if current["status"] == "legacy":
        raise ValueError(current["warning"])
    if current["status"] == "disabled":
        raise ValueError("Auto-stop is disabled; start/rearm it explicitly before extending")
    previous = dt.datetime.fromisoformat(current["stop_at"])
    when = guard.deadline(previous, additional_minutes)
    if when <= guard._utc(now):
        raise ValueError("Extended stop deadline must be in the future; start the stopped workstation with a fresh timer")
    proposed = copy.deepcopy(current["schedule"])
    proposed["ScheduleExpression"] = "at(" + when.strftime("%Y-%m-%dT%H:%M:%S") + ")"
    recovered = guard._update(aws, current["schedule"], proposed, now=now)
    # A verified update cannot retract a stop request already dispatched by AWS.
    try:
        observed = context(aws, region, stack)
    except Exception as exc:
        raise RuntimeError("Auto-stop deadline was updated and verified, but EC2 state is unverified") from exc
    if observed["stack_id"] != selected["stack_id"] or observed["instance"] != selected["instance"]:
        raise RuntimeError("Workstation identity changed after extending auto-stop")
    if when <= guard._utc(now):
        raise RuntimeError("Auto-stop deadline was updated and verified, but elapsed during verification; inspect workstation state")
    result = {"stack": stack, "instance_id": selected["instance"], "previous_stop_utc": previous.isoformat(),
              "proposed_stop_utc": when.isoformat(), "additional_minutes": additional_minutes,
              "extended": True, "recovered_after_error": recovered, "state": observed["state"]}
    if observed["state"] in ("stopping", "stopped"):
        result["warning"] = ("The deadline was updated, but the workstation is " + observed["state"] +
                             "; an already-dispatched stop cannot be canceled. Start again once stopped; saved EBS work is preserved.")
    elif observed["state"] != "running":
        result["warning"] = "The deadline was updated; inspect unexpected workstation state: " + observed["state"]
    return result
