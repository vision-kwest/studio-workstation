"""Short SSM Automation actions; waiting and retries belong to native Automation."""
import datetime as dt
import json
import re

import boto3


def handle(event, context):
    scheduler = boto3.client("scheduler")
    keys = {"Name": event["ScheduleName"], "GroupName": "studio-task7-pr61"}
    schedule = scheduler.get_schedule(**keys)
    expected = event["SetupDescription"]
    match = re.fullmatch(r"Studio setup ([a-f0-9-]{36}); auto-stop after ([1-9][0-9]*) minutes", expected)
    if not match:
        raise ValueError("Invalid setup operation")
    ready = expected.replace("Studio setup ", "Studio ready ", 1)
    target = schedule["Target"]
    if (schedule.get("State") != "ENABLED" or schedule.get("ScheduleExpressionTimezone") != "UTC" or
            schedule.get("FlexibleTimeWindow") != {"Mode": "OFF"} or
            schedule.get("ActionAfterCompletion") != "NONE" or
            target.get("Arn") != "arn:aws:scheduler:::aws-sdk:ec2:stopInstances" or
            target.get("RoleArn") != event["StopRole"] or
            json.loads(target.get("Input", "{}")) != {"InstanceIds": [event["InstanceId"]]} or
            target.get("RetryPolicy") != {"MaximumRetryAttempts": 2, "MaximumEventAgeInSeconds": 60} or
            any(schedule.get(k) is not None for k in ("StartDate", "EndDate", "KmsKeyArn"))):
        raise ValueError("Unexpected stop schedule scope")
    instance = boto3.client("ec2").describe_instances(InstanceIds=[event["InstanceId"]])["Reservations"][0]["Instances"][0]
    tags = {item["Key"]: item["Value"] for item in instance.get("Tags", [])}
    if (instance["InstanceId"] != event["InstanceId"] or
            tags.get("aws:cloudformation:stack-id") != event["StackId"] or
            tags.get("StudioCleanupToken") != event["CleanupToken"]):
        raise ValueError("Workstation deployment changed")
    if schedule.get("Description") == ready:
        return {"Done": True}  # A duplicate association/execution cannot restart the clock.
    if (schedule.get("Description") != expected or
            schedule.get("ScheduleExpression") != "at(" + event["SetupDeadline"] + ")"):
        raise ValueError("Setup operation or deadline changed; preserve the newer timer")
    now = dt.datetime.now(dt.timezone.utc)
    deadline = dt.datetime.fromisoformat(event["SetupDeadline"]).replace(tzinfo=dt.timezone.utc)
    if deadline <= now or instance["State"]["Name"] in ("stopping", "shutting-down", "terminated"):
        raise ValueError("Setup deadline elapsed or workstation is stopping")
    if event["Operation"] == "verify":
        return {"Done": False}
    if event["Operation"] != "handoff" or instance["State"]["Name"] != "running":
        raise ValueError("Readiness handoff requires a running workstation")
    when = (now + dt.timedelta(minutes=int(match[2]))).replace(microsecond=0)
    fields = ("Name", "GroupName", "State", "ScheduleExpression", "ScheduleExpressionTimezone",
              "FlexibleTimeWindow", "ActionAfterCompletion", "Target", "Description")
    proposed = {key: schedule[key] for key in fields}
    proposed.update(Description=ready, ScheduleExpression="at(" + when.strftime("%Y-%m-%dT%H:%M:%S") + ")")
    # Native idempotency plus phase/deadline checks fence duplicate and stale runs.
    try:
        scheduler.update_schedule(**proposed, ClientToken=match[1])
    except Exception:
        actual = scheduler.get_schedule(**keys)
        if {key: actual[key] for key in fields} != proposed:
            raise
    actual = scheduler.get_schedule(**keys)
    if any(actual.get(key) != value for key, value in proposed.items()):
        raise RuntimeError("Readiness stop deadline readback mismatch")
    return {"Done": True}
