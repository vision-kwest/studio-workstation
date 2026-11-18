"""Native GPU stack creation, with independently scoped expiration armed before billing."""
import datetime as dt
import hashlib
import re
from pathlib import Path
import json
import time

from runtime import pr65_one_shot_guard as guard

POLICY = "StudioWorkstationExpiry"
POLL_SECONDS = 3
MAX_DISCOVERY_SECONDS = 120


class Scheduler:
    def __init__(self, aws_call, region):
        self.aws_call, self.region = aws_call, region

    def create_schedule(self, **spec):
        return self.aws_call(self.region, "scheduler", "create-schedule",
                             "--cli-input-json", json.dumps(spec))

    def update_schedule(self, **spec):
        return self.aws_call(self.region, "scheduler", "update-schedule",
                             "--cli-input-json", json.dumps(spec))

    def get_schedule(self, **keys):
        return self.aws_call(self.region, "scheduler", "get-schedule",
                             "--name", keys["Name"], "--group-name", keys["GroupName"])


class EC2:
    def __init__(self, aws_call, region):
        self.aws_call, self.region = aws_call, region

    def terminate_instances(self, **keys):
        return self.aws_call(self.region, "ec2", "terminate-instances",
                             "--instance-ids", *keys["InstanceIds"])


def preflight(aws_call, region, stack, token, *, role_arn):
    """Verify exact native expiration permissions before billing or restart."""
    if region != "us-east-1" or not role_arn.startswith("arn:aws:iam::401233741942:role/"):
        raise ValueError("Unexpected expiration region/account")
    group = aws_call(region, "scheduler", "get-schedule-group", "--name", guard.GROUP)
    if group.get("State") != "ACTIVE":
        raise ValueError("Expiration Scheduler group is not ACTIVE")
    name = role_arn.rsplit("/", 1)[-1]
    role = aws_call(region, "iam", "get-role", "--role-name", name)["Role"]
    trust = {"Version": "2012-10-17", "Statement": [{"Effect": "Allow",
             "Principal": {"Service": "scheduler.amazonaws.com"}, "Action": "sts:AssumeRole",
             "Condition": {"StringEquals": {"aws:SourceAccount": "401233741942"},
                           "ArnEquals": {"aws:SourceArn": "arn:aws:scheduler:us-east-1:401233741942:schedule-group/" + guard.GROUP}}}]}
    if role.get("Arn") != role_arn or role.get("AssumeRolePolicyDocument") != trust:
        raise ValueError("Expiration role identity/trust mismatch")
    doc = aws_call(region, "iam", "get-role-policy", "--role-name", name,
                   "--policy-name", POLICY)["PolicyDocument"]
    doc = json.loads(doc) if isinstance(doc, str) else doc
    scope = {"StringEquals": {"ec2:ResourceTag/StudioStack": stack,
                              "ec2:ResourceTag/StudioCleanupToken": token}}
    expected = [{"Effect": "Allow", "Action": action,
                 "Resource": "arn:aws:ec2:us-east-1:401233741942:" + kind + "/*", "Condition": scope}
                for action, kind in (("ec2:TerminateInstances", "instance"), ("ec2:DeleteSecurityGroup", "security-group"))]
    expected += [{"Effect": "Allow", "Action": "cloudformation:DeleteStack",
                  "Resource": "arn:aws:cloudformation:us-east-1:401233741942:stack/" + stack + "/*",
                  "Condition": {"StringEquals": {"aws:ResourceTag/StudioCleanupToken": token}}},
                 {"Effect": "Allow", "Action": ["ec2:DescribeInstances", "ec2:DescribeInstanceAttribute",
                  "ec2:DescribeSecurityGroups", "ec2:DescribeNetworkInterfaces", "ec2:DescribeVolumes", "ec2:DescribeTags",
                  "ec2:DescribeInstanceCreditSpecifications", "ec2:DescribeLaunchTemplates",
                  "ssm:DescribeAssociation", "ssm:ListAssociations"],
                  "Resource": "*"}]
    if doc != {"Version": "2012-10-17", "Statement": expected}:
        raise ValueError("Expiration role permissions differ from exact workstation scope")
    inline = aws_call(region, "iam", "list-role-policies", "--role-name", name)
    attached = aws_call(region, "iam", "list-attached-role-policies", "--role-name", name)
    if (inline.get("PolicyNames") != [POLICY] or attached.get("AttachedPolicies") or
            inline.get("IsTruncated") or attached.get("IsTruncated")):
        raise ValueError("Unexpected additional expiration policies")



def safety_name(stack):
    return "studio-expiry-" + hashlib.sha256(stack.encode()).hexdigest()[:20]


def safety(aws, region, stack, *, create=False, arn=None, cleanup=False):
    from runtime.workstation_inventory import stack_record
    if region != "us-east-1" or not re.fullmatch(r"[A-Za-z][A-Za-z0-9-]{0,127}", stack):
        raise ValueError("Invalid safety-stack scope")
    name = safety_name(stack)
    if create:
        template = Path(__file__).resolve().parents[1] / "infra/workstation-expiration-role.json"
        aws(region, "cloudformation", "create-stack", "--stack-name", name,
            "--template-body", "file://" + str(template), "--capabilities", "CAPABILITY_IAM",
            "--parameters", "ParameterKey=WorkstationStack,ParameterValue=" + stack,
            "--tags", "Key=StudioManaged,Value=expiry", "Key=StudioWorkstation,Value=" + stack,
            "--on-failure", "DELETE", "--timeout-in-minutes", "5")
        aws(region, "cloudformation", "wait", "stack-create-complete", "--stack-name", name)
    record = stack_record(aws, region, arn or name)
    if record is None:
        raise ValueError("Expiration safety stack is absent")
    outputs = {v["OutputKey"]: v["OutputValue"] for v in record.get("Outputs", [])}
    tags = guard._tags(record)
    token = record["StackId"].rsplit("/", 1)[-1]
    params = {p["ParameterKey"]: p["ParameterValue"] for p in record.get("Parameters", [])}
    if (not record["StackId"].startswith("arn:aws:cloudformation:us-east-1:401233741942:stack/" + name + "/") or
            record["StackName"] != name or (not cleanup and record["StackStatus"] != "CREATE_COMPLETE") or
            tags.get("StudioManaged") != "expiry" or tags.get("StudioWorkstation") != stack or
            params.get("WorkstationStack") != stack or
            (outputs and (outputs.get("WorkstationStack") != stack or outputs.get("CleanupToken") != token))):
        raise ValueError("Native safety-stack identity mismatch")
    resources = aws(region, "cloudformation", "list-stack-resources", "--stack-name", record["StackId"])["StackResourceSummaries"]
    if len(resources) > 1 or any((r.get("LogicalResourceId"), r.get("ResourceType")) != ("ExpirationRole", "AWS::IAM::Role") for r in resources):
        raise ValueError("Unexpected resource in safety stack")
    physical = resources[0].get("PhysicalResourceId") if resources else None
    role = "arn:aws:iam::401233741942:role/" + physical if physical else None
    if (outputs and role != outputs.get("ExpirationRoleArn")) or (not cleanup and not role):
        raise ValueError("Safety role is not owned by this CloudFormation stack")
    if not cleanup:
        preflight(aws, region, stack, token, role_arn=role)
    return {"arn": record["StackId"], "token": token, "role": role, "state": record["StackStatus"]}

def _one_instance(aws_call, region, iid):
    data = aws_call(region, "ec2", "describe-instances", "--instance-ids", iid)
    items = [vm for r in data.get("Reservations", []) for vm in r.get("Instances", [])]
    if len(items) != 1 or items[0].get("InstanceId") != iid:
        raise RuntimeError("Expected exactly one scoped EC2 instance")
    return items[0]


def _tags_match(item, stack, token):
    tags = item.get("Tags", [])
    keys = [t["Key"] for t in tags]
    values = {t["Key"]: t["Value"] for t in tags}
    return (len(keys) == len(set(keys)) and
            values.get("StudioStack") == stack and
            values.get("StudioCleanupToken") == token)


def _emergency(aws_call, region, stack_id, stack, token, iid, stack_owned):
    """Prefer native teardown. Fall back only to the exact owned EC2 instance."""
    problems = []
    if stack_owned:
        try:
            aws_call(region, "cloudformation", "delete-stack", "--stack-name", stack_id)
            return problems
        except Exception as exc:
            problems.append("stack deletion: " + str(exc))
    if iid:
        try:
            instance = _one_instance(aws_call, region, iid)
            if not _tags_match(instance, stack, token) or guard._tags(instance).get("aws:cloudformation:stack-id") != stack_id:
                raise RuntimeError("Emergency instance ownership mismatch")
            aws_call(region, "ec2", "terminate-instances", "--instance-ids", iid)
        except Exception as exc:
            problems.append("instance termination: " + str(exc))
    return problems


def create_guarded(aws_call, region, stack, template, parameters, *, minutes=150,
                   clock=time.monotonic, sleep=time.sleep, utc_now=None):
    """Arm native stack expiration BEFORE CreateStack, then add exact-instance fallback."""
    guard.validate_durations(minutes)
    body = json.loads(Path(template).read_text())
    instance = body["Resources"]["Workstation"]
    disks = [m["Ebs"] for m in instance["Properties"].get("BlockDeviceMappings", []) if "Ebs" in m]
    if (instance["Type"] != "AWS::EC2::Instance" or not disks or
            any(d.get("DeleteOnTermination") is not True for d in disks) or
            any(r["Type"] not in ("AWS::EC2::Instance", "AWS::EC2::SecurityGroup")
                for r in body["Resources"].values())):
        raise ValueError("Expected one disposable workstation without separate disk resources")
    now = utc_now or (lambda: dt.datetime.now(dt.timezone.utc))
    expiry = now().replace(microsecond=0) + dt.timedelta(minutes=minutes)
    identity = safety(aws_call, region, stack, create=True)
    token, role = identity["token"], identity["role"]
    scheduler = Scheduler(aws_call, region)
    initial = guard._schedule("studio-expiry-" + token, guard.DELETE_STACK,
                              {"StackName": stack}, expiry, role)
    scheduler.create_schedule(**initial)
    guard._verify_schedule(scheduler, initial)
    tags = [{"Key": "StudioStack", "Value": stack}, {"Key": "StudioCleanupToken", "Value": token}]
    for resource in body["Resources"].values():
        resource["Properties"].setdefault("Tags", []).extend(tags)
    Path(template).write_text(json.dumps(body))
    cf_params = ["ParameterKey=" + k + ",ParameterValue=" + v for k, v in
                 (p.split("=", 1) for p in parameters)]
    response = aws_call(region, "cloudformation", "create-stack", "--stack-name", stack,
                        "--template-body", "file://" + str(template), "--parameters", *cf_params,
                        "--tags", "Key=StudioStack,Value=" + stack, "Key=StudioCleanupToken,Value=" + token,
                        "Key=StudioManaged,Value=true", "Key=StudioProfile,Value=gpu",
                        "Key=StudioSafetyStack,Value=" + identity["arn"], "--on-failure", "DELETE")
    stack_id = response.get("StackId", "")
    if not stack_id.startswith("arn:aws:cloudformation:us-east-1:401233741942:stack/" + stack + "/"):
        raise RuntimeError("Unrecognized stack identity; native expiry remains armed")
    iid, stack_owned = None, False
    started = clock()
    try:
        while clock() - started <= MAX_DISCOVERY_SECONDS:
            records = aws_call(region, "cloudformation", "describe-stacks", "--stack-name", stack_id)["Stacks"]
            if len(records) != 1 or records[0].get("StackId") != stack_id or not _tags_match(records[0], stack, token):
                raise RuntimeError("CloudFormation ownership changed")
            stack_owned = True
            state = records[0]["StackStatus"]
            if state not in ("CREATE_IN_PROGRESS", "CREATE_COMPLETE"):
                raise RuntimeError("CloudFormation creation failed: " + state)
            resources = aws_call(region, "cloudformation", "list-stack-resources", "--stack-name", stack_id)["StackResourceSummaries"]
            if any(r.get("ResourceType") not in ("AWS::EC2::Instance", "AWS::EC2::SecurityGroup") for r in resources):
                raise RuntimeError("Unexpected workstation resource type")
            found = [r["PhysicalResourceId"] for r in resources if r.get("LogicalResourceId") == "Workstation" and r.get("PhysicalResourceId")]
            if len(found) > 1 or (iid and found and found[0] != iid):
                raise RuntimeError("Changing instance identity")
            if found:
                iid = found[0]
                vm = _one_instance(aws_call, region, iid)
                if vm.get("BlockDeviceMappings"):
                    proposal = guard.plan(vm, stack, token, now(), minutes=minutes, role=role,
                                          stack_id=stack_id, terminate_at=expiry)
                    activated = guard.arm(scheduler, EC2(aws_call, region), proposal)
                    return {"stack_id": stack_id, "safety_stack": identity["arn"], "guard": activated,
                            "terminate_at": expiry.isoformat(), "cloudformation_status": state}
            sleep(POLL_SECONDS)
        raise RuntimeError("Timed out discovering workstation")
    except BaseException as exc:
        problems = _emergency(aws_call, region, stack_id, stack, token, iid, stack_owned)
        raise RuntimeError("Creation failed; verify native teardown. Stack=" + stack_id +
                           "; expiry safety stack=" + identity["arn"] + "; warnings=" + str(problems)) from exc
