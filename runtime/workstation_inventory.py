"""Read-only CloudFormation/EC2 inventory; no workstation registry or cached IPs."""
from runtime import issue43_hostname_handoff as hostname


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
        profile = "gpu" if instance_type == "g4dn.xlarge" else (
            "cpu" if instance_type in ("m7i.large", "m7i.xlarge") else "unknown")
        rows.append({"name": stack["StackName"], "stack_id": stack["StackId"],
                     "stack_status": stack["StackStatus"], "profile": profile,
                     "instance": iid, "state": state, "hostname_owner": owner,
                     "dcv_url": "https://" + (hostname.HOST if owner else ip) + ":8443"
                     if ip and state == "running" else None,
                     "dcv_verified": False})
    return rows
