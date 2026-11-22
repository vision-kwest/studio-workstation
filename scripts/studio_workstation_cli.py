#!/usr/bin/env python3
"""Named AWS CLI/CloudFormation lifecycle for CPU, GPU and publisher workstations.

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
import uuid
from pathlib import Path
from runtime import cli_progress as progress

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_BUCKET = "studio-workstation-releases-401233741942-us-east-1"
DEFAULT_AMI = "ami-0d6dd9e8265d48906"
DEFAULT_VPC = "vpc-01bbe5d01838f70b2"
DEFAULT_SUBNET = "subnet-0986937ce71fb97cb"
DEFAULT_PROFILE = "StudioWorkstationSSMRole"
DEFAULT_REGION = "us-east-1"
# One approved Rez 3.4.0 bootstrap bundle; application packages remain on EFS.
DEFAULT_REZ_VERSION_ID = "LBsqCMx_GF2pullxiHQKl8w9U0Yf08dX"
DEFAULT_REZ_SHA256 = "de9d5159369fd57e3109ce047a290419d07f5dceba71dd18100c94be1f16cea2"
GPU_AMI = DEFAULT_AMI  # Canonical 24.04: let RES install NVIDIA after runtime dependencies
GPU_AMI_OWNER = "099720109477"  # Canonical
GPU_PREFIX = "releases/issue100-res-native-20261115/res-2026.06"
GPU_APP_PREFIX = "releases/task7-pr61-postreboot-a8ec326/res-2026.06"
GPU_VERSIONS = {
    "installation-scripts.tar.gz": "sgj5wZfqMTRUImWDDbxR08emK755aU7A",
    "virtual-desktop.tar.gz": "nzyYrLhYAQ.OTFNpFE_xSHPaK06T1V53",
}
GPU_KEYS = {
    "installation-scripts.tar.gz": GPU_PREFIX + "/installation-scripts.tar.gz",
    "virtual-desktop.tar.gz": GPU_APP_PREFIX + "/virtual-desktop.tar.gz",
}

TRUSTED_DCV_HOSTNAME = "dcv.kwesidavis.com"  # Only its verified owner gets the trusted URL.

def gpu_duration_minutes(value):
    """Accept positive whole minutes, hours, or days without a product-duration cap."""
    match = re.fullmatch(r"([1-9][0-9]*)([mhd])", value)
    if not match:
        raise argparse.ArgumentTypeError("Expected a positive duration such as 150m, 8h, or 2d")
    return int(match[1]) * {"m": 1, "h": 60, "d": 1440}[match[2]]


def gpu_extension_minutes(value):
    if not re.fullmatch(r"[1-9][0-9]*", value):
        raise argparse.ArgumentTypeError("Expected positive whole additional minutes")
    return int(value)


def expiration_options(args):
    from runtime.workstation_inventory import PROFILES
    minutes = PROFILES[args.profile]["expires_minutes"] if args.expires_in is None else args.expires_in
    if minutes is None:
        return None
    from runtime.pr65_one_shot_guard import deadline
    deadline(dt.datetime.now(dt.timezone.utc), minutes)
    return minutes


def run(args, *, capture=True):
    # AWS CLI v2 may open a full-screen pager in CloudShell. Never block
    # a guarded deployment while rendering read-only JSON output.
    waiter = len(args) > 3 and args[0] == "aws" and args[2] == "wait"
    if waiter:
        progress.stage(f"Waiting for {args[1]} {args[3]}")
    if args and args[0] == "aws":
        args = [*args, "--no-cli-pager"]
    if capture:
        output = subprocess.check_output(args, text=True, stderr=subprocess.PIPE)
    else:
        subprocess.check_call(args)
        output = ""
    if waiter:
        progress.stage(f"AWS waiter confirmed: {args[1]} {args[3]}")
    return output

def aws(region, *args):
    output = run(["aws", *args, "--region", region, "--output", "json"])
    # Several successful AWS writes (IAM policy, EC2 tags/EIP detach) emit no JSON.
    # Empty stdout is success only because run() already raises on nonzero exit.
    result = json.loads(output) if output.strip() else {}
    progress.observe(args[0], result)
    return result

def workstation_resource(stack, region):
    detail = aws(region, "cloudformation", "describe-stack-resource",
                 "--stack-name", stack, "--logical-resource-id", "Workstation")["StackResourceDetail"]
    if (stack not in (detail.get("StackName"), detail.get("StackId")) or
            detail.get("LogicalResourceId") != "Workstation" or
            detail.get("ResourceType") != "AWS::EC2::Instance" or
            not detail.get("StackId") or
            not re.fullmatch(r"i-[0-9a-f]{8,17}", detail.get("PhysicalResourceId", ""))):
        raise ValueError("CloudFormation workstation identity mismatch")
    return detail


def instance_id(stack, region):
    return workstation_resource(stack, region)["PhysicalResourceId"]


def describe(stack, region, *, with_expiration=False):
    from runtime.workstation_inventory import context
    from runtime import pr65_one_shot_guard as guard
    selected = context(aws, region, stack)
    result = {k: v for k, v in selected.items() if k not in ("vm", "resources", "token")}
    if with_expiration and guard.has_expiration(selected) and selected["state"] in ("running", "stopped"):
        expiry = guard.status(aws, selected)
        result["expiration"] = {k: expiry[k] for k in ("stop_at", "duration_minutes", "remaining_seconds", "status", "warning") if k in expiry}
    return result

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
    if len(images) != 1 or images[0]["State"] != "available" or images[0]["OwnerId"] != GPU_AMI_OWNER:
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


def gpu_ready(stack, region, *, gpu=True):
    """Read-only guest readiness via bounded SSM diagnostics; no secrets."""
    import time
    iid = instance_id(stack, region)
    commands = [
        "set -e",
        "echo 'Checking RES desktop readiness marker'",
        "test -f /root/bootstrap/semaphore/linux_vdi_config_host_ready.lock",
        "echo 'Checking GDM and DCV services'",
        "systemctl is-active gdm3 dcvserver",
        "echo 'Checking DCV console session'",
        "dcv describe-session console",
    ]
    if gpu:
        commands += [
        "echo 'Checking NVIDIA T4'",
        "nvidia-smi -L | grep -q 'Tesla T4'",
        "echo 'Checking Xorg'",
        "pgrep -x Xorg",
        "echo 'Checking NVIDIA OpenGL'",
        "DISPLAY=:0 XAUTHORITY=/run/user/119/gdm/Xauthority __GLX_VENDOR_LIBRARY_NAME=nvidia glxinfo -B | grep -q 'OpenGL renderer string: Tesla T4'",
    ]
    response = aws(region, "ssm", "send-command", "--instance-ids", iid,
                   "--document-name", "AWS-RunShellScript",
                   "--parameters", json.dumps({"commands": commands, "executionTimeout": ["45"]}),
                   "--timeout-seconds", "60", "--comment", "Studio desktop readiness: no credentials")
    cid = response["Command"]["CommandId"]
    for _ in range(18):
        time.sleep(5)
        try:
            result = aws(region, "ssm", "get-command-invocation",
                         "--command-id", cid, "--instance-id", iid)
        except subprocess.CalledProcessError as exc:
            if "InvocationDoesNotExist" not in (exc.stderr or ""):
                raise
            continue
        if result["Status"] == "Success":
            progress.checkpoint(None)
            progress.stage("Ready: RES, GDM and DCV" + (", Xorg and Tesla T4 OpenGL" if gpu else ""))
            return
        if result["Status"] in ("Failed", "Cancelled", "TimedOut"):
            markers = {line[6:-1] for line in commands if line.startswith("echo 'Checking ")}
            label = next((line[9:] for line in reversed(result.get("StandardOutputContent", "").splitlines())
                          if line in markers), "not reported")
            progress.checkpoint(f"{result['Status']}: {label}")
            raise ValueError(f"GPU readiness failed: instance={iid} SSM command={cid}; {result['Status']}: {label}")
    progress.checkpoint("SSM readiness polling timed out")
    raise ValueError("GPU readiness timed out; inspect SSM and RES bootstrap logs")


def wait_gpu_bootstrap(stack, region, *, attempts=96, sleep=time.sleep, gpu=True):
    """Bound retries across native RES installation and reboots; keep fail-closed readiness.\n\n    The native workstation expiry and exact-instance fallback remain armed throughout.\n    """
    for attempt in range(attempts):
        progress.stage(f"Checking RES and DCV readiness via SSM (attempt {attempt + 1}/{attempts})")
        try:
            gpu_ready(stack, region, gpu=gpu)
            return
        except subprocess.CalledProcessError as exc:
            # SSM can disconnect while RES reboots. Never retry an IAM denial.
            failure = (exc.stderr or "") + " " + str(exc)
            if not any(message in failure for message in
                       ("TargetNotConnected", "InvalidInstanceId",
                        "InvalidInstanceID.NotFound", "InvocationDoesNotExist")):
                raise
            progress.checkpoint("SSM unavailable")
        except ValueError as exc:
            if not any(message in str(exc) for message in
                       ("GPU readiness failed", "GPU readiness timed out")):
                raise
            failure = str(exc)
        if attempt + 1 < attempts:
            progress.stage(f"Waiting to retry readiness after attempt {attempt + 1}/{attempts} (15s delay)")
            sleep(15)
    raise RuntimeError("RES/DCV bootstrap did not become ready; "
                       "native workstation expiry remains armed; last check: " + failure)


def verify_software_bootstrap_source(source):
    """Fail before billing if a stale checkout uses the removed bundled EFS .deb."""
    if ("command -v mount.efs" not in source or
            "amazon-efs-utils_*.deb" in source):
        raise RuntimeError(
            "Outdated Studio software bootstrap; run git pull --ff-only "
            "and retry before launching billable EC2")


def install_efs_client(instance, region, *, manager=aws, sleep=time.sleep):
    """Provision official EFS mount helper via AWS SSM Distributor.

    The pinned Rez bundle contains no amazon-efs-utils .deb. Installation is
    performed by the Studio CLI, never by unverified guest downloads.
    """
    progress.stage("Installing official EFS mount helper via SSM")
    command = None
    for attempt in range(24):
        try:
            response = manager(region, "ssm", "send-command",
                               "--instance-ids", instance,
                               "--document-name", "AWS-ConfigureAWSPackage",
                               "--parameters", json.dumps({
                                   "action": ["Install"], "name": ["AmazonEFSUtils"],
                                   "installationType": ["Uninstall and reinstall"]}),
                               "--timeout-seconds", "180")
            command = response["Command"]["CommandId"]
            break
        except subprocess.CalledProcessError as exc:
            message = (exc.stderr or "") + str(exc)
            if not any(x in message for x in ("InvalidInstanceId", "TargetNotConnected")):
                raise
            if attempt == 23:
                raise RuntimeError("SSM EFS helper installation unavailable") from exc
            sleep(10)
    for attempt in range(60):
        try:
            result = manager(region, "ssm", "get-command-invocation",
                             "--command-id", command, "--instance-id", instance)
        except subprocess.CalledProcessError as exc:
            if "InvocationDoesNotExist" not in ((exc.stderr or "") + str(exc)):
                raise
            result = {}
        if result.get("Status") == "Success":
            return
        if result.get("Status") in ("Failed", "TimedOut", "Cancelled"):
            raise RuntimeError("Official EFS helper installation failed: " +
                               result.get("Status", "unknown"))
        sleep(5)
    raise RuntimeError("Timed out waiting for official EFS helper installation")


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


def wait_dcv(stack, region, *, attempts=24, sleep=time.sleep):
    """Fresh IP/owner on every retry. No cached CloudFormation public addresses."""
    progress.stage("Checking DCV HTTPS endpoint")
    from runtime.dcv_endpoint import resolve
    for attempt in range(attempts):
        state = describe(stack, region)
        try:
            return resolve(state, region, aws, verified_dcv_hostname)
        except ValueError as exc:
            if not str(exc).startswith(("No running public", "DCV HTTPS", "Trusted DCV TLS")):
                raise
            if attempt + 1 == attempts:
                raise
            sleep(10)


def ready_desktop(stack, region, *, gpu):
    """Keep a usable desktop when optional hostname setup cannot finish."""
    from runtime import independent_eip
    wait_gpu_bootstrap(stack, region, gpu=gpu)
    wait_dcv(stack, region)
    try:
        claimed = independent_eip.assign(aws, stack, verified_dcv_hostname, only_if_unassigned=True)
        progress.stage("Shared hostname assigned: " + TRUSTED_DCV_HOSTNAME if claimed else
                       "Shared hostname already assigned; preserving its current owner")
    except (ValueError, RuntimeError, subprocess.CalledProcessError) as exc:
        detail = (exc.stderr or f"AWS command exited {exc.returncode}") if isinstance(exc, subprocess.CalledProcessError) else str(exc)
        print("WARNING: automatic hostname setup incomplete: " + detail.strip() +
              f". For intentional assignment or transfer: python3 studio workstation hostname assign {stack}",
              file=sys.stderr, flush=True)
    endpoint = wait_dcv(stack, region)  # Recheck actual ownership, TLS and reachability after any recovery.
    progress.stage("DCV ready: " + endpoint["url"])
    if not endpoint["trusted"]:
        progress.stage("IP certificate warning expected. To assign or transfer the trusted hostname: "
                       f"python3 studio workstation hostname assign {stack}")
    return endpoint


def command(args):
    from runtime.workstation_inventory import PROFILES, context, list_workstations
    from runtime import pr65_one_shot_guard as guard
    from runtime import independent_eip
    if args.action in ("profiles", "profile"):
        chosen = PROFILES if args.action == "profiles" else {args.profile_name: PROFILES[args.profile_name]}
        print(json.dumps({"profiles": chosen, "region": DEFAULT_REGION,
                          "storage": "Local disks survive stop/start; destroyed with the workstation",
                          "costs": "On-demand EC2 while running; EBS/public IPv4 while allocated; no pricing engine"}, indent=2))
        return
    if args.region != DEFAULT_REGION:
        raise ValueError("Only validated us-east-1 is supported")
    if args.action == "list":
        print(json.dumps(list_workstations(aws, args.region), indent=2))
        return
    if args.action == "hostname":
        if args.hostname_action == "status":
            result = independent_eip.status(aws)
        elif args.hostname_action == "assign":
            result = independent_eip.assign(aws, args.stack, verified_dcv_hostname)
        else:
            result = independent_eip.release(aws, verified_dcv_hostname)
        print(json.dumps(result, indent=2))
        return
    if args.action == "extend-expiration":
        progress.stage("Checking and extending expiration safeguards")
        from runtime import issue68_expiration_extension as extension
        print(json.dumps(extension.extend(aws, args.stack, args.region, args.additional_minutes), indent=2))
        return
    if args.action == "destroy":
        if not args.delete_stack:
            raise ValueError("Refusing destruction: add --delete-stack")
        from runtime import issue43_destroy
        print(json.dumps(issue43_destroy.destroy(aws, run, args.stack, args.region), indent=2))
        return
    if args.action == "create":
        if not re.fullmatch(r"[a-zA-Z][a-zA-Z0-9-]{0,127}", args.stack):
            raise ValueError("Invalid CloudFormation stack name")
        if not re.fullmatch(r"v[0-9]+\.[0-9]+\.[0-9]+", args.version):
            raise ValueError("Release must be a version such as v0.1.3")
        args.client_cidr = str(ipaddress.IPv4Network(args.client_cidr, strict=False))
        gpu = args.profile == "gpu"
        expiration = expiration_options(args)
        if gpu:
            if args.region != "us-east-1" or args.image_id != GPU_AMI:
                raise ValueError("GPU profile requires us-east-1 and the validated AMI")
            if args.version != "v0.1.3" or args.bucket != DEFAULT_BUCKET:
                raise ValueError("GPU profile requires the pinned release; --version is CPU-only")
        if args.instance_type not in PROFILES[args.profile]["instance_types"]:
            raise ValueError("Instance type is not supported by the selected profile")
        if args.instance_profile != ("StudioEfsCiPublisher" if args.profile == "publisher" else DEFAULT_PROFILE):
            raise ValueError("Profile IAM identity must match the supported contract")
        if (args.vpc_id, args.subnet_id) != (DEFAULT_VPC, DEFAULT_SUBNET):
            raise ValueError("Use the validated public workstation VPC/subnet")
        if not (30 if args.profile == "publisher" else 60) <= args.disk_size <= 2048:
            raise ValueError("Disk size outside supported range")
        if args.profile == "publisher" and (args.rez_version_id or args.rez_sha256):
            raise ValueError("Publisher uses its existing publishing bootstrap, not desktop Rez pins")
        if args.profile == "publisher" and not args.security_group_id:
            raise ValueError("Publisher requires --security-group-id for its existing EFS client group")
        explicit_pins = args.rez_version_id is not None or args.rez_sha256 is not None
        if explicit_pins and not (args.rez_version_id and args.rez_sha256):
            raise ValueError("Rez override requires both --rez-version-id and --rez-sha256")
        if args.no_studio_software and (explicit_pins or args.profile == "publisher"):
            raise ValueError("--no-studio-software requires a desktop profile without Rez overrides")
        if args.profile != "publisher" and not (args.no_studio_software or explicit_pins):
            args.rez_version_id, args.rez_sha256 = DEFAULT_REZ_VERSION_ID, DEFAULT_REZ_SHA256
        if args.profile != "publisher":
            verify_software_bootstrap_source(
                (ROOT / "scripts/setup_workstation_software.sh").read_text())
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
            progress.stage("Rendering and validating workstation template")
            if args.profile == "publisher":
                from runtime import publisher
                template.write_text(json.dumps(publisher.template(args)))
            else:
                run(render)  # The renderer's informational stdout is not the CLI result.
            validate = ["aws", "cloudformation", "validate-template", "--template-body",
                        f"file://{template}", "--region", args.region]
            run(validate)
            parameters = [
                f"ImageId={args.image_id}", f"InstanceType={args.instance_type}",
                f"VpcId={args.vpc_id}", f"SubnetId={args.subnet_id}",
                f"ClientCidr={args.client_cidr}", f"InstanceProfileName={args.instance_profile}",
                f"DiskSizeGiB={args.disk_size}",
            ]
            if args.profile != "publisher":
                progress.stage("Shared /studio setup requested after DCV; Rez skipped" if args.no_studio_software else
                               "Shared /studio and Rez setup requested after DCV")
            if args.profile != "publisher" and args.client_cidr == "0.0.0.0/0":
                print("WARNING: DCV login will be publicly accessible on TCP 8443.", file=sys.stderr, flush=True)
            if args.dry_run:
                print("DRY RUN: validated template; deployment not executed.")
                return
            # Never use deploy to silently modify an existing workstation.
            try:
                aws(args.region, "cloudformation", "describe-stacks", "--stack-name", args.stack)
            except subprocess.CalledProcessError as exc:
                # An expected first-create miss is not an error. Do not treat
                # other failures (including AccessDenied) as proof of absence.
                if "ValidationError" not in (exc.stderr or "") or "does not exist" not in (exc.stderr or ""):
                    raise
            else:
                raise ValueError("Stack already exists; create requires a new stack name")
            account = aws(args.region, "sts", "get-caller-identity")["Account"]
            if account != "401233741942":
                raise ValueError("Unexpected AWS account")
            subnet = aws(args.region, "ec2", "describe-subnets", "--subnet-ids", args.subnet_id)["Subnets"]
            if (len(subnet) != 1 or subnet[0].get("MapPublicIpOnLaunch") is not True or
                    subnet[0].get("VpcId") != args.vpc_id):
                raise ValueError("Workstation subnet must assign a public IPv4 in the validated VPC")
            if args.profile == "publisher":
                groups = aws(args.region, "ec2", "describe-security-groups", "--group-ids", args.security_group_id)["SecurityGroups"]
                if len(groups) != 1 or groups[0].get("VpcId") != args.vpc_id:
                    raise ValueError("Publisher security group is not in the workstation VPC")
            if gpu:
                gpu_release_preflight(args)
            if expiration is not None:
                from runtime import pr65_gpu_create
                pr65_gpu_create.create_guarded(
                    aws, args.region, args.stack, template, parameters, minutes=expiration, profile=args.profile)
            else:
                progress.stage("Creating workstation stack")
                cf_params = ["ParameterKey=" + k + ",ParameterValue=" + v for k, v in
                             (item.split("=", 1) for item in parameters)]
                aws(args.region, "cloudformation", "create-stack", "--stack-name", args.stack,
                    "--template-body", "file://" + str(template), "--parameters", *cf_params,
                    "--tags", "Key=StudioManaged,Value=true", "Key=StudioProfile,Value=" + args.profile,
                    "--on-failure", "DELETE")
            aws(args.region, "cloudformation", "wait", "stack-create-complete", "--stack-name", args.stack)
            if args.profile != "publisher":
                try:
                    endpoint = ready_desktop(args.stack, args.region, gpu=gpu)
                except Exception:
                    # Failed desktop creation must not strand a billable, unreachable VM.
                    selected = context(aws, args.region, args.stack)
                    progress.stage("Stopping EC2 after desktop readiness failure")
                    aws(args.region, "ec2", "stop-instances", "--instance-ids", selected["instance"])
                    raise
            if args.profile != "publisher":
                try:
                    install_efs_client(instance_id(args.stack, args.region), args.region)
                except (RuntimeError, subprocess.CalledProcessError) as exc:
                    print("WARNING: desktop is available; Studio software setup incomplete; EFS helper installation failed: " + str(exc), file=sys.stderr)
            result = describe(args.stack, args.region, with_expiration=True)
            if args.profile != "publisher":
                result["dcv_url"] = endpoint["url"]
            print(json.dumps(result, indent=2), flush=True)
        return
    selected = context(aws, args.region, args.stack)
    iid = selected["instance"]
    profile = selected["profile"]
    if args.action == "status":
        result = describe(args.stack, args.region, with_expiration=True)
        print(json.dumps(result, indent=2))
        return
    if args.action in ("run", "result") or (args.action == "check-ready" and profile == "publisher"):
        if profile != "publisher":
            raise ValueError("Package publishing requires the publisher profile")
        from runtime import publisher
        args.instance_id = iid
        args.action = "publisher-" + ("result" if args.action == "result" else "run")
        args.name = getattr(args, "package", "usd")
        args.operation = getattr(args, "operation", "check")
        print(json.dumps(publisher.command(args, aws), indent=2))
        return
    if args.action == "check-ready":
        progress.stage("Checking RES and DCV readiness via SSM")
        gpu_ready(args.stack, args.region, gpu=profile == "gpu")
        print(json.dumps(wait_dcv(args.stack, args.region), indent=2))
        return
    if args.action == "connect":
        if selected["state"] != "running":
            raise ValueError("Instance is not running")
        if args.ssm:
            interactive_ssm(iid, args.region)
        elif profile == "publisher":
            raise ValueError("Publisher has no DCV desktop; use --ssm or run/result")
        else:
            from runtime.dcv_endpoint import resolve
            endpoint = resolve(selected, args.region, aws, verified_dcv_hostname, hostname=args.hostname)
            print(endpoint["url"])
            print("Reachable HTTPS login page; " + ("trusted certificate verified."
                  if endpoint["trusted"] else "IP certificate warning expected; identity is not TLS-verified."))
        return
    if args.action in ("start", "stop"):
        desired = "stopped" if args.action == "start" else "running"
        retry_stop = args.action == "stop" and selected["state"] == "stopped"
        if not retry_stop and selected["state"] != desired:
            raise ValueError(f"Expected {desired} before {args.action}, got {selected['state']}")
        if args.action == "start" and selected["stack_status"] not in ("CREATE_COMPLETE", "UPDATE_COMPLETE"):
            raise ValueError("Cannot restart a workstation during failed or incomplete stack operations")
        if args.action == "start":
            if guard.has_expiration(selected):
                progress.stage("Arming a fresh automatic-stop timer before starting EC2")
                guard.rearm(aws, selected, minutes=args.expires_in)
            elif args.expires_in is not None:
                raise ValueError("Automatic stop is not configured; create the workstation with --expires-in first")
        if not retry_stop:
            progress.stage("Starting EC2" if args.action == "start" else "Stopping EC2")
            aws(args.region, "ec2", args.action + "-instances", "--instance-ids", iid)
            try:
                aws(args.region, "ec2", "wait", "instance-status-ok" if args.action == "start" else "instance-stopped",
                    "--instance-ids", iid)
            except subprocess.CalledProcessError:
                if args.action != "stop":
                    raise
                fresh = context(aws, args.region, selected["stack_id"])
                if fresh["instance"] != iid or fresh["state"] != "stopped":
                    raise
                progress.stage("EC2 stop confirmed by readback; completing cleanup")
        if profile != "publisher":
            if args.action == "stop":
                independent_eip.release_if_owned(aws, selected, verified_dcv_hostname)
            else:
                try:
                    endpoint = ready_desktop(args.stack, args.region, gpu=profile == "gpu")
                except Exception:
                    progress.stage("Stopping EC2 after desktop readiness failure")
                    aws(args.region, "ec2", "stop-instances", "--instance-ids", iid)
                    raise
        result = describe(args.stack, args.region, with_expiration=args.action == "start")
        if args.action == "start" and profile != "publisher":
            result["dcv_url"] = endpoint["url"]
        print(json.dumps(result, indent=2), flush=True)

def main(argv=None):
    from runtime.workstation_inventory import PROFILES
    p = argparse.ArgumentParser(prog="studio workstation", description="Named Studio workstation lifecycle")
    p.add_argument("--quiet", "--no-progress", dest="no_progress", action="store_true", help="Suppress progress on stderr")
    sub = p.add_subparsers(dest="action", required=True)
    for action in ("list", "profiles", "profile", "hostname", "create", "status", "start", "stop", "connect",
                   "destroy", "check-ready", "extend-expiration", "run", "result"):
        s = sub.add_parser(action)
        s.add_argument("--quiet", "--no-progress", dest="no_progress", action="store_true", default=argparse.SUPPRESS)
        s.add_argument("--region", default=DEFAULT_REGION)
        if action == "profile":
            s.add_argument("profile_name", choices=PROFILES)
        if action in ("list", "profiles", "profile"):
            continue
        if action == "hostname":
            s.add_argument("hostname_action", choices=["status", "assign", "release"],
                           help="Inspect ownership, intentionally assign/transfer, or release the hostname")
        if action != "create":
            s.add_argument("name", nargs="?", help="Workstation name shown by list")
        selector = s.add_mutually_exclusive_group()
        selector.add_argument("--stack", help="CloudFormation workstation name")
        selector.add_argument("--name", dest="stack", help="Alias for --stack")
        if action == "create":
            s.add_argument("--dry-run", action="store_true", help="Render and validate; no AWS resources created")
            s.add_argument("--profile", choices=PROFILES, default="cpu")
            s.add_argument("--version", default="v0.1.3")
            s.add_argument("--rez-version-id")
            s.add_argument("--rez-sha256")
            s.add_argument("--no-studio-software", action="store_true",
                           help="Skip optional Rez setup; shared writable /studio remains available")
            s.add_argument("--bucket", default=DEFAULT_BUCKET)
            s.add_argument("--image-id")
            s.add_argument("--vpc-id", default=DEFAULT_VPC)
            s.add_argument("--subnet-id", default=DEFAULT_SUBNET)
            s.add_argument("--instance-profile")
            s.add_argument("--instance-type", choices=["m7i.large", "m7i.xlarge", "g4dn.xlarge", "g4dn.2xlarge"])
            s.add_argument("--disk-size", type=int)
            s.add_argument("--security-group-id", help="Publisher's existing EFS client security group")
            s.add_argument("--client-cidr", default="0.0.0.0/0")
        if action in ("create", "start"):
            s.add_argument("--expires-in", type=gpu_duration_minutes, metavar="DURATION",
                           help="Automatic stop for any profile (150m, 8h, 2d); GPU create defaults to 150m, other profiles opt in; start remembers the last selection")
        if action == "extend-expiration":
            s.add_argument("--additional-minutes", type=gpu_extension_minutes, required=True)
        if action == "connect":
            s.add_argument("--hostname", choices=[TRUSTED_DCV_HOSTNAME],
                           help="Require this trusted hostname; normally the endpoint is selected automatically")
            s.add_argument("--ssm", action="store_true")
        if action == "destroy":
            s.add_argument("--delete-stack", action="store_true")
        if action == "run":
            s.add_argument("--operation", choices=["check", "build", "promote-test", "promote-prod"], required=True)
            s.add_argument("--recipe-repo")
            s.add_argument("--recipe-ref")
            s.add_argument("--recipe-path")
            s.add_argument("--package", default="usd")
            s.add_argument("--version", default="26.05")
        if action == "result":
            s.add_argument("--command-id", required=True)
    a = p.parse_args(argv)
    if a.action not in ("list", "profiles", "profile"):
        name = getattr(a, "name", None)
        if name and a.stack:
            p.error("Use either NAME or --stack/--name, not both")
        a.stack = name or a.stack
        no_target = a.action == "hostname" and a.hostname_action in ("status", "release")
        if no_target and a.stack:
            p.error("Hostname status/release does not accept a target")
        if a.action == "create":
            a.stack = a.stack or "studio-" + a.profile + "-" + uuid.uuid4().hex[:12]
            a.image_id = a.image_id or DEFAULT_AMI
            a.instance_type = a.instance_type or PROFILES[a.profile]["instance_types"][0]
            a.instance_profile = a.instance_profile or PROFILES[a.profile]["instance_profile"]
            a.disk_size = (30 if a.profile == "publisher" else 100) if a.disk_size is None else a.disk_size
        elif not no_target and not a.stack:
            p.error("Specify a workstation name; use 'studio workstation list'")
    try:
        if a.action == "connect" and a.ssm and a.hostname:
            raise ValueError("--hostname cannot be used with --ssm")
        # An interactive SSM terminal owns its display; do not print into the session.
        enabled = not a.no_progress and not (a.action == "connect" and a.ssm)
        with progress.operation(a.action, getattr(a, "stack", None) or a.region, enabled=enabled):
            command(a)
    except (ValueError, KeyError, RuntimeError, subprocess.CalledProcessError) as exc:
        message = (exc.stderr or f"AWS command failed with exit code {exc.returncode}; inspect the reported stage") \
            if isinstance(exc, subprocess.CalledProcessError) else str(exc)
        p.exit(1, f"ERROR: {message.strip()}\n")

if __name__ == "__main__":
    main()
