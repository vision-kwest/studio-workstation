"""On-demand, isolated Studio EFS publisher. No workstation DCV or shared EIP."""
import json
import re
import time
from runtime import cli_progress as progress

PROFILE = "StudioEfsCiPublisher"
REGION = "us-east-1"



BOOTSTRAP = """#!/bin/bash
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
ln -s /opt/studio/packages/shared /studio
# Pin the tested public Rez publishing tools; never consume a moving branch.
git clone https://github.com/vision-kwest/studio-rez.git /opt/studio/rez
(cd /opt/studio/rez && git checkout --detach 2fee37e32a09c337d9486e00bcc2a16c3ff4b0fb)
PYTHONPATH=/opt/studio/rez/python /opt/studio/rez/scripts/studio-rez-promote-package --help >/dev/null
echo 'PUBLISHER_BOOTSTRAP_READY' > /var/log/studio-publisher-ready
"""


def template(args):
    from pathlib import Path
    definition = json.loads((Path(__file__).parent / "workstation-template.json").read_text())
    del definition["Resources"]["WorkstationSG"]
    props = definition["Resources"]["Workstation"]["Properties"]
    props["SecurityGroupIds"] = [args.security_group_id]
    props["UserData"] = {"Fn::Base64": BOOTSTRAP}
    props["PropagateTagsToVolumeOnCreation"] = True
    props["Tags"] = [{"Key": "Name", "Value": {"Ref": "AWS::StackName"}},
                     {"Key": "StudioRole", "Value": "Publisher"},
                     {"Key": "StudioProfile", "Value": "publisher"},
                     {"Key": "StudioStackId", "Value": {"Ref": "AWS::StackId"}}]
    definition["Parameters"]["DiskSizeGiB"].update({"MinValue": 30, "Default": 30})
    definition["Parameters"]["InstanceType"]["AllowedValues"] = ["m7i.large"]
    definition["Parameters"]["InstanceProfileName"]["Default"] = PROFILE
    return definition

def command(args, aws):
    if args.region != REGION:
        raise ValueError("Publisher is restricted to us-east-1")
    if not args.instance_id or not re.fullmatch(r"i-[0-9a-f]{17}", args.instance_id):
        raise ValueError("Exact publisher instance ID required")
    item = aws(REGION, "ec2", "describe-instances", "--instance-ids", args.instance_id)["Reservations"][0]["Instances"][0]
    tags = {t["Key"]: t["Value"] for t in item.get("Tags", [])}
    if tags.get("StudioRole") != "Publisher" or item.get("IamInstanceProfile", {}).get("Arn", "").split("/")[-1] != PROFILE:
        raise ValueError("Not an owned Studio publisher with the dedicated IAM profile")
    state = item["State"]["Name"]
    if state != "running":
        raise ValueError("Publisher must be running")
    if args.action == "publisher-result":
        if not re.fullmatch(r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}", args.command_id):
            raise ValueError("Exact SSM command ID required")
        result = aws(REGION, "ssm", "get-command-invocation", "--command-id", args.command_id,
                     "--instance-id", args.instance_id)
        return {"status": result["Status"], "output": result.get("StandardOutputContent", "")[-2000:],
                "error": result.get("StandardErrorContent", "")[-1200:]}
    if args.operation == "check":
        commands = ["set -eu", "test -f /var/log/studio-publisher-ready", "id", "test -d /opt/studio/packages",
                    "mountpoint /opt/studio/packages", "test -w /opt/studio/packages/dev"]
    elif args.operation == "build":
        if not re.fullmatch(r"vision-kwest/[a-z0-9-]+", args.recipe_repo or "") or not re.fullmatch(r"[0-9a-f]{40}", args.recipe_ref or ""):
            raise ValueError("Build requires a pinned commit in a vision-kwest repository")
        if not re.fullmatch(r"[a-zA-Z0-9_/-]+\.sh", args.recipe_path or "") or ".." in args.recipe_path.split("/"):
            raise ValueError("Invalid recipe path")
        commands = ["set -eu", "mountpoint -q /opt/studio/packages",
                    "test -w /opt/studio/packages/dev",
                    "rm -rf /tmp/studio-publisher-recipe",
                    "git clone --no-checkout https://github.com/" + args.recipe_repo + ".git /tmp/studio-publisher-recipe",
                    "cd /tmp/studio-publisher-recipe",
                    "git checkout --detach " + args.recipe_ref,
                    "test \"$(git rev-parse HEAD)\" = " + args.recipe_ref,
                    "test -f " + args.recipe_path,
                    "STUDIO_PACKAGE_ROOT=/opt/studio/packages bash " + args.recipe_path]
    else:
        if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]*", args.name or "") or not re.fullmatch(r"[0-9]+\.[0-9]+", args.version or ""):
            raise ValueError("Invalid package name/version")
        src, dst = ("dev", "test") if args.operation == "promote-test" else ("test", "prod")
        commands = ["set -eu", "test -x /opt/studio/rez/scripts/studio-rez-promote-package",
                    "PYTHONPATH=/opt/studio/rez/python STUDIO_PACKAGE_ROOT=/opt/studio/packages /opt/studio/rez/scripts/studio-rez-promote-package "
                    + args.name + " " + args.version + " " + src + " " + dst]
    progress.stage("Submitting publisher build" if args.operation == "build" else
                   "Checking publisher" if args.operation == "check" else "Promoting software package")
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
