"""Explicit single-owner DCV hostname. No DNS edits, extra EIPs or CPU-return path.

Hostname commands must be serialized by the operator. AWS has no conditional
EIP reassociation API; repeated ownership checks and no-allow-reassociation
prevent knowingly seizing an address from an unexpected owner.
"""
import subprocess
import time

from runtime import issue43_hostname_handoff as legacy
from runtime import issue43_gpu_acm_setup as acm
from runtime import pr65_one_shot_guard as guard
from runtime.workstation_inventory import context
from runtime.dcv_endpoint import resolve


def _target(aws, stack, *, running=True):
    selected = context(aws, legacy.REGION, stack)
    vm = selected["vm"]
    if selected["profile"] not in ("cpu", "gpu") or vm.get("VpcId") != "vpc-01bbe5d01838f70b2":
        raise ValueError("Hostname requires an owned Studio desktop in the validated VPC")
    if running and (selected["state"] != "running" or selected["stack_status"] != "CREATE_COMPLETE"):
        raise ValueError("Destination must be a running, completely created workstation")
    interfaces = vm.get("NetworkInterfaces", [])
    if len(interfaces) != 1 or interfaces[0].get("Attachment", {}).get("DeviceIndex") != 0:
        raise ValueError("DCV handoff requires one primary ENI for native public-IP recovery")
    if any(not ip.get("Primary") for ip in interfaces[0].get("PrivateIpAddresses", [])):
        raise ValueError("Secondary private addresses require separate networking review")
    selected["eni"] = interfaces[0]["NetworkInterfaceId"]
    return selected


def _address(aws):
    address = legacy._eip(aws)
    legacy._dns(aws)
    return address


def _fingerprint(address):
    return tuple(address.get(k) for k in ("InstanceId", "AssociationId", "NetworkInterfaceId"))


def _owner(aws, address):
    iid = address.get("InstanceId")
    if iid is None:
        if address.get("AssociationId") or address.get("NetworkInterfaceId"):
            raise ValueError("Unknown network interface owns the shared EIP")
        return None
    if legacy._tags(address).get("StudioDcvInstanceId") != iid:
        raise ValueError("EIP association and owner metadata disagree")
    vm = legacy._items(aws, iid)[iid]
    selected = _target(aws, legacy._tags(vm)["aws:cloudformation:stack-id"], running=False)
    if selected["instance"] != iid or selected["eni"] != address.get("NetworkInterfaceId"):
        raise ValueError("EIP owner no longer matches its CloudFormation identity")
    return selected


def _move(aws, before, destination):
    """Recheck exact association; never use allow-reassociation to steal ownership."""
    if _fingerprint(_address(aws)) != _fingerprint(before):
        raise RuntimeError("Hostname owner changed; operation aborted")
    if before.get("AssociationId"):
        aws(legacy.REGION, "ec2", "disassociate-address", "--association-id", before["AssociationId"])
    if any(_fingerprint(_address(aws))):
        raise RuntimeError("EIP disassociation not verified")
    association = None
    if destination:
        reply = aws(legacy.REGION, "ec2", "associate-address", "--allocation-id", legacy.EIP_ID,
                    "--network-interface-id", destination["eni"], "--no-allow-reassociation")
        association = reply.get("AssociationId")
        if not association:
            raise RuntimeError("EIP association response missing")
        aws(legacy.REGION, "ec2", "create-tags", "--resources", legacy.EIP_ID,
            "--tags", "Key=StudioDcvInstanceId,Value=" + destination["instance"])
    else:
        aws(legacy.REGION, "ec2", "delete-tags", "--resources", legacy.EIP_ID, "--tags", "Key=StudioDcvInstanceId")
    aws(legacy.REGION, "ec2", "delete-tags", "--resources", legacy.EIP_ID, "--tags", "Key=StudioDcvReturnStack")
    actual = _address(aws)
    expected = (destination["instance"], association, destination["eni"]) if destination else (None, None, None)
    if _fingerprint(actual) != expected or legacy._tags(actual).get("StudioDcvInstanceId") != expected[0]:
        raise RuntimeError("EIP owner/association readback mismatch")
    return actual


def _reachable(aws, selected, check_tls, *, restart=False, sleep=time.sleep):
    """Wait for native IP recovery; bounded stop/start only for a verified owned VM."""
    for cycle in range(2 if restart else 1):
        for attempt in range(24):
            fresh = context(aws, legacy.REGION, selected["stack_id"])
            if fresh["instance"] != selected["instance"]:
                raise RuntimeError("Workstation changed during hostname recovery")
            if fresh["state"] != "running":
                return None  # Only running workstations require a reachable endpoint.
            try:
                return resolve(fresh, legacy.REGION, aws, check_tls)
            except ValueError as exc:
                if not str(exc).startswith(("No running public", "DCV HTTPS", "Trusted DCV TLS")):
                    raise
                failure = exc
                if attempt < 23:
                    sleep(5)
        if not restart or cycle:
            raise RuntimeError("DCV endpoint recovery failed: " + str(failure)) from failure
        fresh = _target(aws, selected["stack_id"])
        if fresh["profile"] == "gpu":
            guard.status(aws, fresh, headroom=80)  # Never extend expiration implicitly.
        iid = fresh["instance"]
        aws(legacy.REGION, "ec2", "stop-instances", "--instance-ids", iid)
        aws(legacy.REGION, "ec2", "wait", "instance-stopped", "--instance-ids", iid)
        aws(legacy.REGION, "ec2", "start-instances", "--instance-ids", iid)
        aws(legacy.REGION, "ec2", "wait", "instance-status-ok", "--instance-ids", iid)


def _change(aws, destination, check_tls, *, destroying=False):
    before = _address(aws)
    stale = legacy._tags(before).get("StudioDcvInstanceId") if not before.get("InstanceId") else None
    if stale:
        if any(_fingerprint(before)):
            raise ValueError("Unknown association; stale-owner recovery refused")
        try:
            vm = legacy._items(aws, stale)[stale]
        except subprocess.CalledProcessError as exc:
            if "InvalidInstanceID.NotFound" not in (exc.stderr or ""):
                raise
        else:
            selected = context(aws, legacy.REGION, legacy._tags(vm)["aws:cloudformation:stack-id"])
            if selected["instance"] != stale:
                raise ValueError("Stale hostname metadata refers to a replaced instance")
            if selected["state"] == "running" and not destroying:
                _reachable(aws, selected, check_tls, restart=True)
        acm.revoke_gpu(aws, stale)
        _move(aws, before, None)
        before = _address(aws)
    previous = _owner(aws, before)
    if destination:
        if destination["profile"] == "gpu":
            guard.status(aws, destination, headroom=5)
        if before.get("InstanceId") == destination["instance"]:
            acm.prepare_gpu(aws, destination["instance"])
            endpoint = _reachable(aws, destination, check_tls)
            if endpoint is None:
                raise RuntimeError("Hostname owner is not running")
            return {"owner": destination["stack"], "endpoint": endpoint}
        # Verify its independent endpoint before changing certificate permissions or EIP.
        _reachable(aws, destination, check_tls)
        acm.prepare_gpu(aws, destination["instance"])
    try:
        moved = _move(aws, before, destination)
        endpoint = _reachable(aws, destination, check_tls) if destination else None
        if destination and endpoint is None:
            raise RuntimeError("Destination stopped during transfer; not a successful DCV assignment")
        old_endpoint = (_reachable(aws, previous, check_tls, restart=True)
                        if previous and not destroying else None)
        if _fingerprint(_address(aws)) != _fingerprint(moved):
            raise RuntimeError("Hostname owner changed during endpoint verification")
    except BaseException as exc:
        current = _address(aws)
        if _fingerprint(current) != _fingerprint(before):
            if current.get("InstanceId") not in (None, destination["instance"] if destination else None):
                raise RuntimeError("Hostname transfer failed; foreign owner, no rollback takeover") from exc
            _move(aws, current, previous)
        if previous and not destroying:
            _reachable(aws, previous, check_tls, restart=True)
        if destination:
            _reachable(aws, destination, check_tls, restart=True)
            acm.revoke_gpu(aws, destination["instance"])
        raise RuntimeError("Hostname operation failed; original association restored and endpoints checked") from exc
    if previous and (not destination or previous["instance"] != destination["instance"]):
        acm.revoke_gpu(aws, previous["instance"])
    return {"owner": destination["stack"] if destination else None,
            "hostname": legacy.HOST, "endpoint": endpoint, "previous_endpoint": old_endpoint}


def assign(aws, stack, check_tls):
    return _change(aws, _target(aws, stack), check_tls)


def release(aws, check_tls=None):
    return _change(aws, None, check_tls)


def release_if_owned(aws, selected, check_tls, *, destroying=False):
    address = legacy._eip(aws)
    iid = selected["instance"]
    if address.get("InstanceId") == iid and iid:
        return _change(aws, None, check_tls, destroying=destroying)
    if address.get("InstanceId") is None and legacy._tags(address).get("StudioDcvInstanceId") == iid and iid:
        # Native termination detaches the EIP. Clear only this verified deployment's stale metadata.
        if address.get("AssociationId") or address.get("NetworkInterfaceId"):
            raise ValueError("Unassociated EIP has conflicting network identity")
        aws(legacy.REGION, "ec2", "delete-tags", "--resources", legacy.EIP_ID,
            "--tags", "Key=StudioDcvInstanceId", "Key=StudioDcvReturnStack")
    if iid:
        acm.revoke_gpu(aws, iid)
    return {"owner": address.get("InstanceId"), "allocation_preserved": True}


def status(aws):
    address = _address(aws)
    selected = _owner(aws, address)
    return {"hostname": legacy.HOST, "public_ip": legacy.EIP_IP,
            "owner": selected["stack"] if selected else None, "instance": address.get("InstanceId"),
            "stale_owner_tag": legacy._tags(address).get("StudioDcvInstanceId") if not selected else None}
