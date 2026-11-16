"""Read-only CloudFormation/EC2 inventory; no workstation registry or cached IPs."""
import re
import subprocess

from runtime import issue43_hostname_handoff as hostname



PROFILES = {
    "cpu": {"instance_types": ["m7i.large", "m7i.xlarge"], "desktop": True,
            "instance_profile": "StudioWorkstationSSMRole", "expires_minutes": None},
    "gpu": {"instance_types": ["g4dn.xlarge"], "desktop": True,
            "instance_profile": "StudioWorkstationSSMRole", "expires_minutes": 150},
    "publisher": {"instance_types": ["m7i.large"], "desktop": False,
                  "instance_profile": "StudioEfsCiPublisher", "expires_minutes": None},
}


def stack_record(aws, region, name):
    """Only an explicit AWS not-found result means absence, never AccessDenied."""
    try:
        records = aws(region, "cloudformation", "describe-stacks", "--stack-name", name)["Stacks"]
    except subprocess.CalledProcessError as exc:
        if "ValidationError" in (exc.stderr or "") and "does not exist" in exc.stderr:
            return None
        raise
    if len(records) != 1 or name not in (records[0]["StackName"], records[0]["StackId"]):
        raise ValueError("CloudFormation stack identity mismatch")
    return records[0]


def context(aws, region, name):
    """Resolve a name/immutable ARN once; the AWS system tag proves EC2 ownership."""
    if region != hostname.REGION:
        raise ValueError("Only validated us-east-1 workstations are supported")
    stack = stack_record(aws, region, name)
    if stack is None:
        raise ValueError("Workstation not found: " + name)
    tags = hostname._tags(stack)
    profile = tags.get("StudioProfile")
    arn = stack["StackId"]
    if (tags.get("StudioManaged") != "true" or profile not in PROFILES or
            not arn.startswith(f"arn:aws:cloudformation:{region}:{hostname.ACCOUNT}:stack/{stack['StackName']}/")):
        raise ValueError("Not a managed Studio workstation stack")
    resources = aws(region, "cloudformation", "list-stack-resources", "--stack-name", arn)["StackResourceSummaries"]
    matches = [r for r in resources if r.get("LogicalResourceId") == "Workstation"]
    if len(matches) > 1 or any(r.get("ResourceType") != "AWS::EC2::Instance" for r in matches):
        raise ValueError("Ambiguous CloudFormation workstation resource")
    iid = matches[0].get("PhysicalResourceId") if matches else None
    vm = {}
    if iid:
        if not re.fullmatch(r"i-[0-9a-f]{8,17}", iid):
            raise ValueError("Invalid CloudFormation workstation ID")
        try:
            vm = hostname._items(aws, iid)[iid]
        except subprocess.CalledProcessError as exc:
            if "InvalidInstanceID.NotFound" not in (exc.stderr or ""):
                raise
        if vm and (hostname._tags(vm).get("aws:cloudformation:stack-id") != arn or
                   vm.get("InstanceType") not in PROFILES[profile]["instance_types"] or
                   (vm.get("State", {}).get("Name") != "terminated" and
                    vm.get("IamInstanceProfile", {}).get("Arn") !=
                    f"arn:aws:iam::{hostname.ACCOUNT}:instance-profile/{PROFILES[profile]['instance_profile']}")):
            raise ValueError("EC2 ownership/profile differs from selected stack")
    ip = vm.get("PublicIpAddress")
    return {"stack": stack["StackName"], "stack_id": arn, "profile": profile,
            "stack_status": stack["StackStatus"], "instance": iid, "vm": vm,
            "resources": resources, "token": tags.get("StudioCleanupToken"),
            "safety_stack": tags.get("StudioSafetyStack"),
            "state": vm.get("State", {}).get("Name", "missing"), "public_ip": ip,
            "dcv_url": f"https://{ip}:8443" if ip and PROFILES[profile]["desktop"] else None}

def list_workstations(aws, region):
    stacks = aws(region, "cloudformation", "describe-stacks")["Stacks"]
    reservations = aws(region, "ec2", "describe-instances", "--filters",
                       "Name=tag:aws:cloudformation:logical-id,Values=Workstation")["Reservations"]
    instances = {vm["InstanceId"]: vm for reservation in reservations
                 for vm in reservation["Instances"]}
    addresses = aws(region, "ec2", "describe-addresses", "--filters",
                    "Name=tag:StudioDcvHostname,Values=" + hostname.HOST)["Addresses"]
    address = next((a for a in addresses if a.get("AllocationId") == hostname.EIP_ID
                    and a.get("PublicIp") == hostname.EIP_IP), {})
    rows = []
    for stack in sorted(stacks, key=lambda item: item["StackName"]):
        tags = hostname._tags(stack)
        params = {p["ParameterKey"]: p["ParameterValue"] for p in stack.get("Parameters", [])}
        if not (tags.get("StudioManaged") == "true" or
                params.get("InstanceProfileName") == "StudioWorkstationSSMRole"):
            continue
        resources = aws(region, "cloudformation", "list-stack-resources",
                        "--stack-name", stack["StackId"])["StackResourceSummaries"]
        matches = [r for r in resources if r.get("LogicalResourceId") == "Workstation"]
        if len(matches) > 1 or any(r.get("ResourceType") != "AWS::EC2::Instance" for r in matches):
            raise ValueError("Ambiguous Workstation resource: " + stack["StackName"])
        iid = matches[0].get("PhysicalResourceId") if matches else None
        vm = instances.get(iid, {})
        vm_tags = hostname._tags(vm)
        owned = vm_tags.get("aws:cloudformation:stack-id") == stack["StackId"]
        state = vm.get("State", {}).get("Name", "missing" if iid else "not-created")
        if vm and not owned:
            state = "ownership-mismatch"
        ip = vm.get("PublicIpAddress") if owned else None
        owner = bool(iid and owned and address.get("InstanceId") == iid and
                     address.get("PublicIp") == ip and
                     hostname._tags(address).get("StudioDcvInstanceId") == iid)
        instance_type = params.get("InstanceType", vm.get("InstanceType"))
        profile = tags.get("StudioProfile") or ("gpu" if instance_type == "g4dn.xlarge" else (
            "cpu" if instance_type in ("m7i.large", "m7i.xlarge") else "unknown"))
        rows.append({"name": stack["StackName"], "stack_id": stack["StackId"],
                     "stack_status": stack["StackStatus"], "profile": profile,
                     "instance": iid, "state": state, "hostname_owner": owner,
                     "dcv_url": "https://" + (hostname.HOST if owner else ip) + ":8443"
                     if ip and state == "running" and profile != "publisher" else None,
                     "dcv_verified": False})
    deployed = {row["name"] for row in rows}
    for stack in stacks:
        tags = hostname._tags(stack)
        if tags.get("StudioManaged") == "expiry" and tags.get("StudioWorkstation") not in deployed:
            rows.append({"name": tags.get("StudioWorkstation"), "profile": "gpu",
                         "state": "safety-stack-only", "safety_stack": stack["StackId"],
                         "dcv_url": None, "dcv_verified": False})
    return rows
