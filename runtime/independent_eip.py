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
    if vm.get("InstanceType") not in ("m7i.large", "m7i.xlarge", "g4dn.xlarge"):
        raise ValueError("Unsupported workstation type")
    if vm.get("InstanceType") == "g4dn.xlarge":
        if stack != legacy.GPU_STACK or vm.get("ImageId") != legacy.GPU_AMI:
            raise ValueError("GPU target outside guarded acceptance scope")
        import datetime as dt
        from runtime import pr65_gpu_create
        pr65_gpu_create.preflight(aws, legacy.REGION, legacy.GPU_STACK, legacy.GPU_TOKEN)
        legacy._gpu_guard(aws, vm, dt.datetime.now(dt.timezone.utc))
    elif stack == legacy.GPU_STACK:
        raise ValueError("GPU stack cannot contain a CPU target")
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
    if vm.get("State", {}).get("Name") != "stopped":
        raise ValueError("Stop the current EIP owner before releasing its address")
    return iid

def release(aws):
    a = _address(aws)
    owner = _owner(aws, a)
    if not owner:
        if a.get("AssociationId") or legacy._tags(a).get("StudioDcvInstanceId"):
            raise ValueError("Unassociated EIP has stale ownership state")
        return {"allocation": legacy.EIP_ID, "state": "unassociated"}
    if not a.get("AssociationId") or not a.get("NetworkInterfaceId"):
        raise ValueError("Associated EIP missing association identity")
    aws(legacy.REGION, "ec2", "disassociate-address", "--association-id", a["AssociationId"])
    current = _address(aws)
    if current.get("AssociationId") or current.get("InstanceId"):
        raise RuntimeError("EIP disassociation readback mismatch; ownership tag retained")
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
    _guest_tls(aws, iid)
    # Only after guest TLS/DCV readiness is proven do we move the public address.
    aws(legacy.REGION, "ec2", "associate-address", "--allocation-id", legacy.EIP_ID,
        "--network-interface-id", eni, "--no-allow-reassociation")
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


def _guest_tls(aws, iid, *, sleep=None):
    """Require DCV, trusted ACM certificate and matching private key locally."""
    import json
    import time
    sleep = sleep or time.sleep
    commands = [
        "set -eu",
        "test -f /root/bootstrap/semaphore/linux_vdi_config_host_ready.lock",
        "systemctl is-active gdm3 dcvserver >/dev/null",
        "dcv describe-session console >/dev/null",
        "openssl x509 -in /etc/dcv/dcv.pem -noout -checkend 3600 >/dev/null",
        "openssl x509 -in /etc/dcv/dcv.pem -noout -checkhost " + legacy.HOST + " | grep -q 'does match'",
        "test \"$(openssl x509 -in /etc/dcv/dcv.pem -pubkey -noout | sha256sum | cut -d' ' -f1)\" = \"$(openssl pkey -in /etc/dcv/dcv.key -pubout | sha256sum | cut -d' ' -f1)\"",
        "timeout 12 openssl s_client -connect 127.0.0.1:8443 -servername " + legacy.HOST + " -verify_hostname " + legacy.HOST + " -verify_return_error -brief </dev/null >/dev/null 2>&1",
        "echo STUDIO_EIP_TLS_READY",
    ]
    result = aws(legacy.REGION, "ssm", "send-command", "--instance-ids", iid,
                 "--document-name", "AWS-RunShellScript", "--parameters", json.dumps({"commands": commands}),
                 "--timeout-seconds", "90")
    cid = result["Command"]["CommandId"]
    for _ in range(20):
        sleep(3)
        try:
            reply = aws(legacy.REGION, "ssm", "get-command-invocation", "--command-id", cid,
                        "--instance-id", iid)
        except Exception as exc:
            if "InvocationDoesNotExist" in str(exc):
                continue
            raise
        if reply.get("Status") == "Success":
            if reply.get("ResponseCode") != 0 or "STUDIO_EIP_TLS_READY" not in reply.get("StandardOutputContent", "").splitlines():
                raise RuntimeError("Guest DCV certificate readiness missing")
            return
        if reply.get("Status") in ("Failed", "TimedOut", "Cancelled"):
            raise RuntimeError("Guest DCV certificate readiness failed")
    raise RuntimeError("Guest DCV certificate readiness timed out")
