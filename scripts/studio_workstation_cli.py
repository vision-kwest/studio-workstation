#!/usr/bin/env python3
"""Small AWS CLI/CloudFormation wrapper for the validated CPU RES workstation.

No external Python dependencies. Uses existing render_workstation.py and AWS CLI.
Commands run when explicitly invoked; use --dry-run to preview create, and --delete-stack for teardown.
"""
import argparse
import ipaddress
import json
import re
import socket
import ssl
import subprocess
import sys
import tempfile
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

# Task 3: IAM role and exact scope verified; no EC2 launch performed.
# Only this one acceptance stack/token may reach the supervised create path.
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
    return json.loads(run(["aws", *args, "--region", region, "--output", "json"]))

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


def command(args):
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
        prefix = GPU_PREFIX if gpu else f"releases/{args.version}/res-2026.06"
        with tempfile.TemporaryDirectory(prefix="studio-cli-") as temp:
            template = Path(temp) / "workstation.json"
            render = [sys.executable, str(ROOT / "runtime/render_workstation.py"),
                      "--bucket", args.bucket, "--prefix", prefix, "--user", "workstation",
                      "--output", str(template)]
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
                from runtime import pr65_gpu_create
                # The CPU deploy/wait path is too slow to arm a GPU expiration
                # schedule; use asynchronous CreateStack with bounded discovery.
                # Runtime IAM and schedule-group verification still fail closed.
                outcome = pr65_gpu_create.create_guarded(
                    aws, args.region, args.stack, args.cleanup_token, template, parameters)
                print(json.dumps(outcome, indent=2))
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
            state["trusted_dcv_url"] = verified_dcv_hostname(args.hostname, state["public_ip"])
        print(json.dumps(state, indent=2))
        return
    state = describe(args.stack, args.region)
    iid = state["instance"]
    if args.action == "connect":
        if state["state"] != "running":
            raise ValueError("Instance is not running")
        if args.ssm:
            run(["aws", "ssm", "start-session", "--target", iid, "--region", args.region], capture=False)
        else:
            if args.hostname:
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
        run(["aws", "ec2", f"{args.action}-instances", "--instance-ids", iid,
             "--region", args.region], capture=False)
        waiter = "instance-status-ok" if args.action == "start" else "instance-stopped"
        run(["aws", "ec2", "wait", waiter, "--instance-ids", iid, "--region", args.region], capture=False)
        print(json.dumps(describe(args.stack, args.region), indent=2))
        print("Check SSM and DCV separately; EC2 health alone does not prove desktop readiness.")
        return
    if args.action == "destroy":
        if not args.delete_stack:
            raise ValueError("Refusing destruction: add --delete-stack to explicitly request permanent teardown")
        resources = aws(args.region, "cloudformation", "list-stack-resources",
                        "--stack-name", args.stack)["StackResourceSummaries"]
        kinds = {r["ResourceType"] for r in resources}
        allowed = {"AWS::EC2::Instance", "AWS::EC2::SecurityGroup"}
        if args.stack.startswith("studio-gpu-cli-"):
            allowed.add("AWS::EC2::LaunchTemplate")
        if not kinds.issubset(allowed):
            raise ValueError(f"Unexpected stack resource types: {sorted(kinds)}")
        print("Deleting CloudFormation stack; retained EBS volumes require separate audit.", flush=True)
        run(["aws", "cloudformation", "delete-stack", "--stack-name", args.stack,
             "--region", args.region], capture=False)
        run(["aws", "cloudformation", "wait", "stack-delete-complete", "--stack-name", args.stack,
             "--region", args.region], capture=False)
        print("Stack deleted. Audit unattached EBS volumes; none are deleted by this CLI.")

def main(argv=None):
    p = argparse.ArgumentParser(prog="studio workstation",
                                description="Validated CPU RES workstation lifecycle wrapper")
    sub = p.add_subparsers(dest="action", required=True)
    for action in ("create", "status", "start", "stop", "connect", "destroy", "check-ready"):
        s = sub.add_parser(action)
        s.add_argument("--stack", default=DEFAULT_STACK if action != "create" else None,
                       required=action == "create")
        s.add_argument("--region", default=DEFAULT_REGION)
        if action == "create":
            s.add_argument("--dry-run", action="store_true",
                           help="Render and validate without launching AWS resources")
            s.add_argument("--profile", choices=["cpu", "gpu"], default="cpu")
            s.add_argument("--version", default="v0.1.3")
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
