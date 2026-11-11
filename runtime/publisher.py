"""On-demand, isolated Studio EFS publisher. No workstation DCV or shared EIP."""
import json
import re
import time

PROFILE = "StudioEfsCiPublisher"
FS = "fs-054fd0b72429f4569"
TAG = "StudioPublisher"
REGION = "us-east-1"


def command(args, aws):
    if args.region != REGION:
        raise ValueError("Publisher is restricted to us-east-1")
    if args.action == "publisher-create":
        if args.instance_type != "m7i.large":
            raise ValueError("Publisher instance type must be m7i.large")
        script = """#!/bin/bash
set -eux
export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y git python3-pip nfs-common binutils make rustc cargo pkg-config libssl-dev gettext
git clone --depth 1 --branch v2.3.1 https://github.com/aws/efs-utils.git /tmp/studio-efs-utils
(cd /tmp/studio-efs-utils && ./build-deb.sh)
apt-get install -y stunnel4
dpkg -i /tmp/studio-efs-utils/build/amazon-efs-utils*.deb
mkdir -p /opt/studio/packages
printf 'fs-054fd0b72429f4569:/ /opt/studio/packages efs _netdev,tls,iam,nofail,x-systemd.automount 0 0\n' >> /etc/fstab
systemctl daemon-reload
mount /opt/studio/packages
test -f /opt/studio/packages/prod/usd/26.05/package.py
# Pin the tested public Rez publishing tools; never consume a moving branch.
git clone https://github.com/vision-kwest/studio-rez.git /opt/studio/rez
(cd /opt/studio/rez && git checkout --detach 2fee37e32a09c337d9486e00bcc2a16c3ff4b0fb)
PYTHONPATH=/opt/studio/rez/python /opt/studio/rez/scripts/studio-rez-promote-package --help >/dev/null
echo 'PUBLISHER_BOOTSTRAP_READY' > /var/log/studio-publisher-ready
"""
        result = aws(REGION, "ec2", "run-instances",
                     "--image-id", args.image_id, "--instance-type", args.instance_type,
                     "--subnet-id", args.subnet_id, "--security-group-ids", args.security_group_id,
                     "--iam-instance-profile", "Name=" + PROFILE,
                     "--metadata-options", "HttpTokens=required",
                     "--block-device-mappings", json.dumps([{"DeviceName":"/dev/sda1",
                         "Ebs":{"VolumeSize":30,"VolumeType":"gp3","Encrypted":True,"DeleteOnTermination":True}}]),
                     "--user-data", script,
                     "--tag-specifications", 'ResourceType=instance,Tags=[{Key=StudioRole,Value=Publisher},{Key=Name,Value=studio-publisher}]')
        return {"instance_id": result["Instances"][0]["InstanceId"]}
    if not args.instance_id or not re.fullmatch(r"i-[0-9a-f]{17}", args.instance_id):
        raise ValueError("Exact publisher instance ID required")
    item = aws(REGION, "ec2", "describe-instances", "--instance-ids", args.instance_id)["Reservations"][0]["Instances"][0]
    tags = {t["Key"]: t["Value"] for t in item.get("Tags", [])}
    if tags.get("StudioRole") != "Publisher" or item.get("IamInstanceProfile", {}).get("Arn", "").split("/")[-1] != PROFILE:
        raise ValueError("Not an owned Studio publisher with the dedicated IAM profile")
    state = item["State"]["Name"]
    if args.action == "publisher-status":
        return {"instance_id": args.instance_id, "state": state}
    if args.action in ("publisher-start", "publisher-stop"):
        expected = "stopped" if args.action == "publisher-start" else "running"
        if state != expected:
            raise ValueError("Expected " + expected + ", got " + state)
        operation = "start-instances" if args.action == "publisher-start" else "stop-instances"
        aws(REGION, "ec2", operation, "--instance-ids", args.instance_id)
        return {"instance_id": args.instance_id, "action": operation}
    if state != "running":
        raise ValueError("Publisher must be running")
    if args.action == "publisher-result":
        if not re.fullmatch(r"[0-9a-f-]{36}", args.command_id):
            raise ValueError("Exact SSM command ID required")
        result = aws(REGION, "ssm", "get-command-invocation", "--command-id", args.command_id,
                     "--instance-id", args.instance_id)
        return {"status": result["Status"], "output": result.get("StandardOutputContent", "")[-2000:],
                "error": result.get("StandardErrorContent", "")[-1200:]}
    if args.operation == "check":
        commands = ["set -eu", "id", "test -d /opt/studio/packages",
                    "mountpoint /opt/studio/packages", "test -w /opt/studio/packages/dev"]
    elif args.operation == "build":
        if not re.fullmatch(r"vision-kwest/[a-z0-9-]+", args.recipe_repo) or not re.fullmatch(r"[0-9a-f]{40}", args.recipe_ref):
            raise ValueError("Build requires a pinned commit in a vision-kwest repository")
        if not re.fullmatch(r"[a-zA-Z0-9_/-]+\.sh", args.recipe_path) or ".." in args.recipe_path.split("/"):
            raise ValueError("Invalid recipe path")
        commands = ["set -eu", "mountpoint -q /opt/studio/packages",
                    "test -w /opt/studio/packages/dev",
                    "rm -rf /tmp/studio-publisher-recipe",
                    "git clone --no-checkout https://github.com/" + args.recipe_repo + ".git /tmp/studio-publisher-recipe",
                    "cd /tmp/studio-publisher-recipe",
                    "git checkout --detach " + args.recipe_ref,
                    "test -f " + args.recipe_path,
                    "STUDIO_PACKAGE_ROOT=/opt/studio/packages bash " + args.recipe_path]
    else:
        if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]*", args.name) or not re.fullmatch(r"[0-9]+\.[0-9]+", args.version):
            raise ValueError("Invalid package name/version")
        src, dst = ("dev", "test") if args.operation == "promote-test" else ("test", "prod")
        commands = ["set -eu", "test -x /opt/studio/rez/scripts/studio-rez-promote-package",
                    "PYTHONPATH=/opt/studio/rez/python STUDIO_PACKAGE_ROOT=/opt/studio/packages /opt/studio/rez/scripts/studio-rez-promote-package "
                    + args.name + " " + args.version + " " + src + " " + dst]
    result = aws(REGION, "ssm", "send-command", "--instance-ids", args.instance_id,
                 "--document-name", "AWS-RunShellScript",
                 "--parameters", json.dumps({"commands": commands}),
                 "--timeout-seconds", "3600" if args.operation == "build" else "120")
    if args.operation == "build":
        return {"command_id": result["Command"]["CommandId"], "status": "submitted"}
    cid = result["Command"]["CommandId"]
    for _ in range(30):
        time.sleep(2)
        response = aws(REGION, "ssm", "get-command-invocation",
                       "--command-id", cid, "--instance-id", args.instance_id)
        if response["Status"] in ("Success", "Failed", "TimedOut", "Cancelled"):
            if response["Status"] != "Success":
                raise RuntimeError("Publisher command failed: " + response.get("StandardErrorContent", "")[-1200:])
            return {"command_id": cid, "status": response["Status"], "output": response.get("StandardOutputContent", "")[-2000:]}
    raise RuntimeError("Publisher SSM command timed out")
