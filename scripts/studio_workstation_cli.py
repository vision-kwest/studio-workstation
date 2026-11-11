#!/usr/bin/env python3
"""Small AWS CLI/CloudFormation wrapper for the validated CPU RES workstation.

No external Python dependencies. Uses existing render_workstation.py and AWS CLI.
Commands run when explicitly invoked; use --dry-run to preview create, and --delete-stack for teardown.
"""
import argparse
import datetime as dt
import ipaddress
import json
import re
import socket
import ssl
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_STACK = "studio-workstation-cpu-v013"
DEFAULT_BUCKET = "studio-workstation-releases-401233741942-us-east-1"
DEFAULT_AMI = "ami-0d6dd9e8265d48906"
DEFAULT_VPC = "vpc-01bbe5d01838f70b2"
DEFAULT_SUBNET = "subnet-0986937ce71fb97cb"
DEFAULT_PROFILE = "StudioWorkstationSSMRole"
DEFAULT_REGION = "us-east-1"
GPU_AMI = "ami-027c98f85b155c2ac"
GPU_PREFIX = "releases/pr65-pinned-download-084e704e/res-2026.06"
GPU_APP_PREFIX = "releases/task7-pr61-postreboot-a8ec326/res-2026.06"
GPU_VERSIONS = {
    "installation-scripts.tar.gz": "bN0To3QzfVqVsN5P_6YmTraGIQuHcrtB",
    "virtual-desktop.tar.gz": "nzyYrLhYAQ.OTFNpFE_xSHPaK06T1V53",
}
GPU_KEYS = {
    "installation-scripts.tar.gz": GPU_PREFIX + "/installation-scripts.tar.gz",
    "virtual-desktop.tar.gz": GPU_APP_PREFIX + "/virtual-desktop.tar.gz",
}

# The PR #65 GPU acceptance launcher is limited to this exact stack/token.
# Automatic TLS/hostname orchestration never overrides its expiration guard.
GPU_ACCEPTANCE_LIVE_ENABLED = True
GPU_ACCEPTANCE_STACK = "studio-gpu-cli-accept65"
GPU_ACCEPTANCE_TOKEN = "pr65-b23c8e5fdf67b683cd35"
TRUSTED_DCV_HOSTNAME = "dcv.kwesidavis.com"  # Issue #43: opt-in, never an IP alias



def run(args, *, capture=True):
    # AWS CLI v2 may open a full-screen pager in CloudShell. Never block
    # a guarded deployment while rendering read-only JSON output.
    if args and args[0] == "aws":
        args = [*args, "--no-cli-pager"]
    if capture:
        return subprocess.check_output(args, text=True, stderr=subprocess.PIPE)
    subprocess.check_call(args)
    return ""

def aws(region, *args):
    output = run(["aws", *args, "--region", region, "--output", "json"])
    # Several successful AWS writes (IAM policy, EC2 tags/EIP detach) emit no JSON.
    # Empty stdout is success only because run() already raises on nonzero exit.
    return json.loads(output) if output.strip() else {}

def instance_id(stack, region):
    result = aws(region, "cloudformation", "describe-stack-resource",
                 "--stack-name", stack, "--logical-resource-id", "Workstation")
    return result["StackResourceDetail"]["PhysicalResourceId"]

def describe(stack, region):
    iid = instance_id(stack, region)
    vm = aws(region, "ec2", "describe-instances", "--instance-ids", iid)["Reservations"][0]["Instances"][0]
    return {"stack": stack, "instance": iid, "state": vm["State"]["Name"],
            "public_ip": vm.get("PublicIpAddress"),
            "dcv_url": f"https://{vm['PublicIpAddress']}:8443" if vm.get("PublicIpAddress") else None}

def verified_dcv_hostname(hostname, public_ip):
    """Only advertise a trusted URL when DNS AND system TLS verification pass.

    This reads public DNS from the user's client. It never bypasses validation,
    changes the instance, or promotes a self-signed IP endpoint to trusted.
    """
    if hostname != TRUSTED_DCV_HOSTNAME or not public_ip:
        raise ValueError("Unconfigured hostname or instance public IP missing")
    try:
        addresses = {result[4][0] for result in
                     socket.getaddrinfo(hostname, 8443, family=socket.AF_INET,
                                        type=socket.SOCK_STREAM)}
        if addresses != {public_ip}:
            raise ValueError("DNS A record does not match this stack's public IP")
        context = ssl.create_default_context()
        # Connect to the verified EC2 address, not a second DNS lookup.
        # SNI/certificate verification still authenticate the requested hostname.
        with socket.create_connection((public_ip, 8443), timeout=5) as connection:
            with context.wrap_socket(connection, server_hostname=hostname):
                pass
    except (OSError, ssl.SSLError) as exc:
        raise ValueError("Trusted DCV TLS verification failed: " + str(exc)) from exc
    return f"https://{hostname}:8443"


def gpu_release_preflight(args):
    """Read-only preflight of pinned AMI and S3 object versions."""
    images = aws(args.region, "ec2", "describe-images", "--image-ids", GPU_AMI)["Images"]
    if len(images) != 1 or images[0]["State"] != "available" or images[0]["OwnerId"] != "401233741942":
        raise ValueError("Validated GPU AMI unavailable or unexpected owner")
    for name, version in GPU_VERSIONS.items():
        key = GPU_KEYS[name]
        obj = aws(args.region, "s3api", "head-object", "--bucket", args.bucket,
                  "--key", key, "--version-id", version)
        if obj.get("VersionId") != version:
            raise ValueError("GPU release object version mismatch: " + name)
        # Fail closed if the current object differs from the pinned version,
        # even though bootstrap and app loaders use explicit VersionIds.
        latest = aws(args.region, "s3api", "head-object", "--bucket", args.bucket,
                     "--key", key)
        if latest.get("VersionId") != version:
            raise ValueError("GPU release latest object differs from validated version: " + name)


def gpu_ready(stack, region):
    """Read-only guest readiness via bounded SSM diagnostics; no secrets."""
    import time
    iid = instance_id(stack, region)
    commands = [
        "set -e",
        "test -f /root/bootstrap/semaphore/linux_vdi_config_host_ready.lock",
        "systemctl is-active gdm3 dcvserver",
        "dcv describe-session console",
        "nvidia-smi -L | grep -q 'Tesla T4'",
        "pgrep -x Xorg",
        "DISPLAY=:0 XAUTHORITY=/run/user/119/gdm/Xauthority __GLX_VENDOR_LIBRARY_NAME=nvidia glxinfo -B | grep -q 'OpenGL renderer string: Tesla T4'",
    ]
    response = aws(region, "ssm", "send-command", "--instance-ids", iid,
                   "--document-name", "AWS-RunShellScript",
                   "--parameters", json.dumps({"commands": commands, "executionTimeout": ["45"]}),
                   "--timeout-seconds", "60", "--comment", "Studio GPU readiness: no credentials")
    cid = response["Command"]["CommandId"]
    for _ in range(18):
        time.sleep(5)
        try:
            result = aws(region, "ssm", "get-command-invocation",
                         "--command-id", cid, "--instance-id", iid)
        except subprocess.CalledProcessError:
            continue
        if result["Status"] == "Success":
            print("GPU READY: RES, GDM, DCV, Xorg and Tesla T4 OpenGL")
            return
        if result["Status"] in ("Failed", "Cancelled", "TimedOut"):
            raise ValueError("GPU readiness failed: " + result.get("StandardErrorContent", "")[-1200:])
    raise ValueError("GPU readiness timed out; inspect SSM and RES bootstrap logs")


def wait_gpu_bootstrap(stack, region, *, attempts=12, sleep=time.sleep):
    """Wait through RES reboots/SSM reconnects without bypassing its readiness gate."""
    for attempt in range(attempts):
        try:
            gpu_ready(stack, region)
            return
        except subprocess.CalledProcessError as exc:
            # SSM can disconnect while RES reboots. Never retry an IAM denial.
            failure = (exc.stderr or "") + " " + str(exc)
            if not any(message in failure for message in
                       ("TargetNotConnected", "InvalidInstanceId",
                        "InvalidInstanceID.NotFound", "InvocationDoesNotExist")):
                raise
        except ValueError as exc:
            if not any(message in str(exc) for message in
                       ("GPU readiness failed", "GPU readiness timed out")):
                raise
        if attempt + 1 < attempts:
            sleep(15)
    raise RuntimeError("GPU RES/DCV bootstrap did not become ready; "
                       "PR #65 instance and volume expiry schedules remain armed")


def trusted_owner(stack, instance, *, manager=aws):
    """Only the already-approved CPU/GPU stack may claim the shared EIP."""
    from runtime import issue43_hostname_handoff as handoff
    expected = {DEFAULT_STACK: handoff.CPU_ID,
                GPU_ACCEPTANCE_STACK: instance}
    if stack not in expected or instance != expected[stack]:
        raise ValueError("Shared hostname is restricted to the approved CPU/GPU stack")
    if handoff._stack_iid(manager, stack) != instance:
        raise ValueError("CloudFormation workstation identity changed")
    address = handoff._eip(manager)
    if (address.get("InstanceId") != instance or
            handoff._tags(address).get("StudioDcvInstanceId") != instance):
        raise ValueError("Shared EIP is not owned by this workstation")
    handoff._dns(manager)
    return handoff.HOST, handoff.EIP_IP


def trusted_url(stack, instance, *, manager=aws, check_tls=verified_dcv_hostname):
    host, ip = trusted_owner(stack, instance, manager=manager)
    return check_tls(host, ip)


def shared_gpu_source_ready(*, manager=aws):
    """Before a billable GPU create/start, prove CPU is stopped and owns the EIP."""
    from runtime import issue43_hostname_handoff as handoff
    if handoff._stack_iid(manager, handoff.CPU_STACK) != handoff.CPU_ID:
        raise ValueError("Original CPU CloudFormation identity changed")
    cpu = handoff._items(manager, handoff.CPU_ID)[handoff.CPU_ID]
    if (cpu.get("State", {}).get("Name") != "stopped" or
            cpu.get("NetworkInterfaces", [{}])[0].get("NetworkInterfaceId")
            != handoff.CPU_ENI):
        raise ValueError("CPU must be stopped and have its original ENI before GPU launch")
    trusted_owner(DEFAULT_STACK, handoff.CPU_ID, manager=manager)



def gpu_restart_preflight(stack, instance, region, *, manager=aws, now=None):
    """Verify PR65 guards and restart headroom BEFORE starting billable EC2."""
    from runtime import issue43_hostname_handoff as handoff
    from runtime import pr65_gpu_create

    if (stack, region) != (GPU_ACCEPTANCE_STACK, DEFAULT_REGION):
        raise ValueError("GPU restart is restricted to the approved stack and region")
    if handoff._stack_iid(manager, stack) != instance:
        raise ValueError("GPU CloudFormation instance identity changed")
    gpu = handoff._items(manager, instance)[instance]
    if gpu.get("State", {}).get("Name") != "stopped":
        raise ValueError("GPU must be stopped before restart")
    pr65_gpu_create.preflight(manager, region, stack, GPU_ACCEPTANCE_TOKEN)
    now = now or dt.datetime.now(dt.timezone.utc)
    if now.tzinfo is None or now.utcoffset() != dt.timedelta(0):
        raise ValueError("GPU restart clock must be UTC")
    try:
        expiry = handoff._gpu_guard(manager, gpu, now)
    except ValueError as exc:
        raise ValueError("GPU restart refused before EC2 start: " + str(exc)) from exc
    # Reserve bootstrap/startup time in addition to the existing 60-minute
    # handoff safety margin. A late restart must not incur EC2 charges.
    required = handoff.SAFETY_MARGIN + dt.timedelta(minutes=20)
    terminate_at = dt.datetime.fromisoformat(expiry["terminate_at"])
    if terminate_at - now < required:
        raise ValueError("GPU restart refused before EC2 start: fewer than "
                         "80 minutes remain before automatic termination")
    return expiry


def complete_gpu_setup(stack, region, instance, *, manager=aws,
                       wait=wait_gpu_bootstrap):
    """Run existing ACM + EIP components AFTER PR65 guarded launch/start."""
    if stack != GPU_ACCEPTANCE_STACK or region != DEFAULT_REGION:
        raise ValueError("GPU trusted setup limited to approved acceptance scope")
    wait(stack, region)
    from runtime import issue43_gpu_acm_setup as acm
    from runtime import issue43_hostname_handoff as handoff
    result = acm.prepare_gpu(manager)
    if result["instance_id"] != instance:
        raise RuntimeError("GPU instance changed during ACM enrollment")
    transfer = handoff.handoff_gpu(manager, verified_dcv_hostname, allow_live=True)
    if transfer["owner"] != instance:
        raise RuntimeError("GPU EIP handoff owner mismatch")
    # handoff_gpu already verified trusted TLS, the exact EIP owner/tag,
    # and unchanged DNS; avoid a second, non-rollback-protected network check.
    return "https://" + handoff.HOST + ":8443"


def complete_gpu_stop(stack, instance, *, manager=aws):
    """After EC2 confirms stopped, restore CPU EIP and revoke GPU-only IAM."""
    from runtime import issue43_gpu_acm_setup as acm
    from runtime import issue43_hostname_handoff as handoff
    if stack != GPU_ACCEPTANCE_STACK:
        raise ValueError("Not an approved GPU acceptance stack")
    address = handoff._eip(manager)
    owner = address.get("InstanceId")
    tag_owner = handoff._tags(address).get("StudioDcvInstanceId")
    if owner == instance or (owner is None and tag_owner == instance):
        result = handoff.restore_cpu(manager, instance, allow_live=True)
        if result["owner"] != handoff.CPU_ID:
            raise RuntimeError("CPU EIP return not verified")
    elif owner != handoff.CPU_ID or tag_owner != handoff.CPU_ID:
        raise ValueError("Unexpected EIP owner; refusing GPU cleanup")
    return acm.revoke_gpu(manager, instance)


def interactive_ssm(instance, region, *, attempts=8, sleep=time.sleep,
                    execute=subprocess.run):
    """Retry only transient SSM TargetNotConnected; preserve interactive input."""
    cmd = ["aws", "ssm", "start-session", "--target", instance,
           "--region", region, "--no-cli-pager"]
    for attempt in range(attempts):
        completed = execute(cmd, text=True, stderr=subprocess.PIPE, check=False)
        if completed.returncode == 0:
            return
        error = completed.stderr or ""
        if "TargetNotConnected" not in error or attempt + 1 == attempts:
            raise RuntimeError("SSM interactive session failed: " + error.strip())
        sleep(8)


def wait_cpu_https(stack, instance, *, attempts=30, sleep=time.sleep):
    """The approved CPU already has AWCP; wait for trusted DCV after EC2 start."""
    for attempt in range(attempts):
        try:
            return trusted_url(stack, instance)
        except (OSError, ValueError):
            if attempt + 1 == attempts:
                raise
            sleep(10)


def command(args):
    # Issue #43: exact-scope EIP transfer/return requires --execute and the
    # existing strict RES/TLS/PR65 guard preflights. No GPU creation here.
    if args.action in ("hostname-acm-prepare", "hostname-acm-revoke"):
        from runtime import issue43_gpu_acm_setup as acm
        if args.region != DEFAULT_REGION or not args.execute:
            raise ValueError("GPU ACM setup/revoke requires us-east-1 and explicit --execute")
        if args.action == "hostname-acm-prepare":
            print(json.dumps(acm.prepare_gpu(aws), indent=2))
        else:
            print(json.dumps(acm.revoke_gpu(aws, args.gpu_instance_id), indent=2))
        return
    if args.action in ("hostname-assign", "hostname-release"):
        from runtime import independent_eip
        if args.region != DEFAULT_REGION or not args.execute:
            raise ValueError("Independent EIP operation requires us-east-1 and --execute")
        result = (independent_eip.release(aws) if args.action == "hostname-release"
                  else independent_eip.assign(aws, args.stack, verified_dcv_hostname))
        print(json.dumps(result, indent=2))
        return
    if args.action in ("hostname-plan", "hostname-iam", "hostname-handoff",
                       "hostname-return-plan", "hostname-return"):
        from runtime import issue43_hostname_handoff as handoff
        if args.region != handoff.REGION:
            raise ValueError("Issue #43 hostname handoff supports only us-east-1")
        if args.action == "hostname-iam":
            print(json.dumps(handoff.review_gpu_iam(args.gpu_instance_id), indent=2))
            return
        if args.action == "hostname-plan":
            print(json.dumps(handoff.gpu_preview(aws), indent=2))
            return
        if args.action == "hostname-return-plan":
            print(json.dumps(handoff.cpu_return_preview(aws, args.gpu_instance_id), indent=2))
            return
        if not args.execute:
            raise ValueError("Live hostname handoff requires a separately approved --execute")
        if args.action == "hostname-handoff":
            print(json.dumps(handoff.handoff_gpu(aws, verified_dcv_hostname, allow_live=True), indent=2))
        else:
            print(json.dumps(handoff.restore_cpu(aws, args.gpu_instance_id, allow_live=True), indent=2))
        return
    if args.action in ("publisher-create", "publisher-status", "publisher-start", "publisher-stop", "publisher-run", "publisher-result"):
        from runtime import publisher
        print(json.dumps(publisher.command(args, aws), indent=2))
        return
    if args.action == "create":
        if not re.fullmatch(r"[a-zA-Z][a-zA-Z0-9-]{0,127}", args.stack):
            raise ValueError("Invalid CloudFormation stack name")
        if not re.fullmatch(r"v[0-9]+\.[0-9]+\.[0-9]+", args.version):
            raise ValueError("Release must be a version such as v0.1.3")
        ipaddress.IPv4Network(args.client_cidr, strict=False)
        gpu = args.profile == "gpu"
        if gpu:
            if args.region != "us-east-1" or args.image_id != GPU_AMI or args.instance_type != "g4dn.xlarge":
                raise ValueError("GPU profile requires us-east-1, validated AMI and g4dn.xlarge")
            if args.version != "v0.1.3" or args.bucket != DEFAULT_BUCKET:
                raise ValueError("GPU profile requires the pinned release; --version is CPU-only")
        if not gpu and args.instance_type not in ("m7i.large", "m7i.xlarge"):
            raise ValueError("CPU profile only supports m7i.large or m7i.xlarge")
        if not gpu and args.allow_public_dcv:
            raise ValueError("--allow-public-dcv is only for the scoped GPU acceptance test")
        if gpu and not args.dry_run:
            if not args.cleanup_token:
                raise ValueError("GPU live create requires a scoped --cleanup-token")
            if not GPU_ACCEPTANCE_LIVE_ENABLED:
                raise ValueError("Scoped GPU acceptance blocked: Task 3 guard activation pending")
            if (args.stack, args.cleanup_token) != (GPU_ACCEPTANCE_STACK, GPU_ACCEPTANCE_TOKEN):
                raise ValueError("GPU launch refused: only the approved acceptance stack/token are permitted")
            cidr = ipaddress.IPv4Network(args.client_cidr, strict=False)
            if cidr == ipaddress.IPv4Network("0.0.0.0/0"):
                if not args.allow_public_dcv:
                    raise ValueError("Public DCV 8443 requires explicit --allow-public-dcv")
            elif cidr.prefixlen != 32 or not cidr.network_address.is_global:
                raise ValueError("Restricted GPU acceptance requires a real public client /32 CIDR")
            elif args.allow_public_dcv:
                raise ValueError("--allow-public-dcv requires --client-cidr 0.0.0.0/0")
            if (args.vpc_id != DEFAULT_VPC or args.subnet_id != DEFAULT_SUBNET or
                    args.instance_profile != DEFAULT_PROFILE or args.disk_size != 100):
                raise ValueError("GPU acceptance must use the reviewed VPC, subnet, profile and 100 GiB disk")
        if (args.rez_version_id or args.rez_sha256) and args.vpc_id != DEFAULT_VPC:
            raise ValueError("Software EFS client SG requires the reviewed VPC")
        prefix = GPU_PREFIX if gpu else f"releases/{args.version}/res-2026.06"
        with tempfile.TemporaryDirectory(prefix="studio-cli-") as temp:
            template = Path(temp) / "workstation.json"
            render = [sys.executable, str(ROOT / "runtime/render_workstation.py"),
                      "--bucket", args.bucket, "--prefix", prefix, "--user", "workstation",
                      "--output", str(template)]
            if args.rez_version_id or args.rez_sha256:
                render.extend(["--rez-version-id", args.rez_version_id or "", "--rez-sha256", args.rez_sha256 or ""])
            if gpu:
                render.extend(["--profile", "gpu", "--lifecycle", "persistent"])
                if args.cleanup_token:
                    if not re.fullmatch(r"studio-gpu-cli-[a-z0-9-]{1,25}", args.stack) or not re.fullmatch(r"[a-z0-9-]{12,64}", args.cleanup_token):
                        raise ValueError("Cleanup scope requires studio-gpu-cli-* stack and safe token")
                    if args.client_cidr == "0.0.0.0/0" and not args.allow_public_dcv:
                        raise ValueError("Public DCV 8443 requires explicit --allow-public-dcv")
                    if args.client_cidr != "0.0.0.0/0" and args.allow_public_dcv:
                        raise ValueError("--allow-public-dcv requires --client-cidr 0.0.0.0/0")
                    render.extend(["--cleanup-token", args.cleanup_token, "--test-stack", args.stack])
            print("Rendering:", " ".join(render), flush=True)
            run(render, capture=False)
            validate = ["aws", "cloudformation", "validate-template", "--template-body",
                        f"file://{template}", "--region", args.region]
            run(validate)
            parameters = [
                f"ImageId={args.image_id}", f"InstanceType={args.instance_type}",
                f"VpcId={args.vpc_id}", f"SubnetId={args.subnet_id}",
                f"ClientCidr={args.client_cidr}", f"InstanceProfileName={args.instance_profile}",
                f"DiskSizeGiB={args.disk_size}",
            ]
            deploy = ["aws", "cloudformation", "deploy", "--stack-name", args.stack,
                      "--template-file", str(template), "--region", args.region,
                      "--parameter-overrides", *parameters, "--no-fail-on-empty-changeset"]
            if gpu and args.cleanup_token:
                deploy.extend(["--tags", "StudioCleanupToken=" + args.cleanup_token, "StudioStack=" + args.stack])
            print("Stack:", args.stack, "Release:", args.version, "DCV ingress:", args.client_cidr, flush=True)
            if args.client_cidr == "0.0.0.0/0":
                print("WARNING: DCV login will be publicly accessible on TCP 8443.", flush=True)
            if args.dry_run:
                print("DRY RUN: validated template; deployment not executed.")
                return
            # Never use deploy to silently modify an existing workstation.
            try:
                aws(args.region, "cloudformation", "describe-stacks", "--stack-name", args.stack)
            except subprocess.CalledProcessError as exc:
                # An expected first-create miss is not an error. Do not treat
                # other failures (including AccessDenied) as proof of absence.
                if "does not exist" not in (exc.stderr or ""):
                    raise
            else:
                raise ValueError("Stack already exists; create requires a new stack name")
            if gpu:
                gpu_release_preflight(args)
                shared_gpu_source_ready()  # Do not create billable GPU against a running/foreign CPU.
                from runtime import pr65_gpu_create
                # The CPU deploy/wait path is too slow to arm a GPU expiration
                # schedule; use asynchronous CreateStack with bounded discovery.
                # Runtime IAM and schedule-group verification still fail closed.
                outcome = pr65_gpu_create.create_guarded(
                    aws, args.region, args.stack, args.cleanup_token, template, parameters)
                print(json.dumps(outcome, indent=2), flush=True)
                if not outcome.get("guard", {}).get("guard_armed"):
                    raise RuntimeError("PR #65 expiration safeguard not confirmed; no trusted handoff")
                print("GPU guard armed; waiting for RES/DCV and trusted HTTPS...", flush=True)
                url = complete_gpu_setup(args.stack, args.region, outcome["guard"]["instance_id"])
                print("TRUSTED DCV:", url)
                return
            run(deploy, capture=False)
            print(json.dumps(describe(args.stack, args.region), indent=2))
        return
    if args.action == "check-ready":
        gpu_ready(args.stack, args.region)
        return
    if args.action == "status":
        state = describe(args.stack, args.region)
        if args.hostname:
            if state["state"] != "running":
                raise ValueError("Cannot verify trusted TLS while workstation is stopped")
            if args.stack in (DEFAULT_STACK, GPU_ACCEPTANCE_STACK):
                state["trusted_dcv_url"] = trusted_url(args.stack, state["instance"])
            else:
                state["trusted_dcv_url"] = verified_dcv_hostname(args.hostname, state["public_ip"])
        print(json.dumps(state, indent=2))
        return
    if args.action == "destroy":
        if not args.delete_stack:
            raise ValueError("Refusing destruction: add --delete-stack")
        from runtime import issue43_destroy
        if args.stack == GPU_ACCEPTANCE_STACK:
            result = issue43_destroy.destroy_gpu(aws, run, args.stack, args.region)
        else:
            result = issue43_destroy.destroy_cpu(aws, run, args.stack, args.region)
        print(json.dumps(result, indent=2))
        return
    state = describe(args.stack, args.region)
    iid = state["instance"]
    if args.action == "connect":
        if state["state"] != "running":
            raise ValueError("Instance is not running")
        if args.ssm:
            interactive_ssm(iid, args.region)
        elif args.stack in (DEFAULT_STACK, GPU_ACCEPTANCE_STACK):
            print(trusted_url(args.stack, iid))
            print("Trusted HTTPS verified; log in with the local workstation account.")
        elif args.hostname:
            print(verified_dcv_hostname(args.hostname, state["public_ip"]))
            print("DNS and certificate validated; log in with the local workstation account.")
        else:
            print(state["dcv_url"] or "No public IPv4 address. Use --ssm for administrative access.")
            print("Use the local workstation account; browser certificate trust is not yet configured.")
        return
    if args.action in ("start", "stop"):
        desired = "stopped" if args.action == "start" else "running"
        if state["state"] != desired:
            raise ValueError(f"Expected {desired} before {args.action}, got {state['state']}")
        if args.action == "start" and args.stack == DEFAULT_STACK:
            from runtime import independent_eip
            address = independent_eip._address(aws)
            if address.get("InstanceId") not in (None, iid):
                raise ValueError("Shared EIP belongs to another workstation")
        if args.action == "start" and args.stack == GPU_ACCEPTANCE_STACK:
            shared_gpu_source_ready()  # CPU/EIP must be safe before billing.
            gpu_restart_preflight(args.stack, iid, args.region)  # Guard + deadline before EC2.
        if args.action == "stop" and args.stack == GPU_ACCEPTANCE_STACK:
            from runtime import issue43_hostname_handoff as handoff
            address = handoff._eip(aws)
            owner = address.get("InstanceId")
            tagged = handoff._tags(address).get("StudioDcvInstanceId")
            if not ((owner == iid and tagged == iid) or
                    (owner == handoff.CPU_ID and tagged == handoff.CPU_ID) or
                    (owner is None and tagged == iid)):
                raise ValueError("Unexpected EIP ownership; refusing GPU stop orchestration")
        run(["aws", "ec2", f"{args.action}-instances", "--instance-ids", iid,
             "--region", args.region], capture=False)
        waiter = "instance-status-ok" if args.action == "start" else "instance-stopped"
        run(["aws", "ec2", "wait", waiter, "--instance-ids", iid, "--region", args.region], capture=False)
        if args.stack == GPU_ACCEPTANCE_STACK:
            if args.action == "start":
                print("GPU restarted; checking RES/ACM/EIP before advertising HTTPS...", flush=True)
                print("TRUSTED DCV:", complete_gpu_setup(args.stack, args.region, iid))
            else:
                print(json.dumps(complete_gpu_stop(args.stack, iid), indent=2))
        elif args.stack == DEFAULT_STACK and args.action == "start":
            from runtime import independent_eip
            if independent_eip._address(aws).get("InstanceId") == iid:
                print("TRUSTED DCV:", wait_cpu_https(args.stack, iid))
        print(json.dumps(describe(args.stack, args.region), indent=2))
        return

def main(argv=None):
    p = argparse.ArgumentParser(prog="studio workstation",
                                description="Validated CPU RES workstation lifecycle wrapper")
    sub = p.add_subparsers(dest="action", required=True)
    for action in ("create", "status", "start", "stop", "connect", "destroy", "check-ready",
                   "hostname-assign", "hostname-release",
                   "hostname-plan", "hostname-iam", "hostname-handoff",
                   "hostname-return-plan", "hostname-return",
                   "hostname-acm-prepare", "hostname-acm-revoke"):
        s = sub.add_parser(action)
        s.add_argument("--stack", default=DEFAULT_STACK if action != "create" else None,
                       required=action == "create")
        s.add_argument("--region", default=DEFAULT_REGION)
        if action in ("hostname-iam", "hostname-return-plan", "hostname-return", "hostname-acm-revoke"):
            s.add_argument("--gpu-instance-id", required=True,
                           help="Exact supervised GPU EC2 ID, no wildcard discovery")
        if action in ("hostname-assign", "hostname-release", "hostname-handoff", "hostname-return", "hostname-acm-prepare", "hostname-acm-revoke"):
            s.add_argument("--execute", action="store_true",
                           help="Explicitly authorize guarded hostname/ACM AWS writes")
        if action == "create":
            s.add_argument("--dry-run", action="store_true",
                           help="Render and validate without launching AWS resources")
            s.add_argument("--profile", choices=["cpu", "gpu"], default="cpu")
            s.add_argument("--version", default="v0.1.3")
            s.add_argument("--rez-version-id", help="Approved immutable S3 Rez bundle version")
            s.add_argument("--rez-sha256", help="SHA-256 of approved Rez bundle")
            s.add_argument("--bucket", default=DEFAULT_BUCKET)
            s.add_argument("--image-id", default=None)
            s.add_argument("--vpc-id", default=DEFAULT_VPC)
            s.add_argument("--subnet-id", default=DEFAULT_SUBNET)
            s.add_argument("--instance-profile", default=DEFAULT_PROFILE)
            s.add_argument("--instance-type", choices=["m7i.large", "m7i.xlarge", "g4dn.xlarge"], default=None)
            s.add_argument("--disk-size", type=int, default=100)
            s.add_argument("--client-cidr", default="0.0.0.0/0")
            s.add_argument("--allow-public-dcv", action="store_true",
                           help="Explicit supervised GPU acceptance: permit TCP 8443 from any IPv4 address")
            s.add_argument("--cleanup-token", default=None, help="Unique scope for independently guarded GPU acceptance test")
        if action in ("connect", "status"):
            s.add_argument("--hostname", choices=[TRUSTED_DCV_HOSTNAME],
                           help="Opt-in trusted hostname; verify DNS, TLS trust, and instance IPv4")
        if action == "connect":
            s.add_argument("--ssm", action="store_true", help="Open administrative SSM session instead of printing DCV URL")
        if action == "destroy":
            s.add_argument("--delete-stack", action="store_true")
    for action in ("publisher-create", "publisher-status", "publisher-start", "publisher-stop", "publisher-run", "publisher-result"):
        s = sub.add_parser(action)
        s.add_argument("--region", default=DEFAULT_REGION)
        s.add_argument("--instance-id", help="Exact publisher EC2 instance ID")
        if action == "publisher-create":
            s.add_argument("--subnet-id", default=DEFAULT_SUBNET)
            s.add_argument("--security-group-id", required=True)
            s.add_argument("--image-id", default=DEFAULT_AMI)
            s.add_argument("--instance-type", default="m7i.large")
        if action == "publisher-result":
            s.add_argument("--command-id", required=True)
        if action == "publisher-run":
            s.add_argument("--operation", choices=["check", "build", "promote-test", "promote-prod"], required=True)
            s.add_argument("--recipe-repo", help="Approved vision-kwest GitHub recipe repository")
            s.add_argument("--recipe-ref", help="Pinned 40-character recipe commit SHA")
            s.add_argument("--recipe-path", help="Relative build script path")
            s.add_argument("--name", default="usd")
            s.add_argument("--version", default="26.05")
    a = p.parse_args(argv)
    if a.action == "create":
        if a.image_id is None:
            a.image_id = GPU_AMI if a.profile == "gpu" else DEFAULT_AMI
        if a.instance_type is None:
            a.instance_type = "g4dn.xlarge" if a.profile == "gpu" else "m7i.large"
    try:
        if a.action == "connect" and a.ssm and a.hostname:
            raise ValueError("--hostname cannot be used with --ssm")
        command(a)
    except (ValueError, KeyError, RuntimeError, subprocess.CalledProcessError) as exc:
        if isinstance(exc, subprocess.CalledProcessError) and exc.stderr:
            p.exit(1, f"ERROR: {(exc.stderr or '').strip()}\n")
        p.exit(1, f"ERROR: {exc}\n")

if __name__ == "__main__":
    main()
