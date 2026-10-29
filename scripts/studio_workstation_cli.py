#!/usr/bin/env python3
"""Small AWS CLI/CloudFormation wrapper for the validated CPU RES workstation.

No external Python dependencies. Uses existing render_workstation.py and AWS CLI.
Mutating actions require --yes and an exact --confirm-stack or --confirm-instance.
"""
import argparse
import ipaddress
import json
import re
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

def run(args, *, capture=True):
    if capture:
        return subprocess.check_output(args, text=True)
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

def require_approval(args, expected, supplied):
    if not args.yes or supplied != expected:
        raise ValueError(f"Refusing mutation: pass --yes --confirm-{'instance' if expected.startswith('i-') else 'stack'} {expected}")

def command(args):
    if args.action == "create":
        if not re.fullmatch(r"[a-zA-Z][a-zA-Z0-9-]{0,127}", args.stack):
            raise ValueError("Invalid CloudFormation stack name")
        if not re.fullmatch(r"v[0-9]+\.[0-9]+\.[0-9]+", args.version):
            raise ValueError("Release must be a version such as v0.1.3")
        ipaddress.IPv4Network(args.client_cidr, strict=False)
        prefix = f"releases/{args.version}/res-2026.06"
        with tempfile.TemporaryDirectory(prefix="studio-cli-") as temp:
            template = Path(temp) / "workstation.json"
            render = [sys.executable, str(ROOT / "runtime/render_workstation.py"),
                      "--bucket", args.bucket, "--prefix", prefix, "--user", "workstation",
                      "--output", str(template)]
            print("Rendering:", " ".join(render), flush=True)
            run(render, capture=False)
            validate = ["aws", "cloudformation", "validate-template", "--template-body",
                        f"file://{template}", "--region", args.region]
            run(validate, capture=False)
            parameters = [
                f"ImageId={args.image_id}", f"InstanceType={args.instance_type}",
                f"VpcId={args.vpc_id}", f"SubnetId={args.subnet_id}",
                f"ClientCidr={args.client_cidr}", f"InstanceProfileName={args.instance_profile}",
                f"DiskSizeGiB={args.disk_size}",
            ]
            deploy = ["aws", "cloudformation", "deploy", "--stack-name", args.stack,
                      "--template-file", str(template), "--region", args.region,
                      "--parameter-overrides", *parameters, "--no-fail-on-empty-changeset"]
            print("Stack:", args.stack, "Release:", args.version, "DCV ingress:", args.client_cidr, flush=True)
            if args.client_cidr == "0.0.0.0/0":
                print("WARNING: DCV login will be publicly accessible on TCP 8443.", flush=True)
            if not args.yes:
                print("DRY RUN: validated template; deployment not executed. Re-run with --yes --confirm-stack", args.stack)
                return
            require_approval(args, args.stack, args.confirm_stack)
            # Never use deploy to silently modify an existing workstation.
            try:
                aws(args.region, "cloudformation", "describe-stacks", "--stack-name", args.stack)
            except subprocess.CalledProcessError:
                pass
            else:
                raise ValueError("Stack already exists; create requires a new stack name")
            run(deploy, capture=False)
            print(json.dumps(describe(args.stack, args.region), indent=2))
        return
    if args.action == "status":
        print(json.dumps(describe(args.stack, args.region), indent=2))
        return
    state = describe(args.stack, args.region)
    iid = state["instance"]
    if args.action == "connect":
        if state["state"] != "running":
            raise ValueError("Instance is not running")
        if args.ssm:
            run(["aws", "ssm", "start-session", "--target", iid, "--region", args.region], capture=False)
        else:
            print(state["dcv_url"] or "No public IPv4 address. Use --ssm for administrative access.")
            print("Use the local workstation account; browser certificate trust is not yet configured.")
        return
    if args.action in ("start", "stop"):
        require_approval(args, iid, args.confirm_instance)
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
        require_approval(args, args.stack, args.confirm_stack)
        if args.confirm_instance != iid:
            raise ValueError(f"Refusing destruction: pass --confirm-instance {iid}")
        if not args.delete_stack:
            raise ValueError("Refusing destruction: add --delete-stack to explicitly request permanent teardown")
        resources = aws(args.region, "cloudformation", "list-stack-resources",
                        "--stack-name", args.stack)["StackResourceSummaries"]
        kinds = {r["ResourceType"] for r in resources}
        if not kinds.issubset({"AWS::EC2::Instance", "AWS::EC2::SecurityGroup"}):
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
    for action in ("create", "status", "start", "stop", "connect", "destroy"):
        s = sub.add_parser(action)
        s.add_argument("--stack", default=DEFAULT_STACK if action != "create" else None,
                       required=action == "create")
        s.add_argument("--region", default=DEFAULT_REGION)
        if action in ("create", "start", "stop", "destroy"):
            s.add_argument("--yes", action="store_true", help="Explicitly authorize AWS mutation")
        if action in ("create", "destroy"):
            s.add_argument("--confirm-stack")
        if action in ("start", "stop", "destroy"):
            s.add_argument("--confirm-instance")
        if action == "create":
            s.add_argument("--profile", choices=["cpu"], default="cpu")
            s.add_argument("--version", default="v0.1.3")
            s.add_argument("--bucket", default=DEFAULT_BUCKET)
            s.add_argument("--image-id", default=DEFAULT_AMI)
            s.add_argument("--vpc-id", default=DEFAULT_VPC)
            s.add_argument("--subnet-id", default=DEFAULT_SUBNET)
            s.add_argument("--instance-profile", default=DEFAULT_PROFILE)
            s.add_argument("--instance-type", choices=["m7i.large", "m7i.xlarge"], default="m7i.large")
            s.add_argument("--disk-size", type=int, default=100)
            s.add_argument("--client-cidr", default="0.0.0.0/0")
        if action == "connect":
            s.add_argument("--ssm", action="store_true", help="Open administrative SSM session instead of printing DCV URL")
        if action == "destroy":
            s.add_argument("--delete-stack", action="store_true")
    a = p.parse_args(argv)
    try:
        command(a)
    except (ValueError, KeyError, subprocess.CalledProcessError) as exc:
        p.exit(1, f"ERROR: {exc}\n")

if __name__ == "__main__":
    main()
