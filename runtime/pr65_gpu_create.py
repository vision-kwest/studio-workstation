"""CloudFormation provisions the workstation and its native automatic-stop timer."""
import datetime as dt
import hashlib
import re
from pathlib import Path
import json
import subprocess

from runtime import pr65_one_shot_guard as guard
from runtime import cli_progress as progress
from runtime import readiness_lifetime

POLICY = "StudioWorkstationExpiry"


class Scheduler:
    def __init__(self, aws_call, region):
        self.aws_call, self.region = aws_call, region

    def update_schedule(self, **spec):
        return self.aws_call(self.region, "scheduler", "update-schedule",
                             "--cli-input-json", json.dumps(spec))

    def get_schedule(self, **keys):
        try:
            return self.aws_call(self.region, "scheduler", "get-schedule",
                                 "--name", keys["Name"], "--group-name", keys["GroupName"])
        except subprocess.CalledProcessError as exc:
            if "(ResourceNotFoundException)" in (exc.stderr or ""):
                raise guard.InvalidSafeguard("Auto-stop schedule is absent") from exc
            raise


def preflight(aws_call, region, stack, token, *, role_arn):
    """Verify exact native expiration permissions before billing or restart."""
    if region != "us-east-1" or not role_arn.startswith("arn:aws:iam::401233741942:role/"):
        raise ValueError("Unexpected expiration region/account")
    group = aws_call(region, "scheduler", "get-schedule-group", "--name", guard.GROUP)
    if group.get("State") != "ACTIVE":
        raise guard.InvalidSafeguard("Expiration Scheduler group is not ACTIVE")
    name = role_arn.rsplit("/", 1)[-1]
    role = aws_call(region, "iam", "get-role", "--role-name", name)["Role"]
    trust = {"Version": "2012-10-17", "Statement": [{"Effect": "Allow",
             "Principal": {"Service": "scheduler.amazonaws.com"}, "Action": "sts:AssumeRole",
             "Condition": {"StringEquals": {"aws:SourceAccount": "401233741942"},
                           "ArnEquals": {"aws:SourceArn": "arn:aws:scheduler:us-east-1:401233741942:schedule-group/" + guard.GROUP}}}]}
    if role.get("Arn") != role_arn or role.get("AssumeRolePolicyDocument") != trust:
        raise guard.InvalidSafeguard("Expiration role identity/trust mismatch")
    doc = aws_call(region, "iam", "get-role-policy", "--role-name", name,
                   "--policy-name", POLICY)["PolicyDocument"]
    doc = json.loads(doc) if isinstance(doc, str) else doc
    scope = {"StringEquals": {"ec2:ResourceTag/StudioStack": stack,
                              "ec2:ResourceTag/StudioCleanupToken": token}}
    expected = [{"Effect": "Allow", "Action": "ec2:StopInstances",
                 "Resource": "arn:aws:ec2:us-east-1:401233741942:instance/*", "Condition": scope}]
    if doc != {"Version": "2012-10-17", "Statement": expected}:
        raise guard.InvalidSafeguard("Expiration role permissions differ from exact workstation scope")
    inline = aws_call(region, "iam", "list-role-policies", "--role-name", name)
    attached = aws_call(region, "iam", "list-attached-role-policies", "--role-name", name)
    if (inline.get("PolicyNames") != [POLICY] or attached.get("AttachedPolicies") or
            inline.get("IsTruncated") or attached.get("IsTruncated")):
        raise guard.InvalidSafeguard("Unexpected additional expiration policies")



def safety_name(stack):
    return "studio-expiry-" + hashlib.sha256(stack.encode()).hexdigest()[:20]


def safety(aws, region, stack, *, create=False, arn=None, cleanup=False, profile="gpu"):
    from runtime.workstation_inventory import stack_record
    progress.stage("Checking expiration safeguards")
    if region != "us-east-1" or not re.fullmatch(r"[A-Za-z][A-Za-z0-9-]{0,127}", stack):
        raise ValueError("Invalid safety-stack scope")
    name = safety_name(stack)
    if create:
        progress.stage("Creating expiration safeguards")
        template = Path(__file__).resolve().parents[1] / "infra/workstation-expiration-role.json"
        aws(region, "cloudformation", "create-stack", "--stack-name", name,
            "--template-body", "file://" + str(template), "--capabilities", "CAPABILITY_IAM",
            "--parameters", "ParameterKey=WorkstationStack,ParameterValue=" + stack,
            "--tags", "Key=StudioManaged,Value=expiry", "Key=StudioWorkstation,Value=" + stack,
            "Key=StudioProfile,Value=" + profile,
            "--on-failure", "DELETE", "--timeout-in-minutes", "5")
        aws(region, "cloudformation", "wait", "stack-create-complete", "--stack-name", name)
    record = stack_record(aws, region, arn or name)
    if record is None:
        raise guard.InvalidSafeguard("Expiration safety stack is absent")
    outputs = {v["OutputKey"]: v["OutputValue"] for v in record.get("Outputs", [])}
    tags = guard._tags(record)
    token = record["StackId"].rsplit("/", 1)[-1]
    params = {p["ParameterKey"]: p["ParameterValue"] for p in record.get("Parameters", [])}
    if (not record["StackId"].startswith("arn:aws:cloudformation:us-east-1:401233741942:stack/" + name + "/") or
            record["StackName"] != name or (not cleanup and record["StackStatus"] not in ("CREATE_COMPLETE", "UPDATE_COMPLETE")) or
            tags.get("StudioManaged") != "expiry" or tags.get("StudioWorkstation") != stack or
            params.get("WorkstationStack") != stack or
            (outputs and (outputs.get("WorkstationStack") != stack or outputs.get("CleanupToken") != token))):
        raise guard.InvalidSafeguard("Native safety-stack identity mismatch")
    resources = aws(region, "cloudformation", "list-stack-resources", "--stack-name", record["StackId"])["StackResourceSummaries"]
    if len(resources) > 1 or any((r.get("LogicalResourceId"), r.get("ResourceType")) != ("ExpirationRole", "AWS::IAM::Role") for r in resources):
        raise guard.InvalidSafeguard("Unexpected resource in safety stack")
    physical = resources[0].get("PhysicalResourceId") if resources else None
    role = "arn:aws:iam::401233741942:role/" + physical if physical else None
    if (outputs and role != outputs.get("ExpirationRoleArn")) or (not cleanup and not role):
        raise guard.InvalidSafeguard("Safety role is not owned by this CloudFormation stack")
    if not cleanup:
        preflight(aws, region, stack, token, role_arn=role)
    return {"arn": record["StackId"], "token": token, "role": role, "state": record["StackStatus"]}


def create_guarded(aws_call, region, stack, template, parameters, *, minutes=150, utc_now=None, profile="gpu"):
    """Provision a native stop schedule with EC2; no client-side arming window."""
    from runtime.workstation_inventory import PROFILES
    if profile not in PROFILES:
        raise ValueError("Unsupported workstation profile")
    now = utc_now or (lambda: dt.datetime.now(dt.timezone.utc))
    guard.deadline(now(), minutes)  # Validate the selected duration before any AWS writes.
    body = json.loads(Path(template).read_text())
    instance = body["Resources"]["Workstation"]
    expected = {"Workstation": "AWS::EC2::Instance"}
    if PROFILES[profile]["desktop"]:
        expected["WorkstationSG"] = "AWS::EC2::SecurityGroup"
        expected.update(EfsClientAssociation="AWS::SSM::Association", EfsClientAssociationRole="AWS::IAM::Role",
                        EfsClientDocument="AWS::SSM::Document", BootstrapReadPolicy="AWS::IAM::RolePolicy")
    disks = [m["Ebs"] for m in instance["Properties"].get("BlockDeviceMappings", []) if "Ebs" in m]
    if (instance["Type"] != "AWS::EC2::Instance" or not disks or
            any(d.get("DeleteOnTermination") is not True for d in disks) or
            {key: r["Type"] for key, r in body["Resources"].items()} != expected):
        raise ValueError("Expected a workstation with native EBS cleanup on explicit destroy")
    identity = safety(aws_call, region, stack, create=True, profile=profile)
    token, role = identity["token"], identity["role"]
    # Desktop lifetime is handed off by AWS after readiness, independently of this CLI.
    desktop = PROFILES[profile]["desktop"]
    expiry = guard.deadline(now(), readiness_lifetime.SETUP_MINUTES if desktop else 10)
    tags = [{"Key": "StudioStack", "Value": stack}, {"Key": "StudioCleanupToken", "Value": token}]
    for resource in body["Resources"].values():
        if resource["Type"] in ("AWS::EC2::Instance", "AWS::EC2::SecurityGroup"):
            resource["Properties"].setdefault("Tags", []).extend(tags)
    schedule = guard._schedule("studio-stop-" + token, expiry, role, "${Workstation}", minutes)
    if desktop:
        schedule["Description"] = f"Studio setup {token}; auto-stop after {minutes} minutes"
    # CloudFormation has no ActionAfterCompletion property; Scheduler defaults to NONE.
    schedule.pop("ActionAfterCompletion")
    schedule["Target"]["Input"] = {"Fn::Sub": schedule["Target"]["Input"]}
    body["Resources"]["AutoStopSchedule"] = {"Type": "AWS::Scheduler::Schedule", "Properties": schedule}
    if desktop:
        body["Resources"].update(readiness_lifetime.resources(token, role, schedule, profile))
    Path(template).write_text(json.dumps(body))
    cf_params = ["ParameterKey=" + k + ",ParameterValue=" + v for k, v in
                 (p.split("=", 1) for p in parameters)]
    progress.stage("Creating workstation and automatic-stop timer")
    response = aws_call(region, "cloudformation", "create-stack", "--stack-name", stack,
                        "--capabilities", "CAPABILITY_NAMED_IAM",
                        "--template-body", "file://" + str(template), "--parameters", *cf_params,
                        "--tags", "Key=StudioStack,Value=" + stack, "Key=StudioCleanupToken,Value=" + token,
                        "Key=StudioManaged,Value=true", "Key=StudioProfile,Value=" + profile,
                        "Key=StudioSafetyStack,Value=" + identity["arn"], "--on-failure", "DELETE", "--timeout-in-minutes", "5")
    stack_id = response.get("StackId", "")
    if not stack_id.startswith("arn:aws:cloudformation:us-east-1:401233741942:stack/" + stack + "/"):
        raise RuntimeError("Unrecognized stack identity; inspect CloudFormation provisioning")
    if desktop:
        progress.stage("AWS accepted workstation creation. SAFE TO DISCONNECT OR CLOSE LAPTOP.")
        progress.stage("Provisioning continues in AWS; creation can still fail. Continuing to monitor progress.")
    from runtime.workstation_inventory import context
    try:
        aws_call(region, "cloudformation", "wait", "stack-create-complete", "--stack-name", stack_id)
        selected = context(aws_call, region, stack_id)
        actual = guard.status(aws_call, selected, now=now())
        if actual["status"] != "scheduled":
            raise guard.InvalidSafeguard("Workstation provisioning stop timer is not active in the future")
        if not desktop:
            desired = guard.plan(selected["vm"], stack, token, now(), minutes=minutes,
                                 role=role, stack_id=stack_id)
            guard._update(aws_call, actual["schedule"], desired["schedule"], now=now())
        actual = guard.status(aws_call, selected, now=now())
        if actual["status"] != "scheduled":
            raise guard.InvalidSafeguard("Selected stop deadline elapsed during creation; workstation will stay stopped")
        return {"stack_id": stack_id, "safety_stack": identity["arn"],
                "stop_at": actual["stop_at"], "duration_minutes": minutes,
                "cloudformation_status": selected["stack_status"]}
    except BaseException as exc:
        # Read uncertainty cannot cancel server-owned setup. A proven unsafe timer must
        # still stop only the exact owned VM; failed CFN creation retains native rollback.
        must_stop = not desktop or isinstance(exc, guard.InvalidSafeguard)
        try:
            selected = context(aws_call, region, stack_id)
            outcome = f"last observed CloudFormation={selected['stack_status']}, EC2={selected['state']}"
            if not must_stop:
                outcome += "; AWS provisioning and its bounded setup stop continue"
            elif selected["state"] in ("pending", "running"):
                guard.verify_ownership(selected["vm"], stack, token, stack_id)
                aws_call(region, "ec2", "stop-instances", "--instance-ids", selected["instance"])
                outcome += "; stop requested"
            if selected["stack_status"] == "DELETE_COMPLETE" and selected["state"] in ("missing", "terminated"):
                outcome += f"; clean up remaining safeguards before retrying: python3 studio workstation destroy {stack}"
        except Exception as stop_error:
            outcome = ("stopping is unverified: " if must_stop else "AWS setup continues; local observation is unverified: ") + str(stop_error)
        detail = (getattr(exc, "stderr", None) or str(exc)).strip()
        # Read-only diagnostics must never prevent the ownership check or stop attempt.
        try:
            events = aws_call(region, "cloudformation", "describe-stack-events", "--stack-name", stack_id)["StackEvents"]
            reasons = [f"{e.get('LogicalResourceId', 'resource')}: {e['ResourceStatusReason']}" for e in events
                       if e.get("ResourceStatus") == "CREATE_FAILED" and
                       e.get("ResourceType") != "AWS::CloudFormation::Stack" and e.get("ResourceStatusReason")]
            if reasons:
                detail += "\nCloudFormation resource failures: " + "; ".join(reasons)
        except Exception:
            pass  # Retain the original cause if diagnostic permissions or AWS fail.
        raise RuntimeError("Creation/auto-stop verification failed; " + outcome + ". Inspect " + stack_id + "\n" + detail) from exc
