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

def _owner(aws, address, *, require_stopped=True):
    iid = address.get("InstanceId")
    if not iid:
        return None
    if legacy._tags(address).get("StudioDcvInstanceId") != iid:
        raise ValueError("EIP owner tag mismatch")
    vm = legacy._items(aws, iid)[iid]
    if vm.get("VpcId") != "vpc-01bbe5d01838f70b2" or vm.get("IamInstanceProfile", {}).get("Arn") != legacy.BASE_PROFILE:
        raise ValueError("EIP held by unrecognized instance")
    state = vm.get("State", {}).get("Name")
    if require_stopped and state != "stopped":
        raise ValueError("Stop the current EIP owner before releasing its address")
    if not require_stopped and state not in ("stopped", "running"):
        raise ValueError("EIP owner is not in an approved state")
    return iid


def _cpu_owner(aws, address):
    """Verify a stopped CPU is the current EIP owner, independent of old anchor."""
    iid = _owner(aws, address)
    if not iid:
        raise ValueError("Expected an associated stopped CPU owner")
    eni = address.get("NetworkInterfaceId")
    vm = legacy._items(aws, iid)[iid]
    interfaces = vm.get("NetworkInterfaces", [])
    if (len(interfaces) != 1 or interfaces[0].get("NetworkInterfaceId") != eni
            or not address.get("AssociationId")):
        raise ValueError("CPU EIP interface/association mismatch")
    stack = legacy._tags(vm).get("aws:cloudformation:stack-name", "")
    if (not re.fullmatch(r"studio-[a-z0-9-]{3,100}", stack)
            or stack.startswith("studio-gpu-cli-")
            or vm.get("InstanceType") not in ("m7i.large", "m7i.xlarge")
            or legacy._stack_iid(aws, stack) != iid):
        raise ValueError("EIP owner is not a verified Studio CPU stack")
    return {"instance": iid, "stack": stack, "eni": eni,
            "association": address["AssociationId"]}


def gpu_preview(aws, *, now=None):
    """Read-only GPU handoff plan using a verified replacement CPU, not CPU_ID."""
    import datetime as dt
    from runtime import pr65_gpu_create
    now = now or dt.datetime.now(dt.timezone.utc)
    address = _address(aws)
    cpu = _cpu_owner(aws, address)
    gpu_id = legacy._stack_iid(aws, legacy.GPU_STACK)
    gpu = legacy._items(aws, gpu_id)[gpu_id]
    if (gpu.get("State", {}).get("Name") != "running"
            or gpu.get("ImageId") != legacy.GPU_AMI
            or gpu.get("InstanceType") != "g4dn.xlarge"
            or gpu.get("IamInstanceProfile", {}).get("Arn") != legacy.BASE_PROFILE
            or gpu.get("VpcId") != "vpc-01bbe5d01838f70b2"
            or len(gpu.get("NetworkInterfaces", [])) != 1):
        raise ValueError("Unexpected GPU instance profile, image or network")
    interface = gpu["NetworkInterfaces"][0]
    if (interface.get("Attachment", {}).get("DeviceIndex") != 0
            or interface.get("Attachment", {}).get("Status") != "attached"
            or interface.get("Association", {}).get("AllocationId")):
        raise ValueError("GPU ENI identity/attachment mismatch")
    pr65_gpu_create.preflight(aws, legacy.REGION, legacy.GPU_STACK, legacy.GPU_TOKEN)
    guard = legacy._gpu_guard(aws, gpu, now)
    return {"from": cpu["instance"], "to": gpu_id, "cpu": cpu,
            "gpu_eni": interface["NetworkInterfaceId"],
            "old_association": cpu["association"], "guard": guard}


def handoff_gpu(aws, check_public_tls, *, check_guest=None):
    """Move EIP only after guest ACM and both expiration schedules pass."""
    from runtime import issue43_hostname_handoff as handoff
    check_guest = check_guest or handoff.check_gpu_tls
    pre = gpu_preview(aws)
    check_guest(aws, pre["to"])
    fresh = gpu_preview(aws)
    if (fresh["to"], fresh["old_association"]) != (pre["to"], pre["old_association"]):
        raise RuntimeError("GPU handoff scope changed")
    try:
        aws(legacy.REGION, "ec2", "disassociate-address", "--association-id", pre["old_association"])
        current = _address(aws)
        if current.get("InstanceId") or current.get("AssociationId"):
            raise RuntimeError("EIP failed to disassociate")
        result = aws(legacy.REGION, "ec2", "associate-address", "--allocation-id", legacy.EIP_ID,
                     "--network-interface-id", pre["gpu_eni"], "--no-allow-reassociation")
        if not result.get("AssociationId"):
            raise RuntimeError("GPU EIP association missing")
        aws(legacy.REGION, "ec2", "create-tags", "--resources", legacy.EIP_ID,
            "--tags", "Key=StudioDcvInstanceId,Value=" + pre["to"],
            "Key=StudioDcvReturnStack,Value=" + pre["cpu"]["stack"])
        actual = _address(aws)
        if (actual.get("InstanceId") != pre["to"]
                or actual.get("NetworkInterfaceId") != pre["gpu_eni"]
                or actual.get("AssociationId") != result["AssociationId"]
                or legacy._tags(actual).get("StudioDcvInstanceId") != pre["to"]
                or legacy._tags(actual).get("StudioDcvReturnStack") != pre["cpu"]["stack"]):
            raise RuntimeError("GPU EIP owner readback mismatch")
        check_public_tls(legacy.HOST, legacy.EIP_IP)
        return {"owner": pre["to"], "previous_cpu": pre["from"],
                "guard": fresh["guard"], "hostname": legacy.HOST}
    except BaseException as error:
        # Never steal an EIP from a third-party owner. Restore only a
        # demonstrably unassociated allocation or our exact GPU association.
        current = _address(aws)
        if current.get("InstanceId") not in (None, pre["to"]):
            raise RuntimeError("GPU handoff failed; foreign EIP owner, manual recovery") from error
        if current.get("InstanceId") == pre["to"]:
            aws(legacy.REGION, "ec2", "disassociate-address", "--association-id", current["AssociationId"])
        current = _address(aws)
        if current.get("InstanceId") or current.get("AssociationId"):
            raise RuntimeError("GPU handoff failed; EIP rollback unverified") from error
        restored = aws(legacy.REGION, "ec2", "associate-address", "--allocation-id", legacy.EIP_ID,
                       "--network-interface-id", pre["cpu"]["eni"], "--no-allow-reassociation")
        aws(legacy.REGION, "ec2", "create-tags", "--resources", legacy.EIP_ID,
            "--tags", "Key=StudioDcvInstanceId,Value=" + pre["from"])
        aws(legacy.REGION, "ec2", "delete-tags", "--resources", legacy.EIP_ID,
            "--tags", "Key=StudioDcvReturnStack")
        actual = _address(aws)
        if (not restored.get("AssociationId") or actual.get("InstanceId") != pre["from"]
                or legacy._tags(actual).get("StudioDcvInstanceId") != pre["from"]):
            raise RuntimeError("GPU handoff failed; replacement CPU rollback unverified") from error
        raise RuntimeError("GPU handoff failed; EIP restored to replacement CPU") from error



def cpu_return_preview(aws, gpu_id):
    """Read-only GPU -> verified replacement CPU plan; never use old CPU_ID."""
    if not re.fullmatch(r"i-[0-9a-f]{8,17}", gpu_id or ""):
        raise ValueError("Invalid GPU instance ID")
    address = _address(aws)
    tags = legacy._tags(address)
    stack = tags.get("StudioDcvReturnStack", "")
    if (not re.fullmatch(r"studio-[a-z0-9-]{3,100}", stack)
            or stack.startswith("studio-gpu-cli-")):
        raise ValueError("GPU EIP missing verified CPU return stack")
    iid = legacy._stack_iid(aws, stack)
    cpu = legacy._items(aws, iid)[iid]
    if (cpu.get("State", {}).get("Name") != "stopped"
            or cpu.get("InstanceType") not in ("m7i.large", "m7i.xlarge")
            or cpu.get("VpcId") != "vpc-01bbe5d01838f70b2"
            or cpu.get("IamInstanceProfile", {}).get("Arn") != legacy.BASE_PROFILE
            or len(cpu.get("NetworkInterfaces", [])) != 1
            or cpu["NetworkInterfaces"][0].get("Attachment", {}).get("DeviceIndex") != 0):
        raise ValueError("Replacement CPU is not a verified stopped workstation")
    eni = cpu["NetworkInterfaces"][0]["NetworkInterfaceId"]
    owner = address.get("InstanceId")
    if owner not in (None, gpu_id) or tags.get("StudioDcvInstanceId") != gpu_id:
        raise ValueError("GPU does not own the EIP according to its tag")
    if owner:
        gpu = legacy._items(aws, gpu_id)[gpu_id]
        if (gpu.get("State", {}).get("Name") not in ("stopped", "terminated")
                or legacy._tags(gpu).get("StudioStack") != legacy.GPU_STACK
                or legacy._tags(gpu).get("StudioCleanupToken") != legacy.GPU_TOKEN
                or not address.get("AssociationId")):
            raise ValueError("GPU must be stopped and match its cleanup scope")
    elif address.get("AssociationId"):
        raise ValueError("Unassociated EIP has unexpected association")
    return {"from": gpu_id, "to": iid, "cpu_stack": stack, "cpu_eni": eni,
            "association": address.get("AssociationId"),
            "allocation_id": legacy.EIP_ID, "hostname": legacy.HOST,
            "ip": legacy.EIP_IP, "cpu_will_remain_stopped": True}


def restore_cpu(aws, gpu_id):
    """Return EIP from stopped GPU to the recorded, verified source CPU."""
    if not re.fullmatch(r"i-[0-9a-f]{8,17}", gpu_id or ""):
        raise ValueError("Invalid GPU instance ID")
    address = _address(aws)
    cpu_stack = legacy._tags(address).get("StudioDcvReturnStack")
    if (not cpu_stack or not re.fullmatch(r"studio-[a-z0-9-]{3,100}", cpu_stack)
            or cpu_stack.startswith("studio-gpu-cli-")):
        raise ValueError("GPU EIP missing verified CPU return stack")
    cpu_id = legacy._stack_iid(aws, cpu_stack)
    cpu = legacy._items(aws, cpu_id)[cpu_id]
    if (cpu.get("State", {}).get("Name") != "stopped"
            or cpu.get("InstanceType") not in ("m7i.large", "m7i.xlarge")
            or cpu.get("IamInstanceProfile", {}).get("Arn") != legacy.BASE_PROFILE
            or cpu.get("VpcId") != "vpc-01bbe5d01838f70b2"
            or len(cpu.get("NetworkInterfaces", [])) != 1
            or cpu["NetworkInterfaces"][0].get("Attachment", {}).get("DeviceIndex") != 0):
        raise ValueError("Replacement CPU is not a verified stopped workstation")
    eni = cpu["NetworkInterfaces"][0]["NetworkInterfaceId"]
    owner = address.get("InstanceId")
    tagged = legacy._tags(address).get("StudioDcvInstanceId")
    if owner == cpu_id and tagged == cpu_id:
        if address.get("NetworkInterfaceId") != eni or not address.get("AssociationId"):
            raise ValueError("CPU EIP association mismatch")
        return {"owner": cpu_id, "state": "already-restored"}
    if owner not in (gpu_id, None) or tagged != gpu_id:
        raise ValueError("EIP is not owned by the expected GPU")
    if owner:
        gpu = legacy._items(aws, gpu_id)[gpu_id]
        if (gpu.get("State", {}).get("Name") not in ("stopped", "terminated")
                or legacy._tags(gpu).get("StudioStack") != legacy.GPU_STACK
                or legacy._tags(gpu).get("StudioCleanupToken") != legacy.GPU_TOKEN):
            raise ValueError("GPU must be stopped and match its cleanup scope")
        if not address.get("AssociationId"):
            raise ValueError("GPU association ID missing")
        aws(legacy.REGION, "ec2", "disassociate-address",
            "--association-id", address["AssociationId"])
    current = _address(aws)
    if current.get("AssociationId") or current.get("InstanceId"):
        raise RuntimeError("EIP disassociation not verified")
    result = aws(legacy.REGION, "ec2", "associate-address", "--allocation-id", legacy.EIP_ID,
                 "--network-interface-id", eni, "--no-allow-reassociation")
    aws(legacy.REGION, "ec2", "create-tags", "--resources", legacy.EIP_ID,
        "--tags", "Key=StudioDcvInstanceId,Value=" + cpu_id)
    current = _address(aws)
    if (not result.get("AssociationId") or current.get("InstanceId") != cpu_id
            or current.get("NetworkInterfaceId") != eni
            or legacy._tags(current).get("StudioDcvInstanceId") != cpu_id):
        raise RuntimeError("Replacement CPU EIP restoration unverified")
    aws(legacy.REGION, "ec2", "delete-tags", "--resources", legacy.EIP_ID,
        "--tags", "Key=StudioDcvReturnStack")
    if legacy._tags(_address(aws)).get("StudioDcvReturnStack"):
        raise RuntimeError("Stale GPU CPU-return tag after restoration")
    return {"owner": cpu_id, "state": "restored"}


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
