"""Bounded, supervised GPU CloudFormation creation with Task 7-style expiry.

Task 2 implementation only. The public CLI retains a hard live-launch block
until a separately approved Task 3 activation.
"""
import datetime as dt
import json
import time

from runtime import pr65_one_shot_guard as guard

POLICY = "StudioPr65ExactTaggedExpiry"
POLL_SECONDS = 3
MAX_DISCOVERY_SECONDS = 120


class Scheduler:
    def __init__(self, aws_call, region):
        self.aws_call, self.region = aws_call, region

    def create_schedule(self, **spec):
        return self.aws_call(self.region, "scheduler", "create-schedule",
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


def preflight(aws_call, region, stack, token):
    """Verify independent schedule destination and exact scoped role BEFORE create."""
    group = aws_call(region, "scheduler", "get-schedule-group", "--name", guard.GROUP)
    if group.get("State") != "ACTIVE":
        raise ValueError("Expiration Scheduler group is not ACTIVE")
    role = aws_call(region, "iam", "get-role", "--role-name", guard.ROLE.rsplit("/", 1)[-1])["Role"]
    if role.get("Arn") != guard.ROLE:
        raise ValueError("Unexpected expiration role")
    trust = role.get("AssumeRolePolicyDocument", {})
    statements = trust.get("Statement", [])
    group_arn = ("arn:aws:scheduler:us-east-1:401233741942:"
                 "schedule-group/" + guard.GROUP)
    if not any(s.get("Effect") == "Allow" and s.get("Action") == "sts:AssumeRole" and
               s.get("Principal", {}).get("Service") == "scheduler.amazonaws.com" and
               s.get("Condition", {}).get("ArnEquals", {}).get("aws:SourceArn") == group_arn and
               s.get("Condition", {}).get("StringEquals", {}).get("aws:SourceAccount") == "401233741942"
               for s in statements):
        raise ValueError("Expiration role scheduler trust mismatch")
    doc = aws_call(region, "iam", "get-role-policy",
                   "--role-name", guard.ROLE.rsplit("/", 1)[-1],
                   "--policy-name", POLICY)["PolicyDocument"]
    if isinstance(doc, str):
        doc = json.loads(doc)
    allows = [s for s in doc.get("Statement", []) if s.get("Effect") == "Allow"]
    expected = {
        "ec2:TerminateInstances": "instance",
        "ec2:DeleteVolume": "volume",
    }
    if len(allows) != 2:
        raise ValueError("Expiration role must have exactly two scoped grants")
    for action, kind in expected.items():
        scoped = [s for s in allows if s.get("Action") == action]
        resource = "arn:aws:ec2:us-east-1:401233741942:" + kind + "/*"
        if len(scoped) != 1 or scoped[0].get("Resource") != resource or (
            scoped[0].get("Condition", {}).get("StringEquals") != {
                "ec2:ResourceTag/StudioStack": stack,
                "ec2:ResourceTag/StudioCleanupToken": token,
            }
        ):
            raise ValueError("Expiration role permissions do not match exact test scope")
    policies = aws_call(region, "iam", "list-role-policies",
                        "--role-name", guard.ROLE.rsplit("/", 1)[-1])
    if policies.get("PolicyNames") != [POLICY] or policies.get("IsTruncated", False):
        raise ValueError("Unexpected inline policies on expiration role")
    attached = aws_call(region, "iam", "list-attached-role-policies",
                        "--role-name", guard.ROLE.rsplit("/", 1)[-1])
    if attached.get("AttachedPolicies") or attached.get("IsTruncated", False):
        raise ValueError("Unexpected managed policies on expiration role")


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
    """Best effort; never claim success or conceal orphaned retained EBS."""
    problems = []
    if iid:
        try:
            instance = _one_instance(aws_call, region, iid)
            if not _tags_match(instance, stack, token):
                raise RuntimeError("Emergency instance ownership mismatch")
            aws_call(region, "ec2", "terminate-instances", "--instance-ids", iid)
        except Exception as exc:
            problems.append("instance termination: " + str(exc))
    if stack_owned:
        try:
            aws_call(region, "cloudformation", "delete-stack", "--stack-name", stack_id)
        except Exception as exc:
            problems.append("stack deletion: " + str(exc))
    else:
        problems.append("stack ownership not verified; no automatic stack delete")
    return problems


def create_guarded(aws_call, region, stack, token, template, parameters,
                   *, clock=time.monotonic, sleep=time.sleep, utc_now=None):
    """Create asynchronously; arm both schedules as soon as the retained root EBS is seen.

    On discovery/arming failure request termination and stack deletion, but
    ALWAYS require human verification of retained EBS and the final stack state.
    """
    if region != "us-east-1":
        raise ValueError("Only validated us-east-1 is supported")
    # Do not create ANY CloudFormation resources if the independent IAM/scheduler
    # configuration is missing, untrusted or scoped to a different test.
    preflight(aws_call, region, stack, token)
    cf_params = ["ParameterKey=" + k + ",ParameterValue=" + v for k, v in
                 (p.split("=", 1) for p in parameters)]
    response = aws_call(region, "cloudformation", "create-stack",
                        "--stack-name", stack, "--template-body", "file://" + str(template),
                        "--parameters", *cf_params,
                        "--tags", "Key=StudioStack,Value=" + stack,
                        "Key=StudioCleanupToken,Value=" + token,
                        "--disable-rollback")
    stack_id = response.get("StackId")
    if not stack_id or not stack_id.startswith(
            "arn:aws:cloudformation:us-east-1:401233741942:stack/" + stack + "/"):
        raise RuntimeError("Unrecognized StackId from CreateStack; investigate manually")
    iid = None
    stack_owned = False
    started = clock()
    now = utc_now or (lambda: dt.datetime.now(dt.timezone.utc))
    try:
        while clock() - started <= MAX_DISCOVERY_SECONDS:
            stacks = aws_call(region, "cloudformation", "describe-stacks",
                              "--stack-name", stack_id)["Stacks"]
            if (len(stacks) != 1 or stacks[0].get("StackId") != stack_id or
                    stacks[0].get("StackName") != stack or
                    not _tags_match(stacks[0], stack, token)):
                raise RuntimeError("CloudFormation stack identity or ownership changed")
            stack_owned = True
            state = stacks[0]["StackStatus"]
            if state not in ("CREATE_IN_PROGRESS", "CREATE_COMPLETE"):
                raise RuntimeError("CloudFormation failed before guard was armed: " + state)
            summaries = aws_call(region, "cloudformation", "list-stack-resources",
                                 "--stack-name", stack_id)["StackResourceSummaries"]
            if any(x.get("ResourceType") not in (
                    "AWS::EC2::Instance", "AWS::EC2::SecurityGroup", "AWS::EC2::LaunchTemplate")
                   for x in summaries):
                raise RuntimeError("Unexpected CloudFormation resource type")
            found = [x["PhysicalResourceId"] for x in summaries
                     if x.get("LogicalResourceId") == "Workstation" and x.get("PhysicalResourceId")]
            if len(found) > 1 or (iid and found and found[0] != iid):
                raise RuntimeError("Multiple or changing instance identities")
            if found:
                iid = found[0]
                vm = _one_instance(aws_call, region, iid)
                if not _tags_match(vm, stack, token):
                    raise RuntimeError("EC2 instance ownership mismatch")
                mappings = [m for m in vm.get("BlockDeviceMappings", []) if m.get("DeviceName") == "/dev/sda1"]
                if len(mappings) == 1 and mappings[0].get("Ebs", {}).get("VolumeId"):
                    vid = mappings[0]["Ebs"]["VolumeId"]
                    vols = aws_call(region, "ec2", "describe-volumes",
                                    "--volume-ids", vid).get("Volumes", [])
                    if len(vols) != 1 or vols[0].get("VolumeId") != vid:
                        raise RuntimeError("Root EBS inventory mismatch")
                    if not _tags_match(vols[0], stack, token):
                        raise RuntimeError("Retained EBS ownership mismatch")
                    if vols[0].get("State") == "in-use":
                        proposal = guard.plan(vm, vols[0], stack, token, now())
                        activated = guard.arm(Scheduler(aws_call, region), EC2(aws_call, region), proposal)
                        return {"stack_id": stack_id, "cloudformation_status": state,
                                "guard": activated, "terminate_at": proposal["terminate_at"],
                                "volume_cleanup_at": proposal["volume_cleanup_at"],
                                "manual_ebs_audit_required": True}
            sleep(POLL_SECONDS)
        raise RuntimeError("Timed out discovering instance and retained root EBS")
    except BaseException as exc:
        problems = _emergency(aws_call, region, stack_id, stack, token, iid, stack_owned)
        raise RuntimeError(
            "GPU guard NOT verified; emergency termination/stack deletion requested. "
            "Check CloudFormation, exact instance, SG, launch template, and retained EBS manually. "
            "Instance=" + str(iid) + " StackId=" + stack_id +
            "; failure: " + str(exc)[:240] +
            ("; cleanup warnings: " + "; ".join(problems) if problems else "")
        ) from exc
