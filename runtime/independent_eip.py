"""Independent Studio EIP assignment. Never releases the allocation or edits DNS."""
import re

from runtime import issue43_hostname_handoff as legacy

def _target(aws, stack):
    if not re.fullmatch(r"studio-[a-z0-9-]{1,100}", stack):
        raise ValueError("Invalid Studio stack")
    iid = legacy._stack_iid(aws, stack)
    vm = legacy._items(aws, iid)[iid]
    if vm.get("State", {}).get("Name") != "running":
        raise ValueError("Target workstation must be running")
    if vm.get("IamInstanceProfile", {}).get("Arn") != legacy.BASE_PROFILE:
        raise ValueError("Unexpected workstation instance profile")
    if vm.get("VpcId") != "vpc-01bbe5d01838f70b2":
        raise ValueError("Unexpected workstation VPC")
    if len(vm.get("NetworkInterfaces", [])) != 1:
        raise ValueError("Expected one primary ENI")
    eni = vm["NetworkInterfaces"][0]
    if eni.get("Attachment", {}).get("DeviceIndex") != 0:
        raise ValueError("Unexpected ENI")
    return iid, eni["NetworkInterfaceId"]

def _address(aws):
    a = legacy._eip(aws)
    legacy._dns(aws)
    return a

def _owner(aws, address):
    iid = address.get("InstanceId")
    if not iid:
        return None
    if legacy._tags(address).get("StudioDcvInstanceId") != iid:
        raise ValueError("EIP owner tag mismatch")
    vm = legacy._items(aws, iid)[iid]
    if vm.get("VpcId") != "vpc-01bbe5d01838f70b2" or vm.get("IamInstanceProfile", {}).get("Arn") != legacy.BASE_PROFILE:
        raise ValueError("EIP held by unrecognized instance")
    if vm.get("State", {}).get("Name") not in ("stopped", "running"):
        raise ValueError("EIP owner in unsafe state")
    return iid

def release(aws):
    a = _address(aws)
    owner = _owner(aws, a)
    if not owner:
        if a.get("AssociationId") or legacy._tags(a).get("StudioDcvInstanceId"):
            raise ValueError("Unassociated EIP has stale ownership state")
        return {"allocation": legacy.EIP_ID, "state": "unassociated"}
    if not a.get("AssociationId"):
        raise ValueError("Associated EIP missing association ID")
    aws(legacy.REGION, "ec2", "disassociate-address", "--association-id", a["AssociationId"])
    aws(legacy.REGION, "ec2", "delete-tags", "--resources", legacy.EIP_ID, "--tags", "Key=StudioDcvInstanceId")
    current = _address(aws)
    if current.get("AssociationId") or legacy._tags(current).get("StudioDcvInstanceId"):
        raise RuntimeError("EIP release readback mismatch")
    return {"allocation": legacy.EIP_ID, "state": "unassociated", "previous_owner": owner}

def assign(aws, stack, check_tls):
    iid, eni = _target(aws, stack)
    a = _address(aws)
    if a.get("AssociationId") or a.get("InstanceId") or legacy._tags(a).get("StudioDcvInstanceId"):
        raise ValueError("EIP is not unassociated; release its verified owner first")
    # TLS/DCV must be checked locally before assignment by caller; after
    # association validate public hostname and roll back on failure.
    aws(legacy.REGION, "ec2", "associate-address", "--allocation-id", legacy.EIP_ID,
        "--network-interface-id", eni, "--allow-reassociation", "false")
    try:
        aws(legacy.REGION, "ec2", "create-tags", "--resources", legacy.EIP_ID,
            "--tags", "Key=StudioDcvInstanceId,Value=" + iid)
        current = _address(aws)
        if current.get("InstanceId") != iid or current.get("NetworkInterfaceId") != eni:
            raise RuntimeError("EIP assignment readback mismatch")
        check_tls(legacy.HOST, legacy.EIP_IP)
    except BaseException:
        current = _address(aws)
        if current.get("InstanceId") == iid and current.get("AssociationId"):
            aws(legacy.REGION, "ec2", "disassociate-address", "--association-id", current["AssociationId"])
            aws(legacy.REGION, "ec2", "delete-tags", "--resources", legacy.EIP_ID, "--tags", "Key=StudioDcvInstanceId")
        raise
    return {"allocation": legacy.EIP_ID, "instance": iid, "hostname": legacy.HOST}
