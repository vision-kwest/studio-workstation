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
apt-get update
DEBIAN_FRONTEND=noninteractive apt-get install -y git python3-pip nfs-common
mkdir -p /opt/studio/packages
echo 'PUBLISHER_BOOTSTRAP_READY' > /var/log/studio-publisher-ready
"""
        result = aws(REGION, "ec2", "run-instances",
                     "--image-id", args.image_id, "--instance-type", args.instance_type,
                     "--subnet-id", args.subnet_id, "--security-group-ids", args.security_group_id,
                     "--iam-instance-profile", "Name=" + PROFILE,
                     "--metadata-options", "HttpTokens=required",
                     "--block-device-mappings", json.dumps([{"DeviceName":"/dev/sda1",
                         "Ebs":{"VolumeSize":100,"VolumeType":"gp3","Encrypted":True}}]),
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
    if args.operation == "check":
        commands = ["set -eu", "id", "test -d /opt/studio/packages",
                    "mountpoint /opt/studio/packages", "test -w /opt/studio/packages/dev"]
    else:
        if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]*", args.name) or not re.fullmatch(r"[0-9]+\\.[0-9]+", args.version):
            raise ValueError("Invalid package name/version")
        src, dst = ("dev", "test") if args.operation == "promote-test" else ("test", "prod")
        commands = ["set -eu", "test -x /opt/studio/rez/scripts/studio-rez-promote-package",
                    "STUDIO_PACKAGE_ROOT=/opt/studio/packages /opt/studio/rez/scripts/studio-rez-promote-package "
                    + args.name + " " + args.version + " " + src + " " + dst]
    result = aws(REGION, "ssm", "send-command", "--instance-ids", args.instance_id,
                 "--document-name", "AWS-RunShellScript",
                 "--parameters", json.dumps({"commands": commands}),
                 "--timeout-seconds", "120")
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
