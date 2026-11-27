#!/usr/bin/env python3
"""Offline CloudFormation renderer using unchanged RES 2026.06 workstation user data."""
import argparse
import json
import re
from pathlib import Path
from jinja2 import Environment, FileSystemLoader, StrictUndefined

ROOT=Path(__file__).resolve().parent
REGION=chr(36)+"{AWS::Region}"
# GPU bootstrap is published separately; the independently verified app
# archive stays at the original Task 7 release key. Do not infer that both
# archives live beneath the same S3 prefix.
PINNED_GPU_PREFIX="releases/issue100-res-package-policy-20261125/res-2026.06"
PINNED_GPU_APP_PREFIX="releases/task7-pr61-postreboot-a8ec326/res-2026.06"
PINNED_GPU_BUCKET="studio-workstation-releases-401233741942-us-east-1"
GPU_ARCHIVES={
    "installation-scripts.tar.gz": ("AsJLU_Yt7gAiAV5i_z27HbETTJ7pg2cq", "a468151e178ded6f5c5386e6e41676613c318db63bec995e7b82fb4f627a8752"),
    "virtual-desktop.tar.gz": ("nzyYrLhYAQ.OTFNpFE_xSHPaK06T1V53", "e7686d6240db9062b5ea62f94859bd657de8c1231e8a3ba097704f9faa45d147"),
}
def archive_sources(bucket, prefix, profile):
    """Select the same archive keys and optional pins for bootstrap and IAM."""
    prefix = prefix.strip("/")
    pinned = profile == "gpu" and bucket == PINNED_GPU_BUCKET and prefix == PINNED_GPU_PREFIX
    return {name: (source + "/" + name, *(GPU_ARCHIVES[name] if pinned else (None, None)))
            for name, source in (("installation-scripts.tar.gz", prefix),
                                 ("virtual-desktop.tar.gz", PINNED_GPU_APP_PREFIX if pinned else prefix))}

def render(bucket,prefix,user,profile="cpu",rez_version_id=None,rez_sha256=None):
    if not re.fullmatch(r"[a-z0-9.-]{3,63}",bucket): raise ValueError("bucket")
    if not re.fullmatch(r"[A-Za-z0-9_./-]+",prefix) or ".." in prefix: raise ValueError("prefix")
    if not re.fullmatch(r"[a-z_][a-z0-9_-]{0,30}",user): raise ValueError("user")
    if profile not in ("cpu", "gpu"): raise ValueError("profile")
    if bool(rez_version_id) != bool(rez_sha256): raise ValueError("Rez release requires version ID and SHA-256")
    if rez_version_id and (not re.fullmatch(r"[A-Za-z0-9_.-]{1,160}",rez_version_id) or not re.fullmatch(r"[a-f0-9]{64}",rez_sha256)): raise ValueError("Invalid Rez release pin")
    gpu_family = "NVIDIA" if profile == "gpu" else "NONE"
    archives = archive_sources(bucket, prefix, profile)
    bootstrap_key, bootstrap_version, bootstrap_sha = archives["installation-scripts.tar.gz"]
    app_key, app_version, app_sha = archives["virtual-desktop.tar.gz"]
    app="s3://"+bucket+"/"+app_key
    boot="s3://"+bucket+"/"+bootstrap_key
    command="""set -e
if ! id -u USERNAME >/dev/null 2>&1; then
  groupadd --gid 2000 USERNAME
  useradd --uid 2000 --gid 2000 --create-home --shell /bin/bash USERNAME
  passwd -l USERNAME
  runuser -u USERNAME -- bash -se <<'STUDIO_UPDATE_PROMPTS'
mkdir -p "$HOME/.config/autostart" "$HOME/.config/systemd/user"
cat > "$HOME/.config/autostart/update-notifier.desktop" <<'DESKTOP'
[Desktop Entry]
Type=Application
Name=Update Notifier
Exec=update-notifier
Hidden=true
DESKTOP
ln -sf /dev/null "$HOME/.config/systemd/user/update-notifier-release.path"
ln -sf /dev/null "$HOME/.config/systemd/user/update-notifier-release.service"
STUDIO_UPDATE_PROMPTS
fi
usermod -aG sudo USERNAME
cat > /etc/studio-standalone.env <<'CONFIG'
STUDIO_STANDALONE=true
STUDIO_APP_PACKAGE_URI=APPURI
STUDIO_GPU_FAMILIES=g4dn,g5,g6
STUDIO_DCV_IDLE_TIMEOUT=0
STUDIO_DCV_IDLE_WARNING=0
APP_VERSION_LINE
APP_SHA_LINE
CONFIG
chmod 600 /etc/studio-standalone.env
set -a
source /etc/studio-standalone.env
set +a
# Set desktop defaults before RES installs GNOME and starts the first session.
install -d -m 0755 /usr/share/glib-2.0/schemas
cat > /usr/share/glib-2.0/schemas/99_studio-desktop.gschema.override <<'STUDIO_IDLE_DEFAULTS'
[org.gnome.desktop.session]
idle-delay=uint32 0
[org.gnome.desktop.screensaver]
lock-enabled=false
STUDIO_IDLE_DEFAULTS
chmod 0644 /usr/share/glib-2.0/schemas/99_studio-desktop.gschema.override
# Initial package installation compiles schemas; reentry handles installed GNOME.
if test -f /usr/share/glib-2.0/schemas/org.gnome.desktop.session.gschema.xml; then
  glib-compile-schemas /usr/share/glib-2.0/schemas
fi
/bin/bash scripts/virtual-desktop-host/linux/install.sh -p false -m vdi-app -g GPUFAMILY -e standalone -n standalone -o USERNAME -d CONSOLE -i standalone-console -a REGION
""".replace("USERNAME",user).replace("APPURI",app).replace("REGION",REGION).replace("GPUFAMILY",gpu_family).replace(
        "APP_VERSION_LINE", "STUDIO_APP_VERSION_ID="+app_version if app_version else "").replace(
        "APP_SHA_LINE", "STUDIO_APP_SHA256="+app_sha if app_sha else "")
    if rez_version_id:
        command += f"""cat > /etc/studio-software-release.env <<'STUDIO_RELEASE'
STUDIO_REZ_VERSION_ID={rez_version_id}
STUDIO_REZ_SHA256={rez_sha256}
STUDIO_RELEASE
chmod 600 /etc/studio-software-release.env
"""
    script=(ROOT.parent/"scripts/setup_workstation_software.sh").read_text()
    command += f"""cat > /usr/local/sbin/studio-software-setup <<'STUDIO_SETUP'
{script}
STUDIO_SETUP
chmod 700 /usr/local/sbin/studio-software-setup
cat > /etc/systemd/system/studio-software-setup.service <<'STUDIO_UNIT'
[Unit]
Description=Studio post-DCV shared storage and optional Rez setup
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
ExecStart=/usr/local/sbin/studio-software-setup
RemainAfterExit=yes
Restart=on-failure
RestartSec=30s

[Install]
WantedBy=multi-user.target
STUDIO_UNIT
systemctl daemon-reload
systemctl enable studio-software-setup.service
if test -f /root/bootstrap/semaphore/app_installed.lock; then
  systemctl start --no-block studio-software-setup.service || true
fi
"""
    env=Environment(loader=FileSystemLoader(str(ROOT/"upstream")),undefined=StrictUndefined,keep_trailing_newline=True)
    userdata=env.get_template("bootstrap_userdata_linux_base_non_substitution.sh.jinja2").render(
        base_os="ubuntu2404",aws_region=REGION,bootstrap_package_uri=boot,
        install_commands=[command],infra_config=None,proxy_config=None,
        bootstrap_version_id=bootstrap_version,bootstrap_sha256=bootstrap_sha)
    # Fn::Sub must not interpolate original RES shell variables.
    # Preserve only CloudFormation's AWS::Region pseudo parameter.
    userdata=re.sub(r"\$\{(?!AWS::Region\})([^}]+)\}",lambda m: chr(36)+"{!"+m.group(1)+"}",userdata)
    if len(userdata.encode())>16384: raise ValueError("EC2 user-data exceeds 16KiB")
    return userdata
def main():
    p=argparse.ArgumentParser()
    p.add_argument("--profile",choices=["cpu","gpu"],default="cpu")
    p.add_argument("--rez-version-id")
    p.add_argument("--rez-sha256")
    p.add_argument("--lifecycle",choices=["temporary","persistent"],default="temporary",help="GPU only; persistent enables stop/start")
    p.add_argument("--bucket",required=True)
    p.add_argument("--prefix",required=True)
    p.add_argument("--user",default="workstation")
    p.add_argument("--output",type=Path,required=True)
    a=p.parse_args()
    template=json.loads((ROOT/"workstation-template.json").read_text())
    template["Parameters"]["InstanceProfileName"]["AllowedValues"] = ["StudioWorkstationSSMRole"]
    template["Parameters"]["InstanceProfileName"]["Description"] = "Existing supported SSM profile; this stack grants its selected release reads"
    statements = []
    for key, version, _ in archive_sources(a.bucket, a.prefix, a.profile).values():
        statement = {"Effect": "Allow", "Action": "s3:GetObjectVersion" if version else "s3:GetObject",
                     "Resource": f"arn:aws:s3:::{a.bucket}/{key}"}
        if version:
            statement["Condition"] = {"StringEquals": {"s3:VersionId": version}}
        statements.append(statement)
    if a.profile == "gpu":
        statements.extend(json.loads((ROOT.parent / "infra/res202606-nvidia-grid-read.policy.json").read_text())["Statement"])
    template["Resources"]["BootstrapReadPolicy"] = {
        "Type": "AWS::IAM::RolePolicy", "Properties": {
            "PolicyName": {"Fn::Join": ["", ["studio-bootstrap-",
                {"Fn::Select": [2, {"Fn::Split": ["/", {"Ref": "AWS::StackId"}]}]}]]},
            "RoleName": "StudioWorkstationSSMRole",
            "PolicyDocument": {"Version": "2012-10-17", "Statement": statements}}}
    template["Resources"]["Workstation"]["DependsOn"] = ["BootstrapReadPolicy"]
    template["Resources"]["Workstation"]["Properties"]["SecurityGroupIds"].append("sg-058f2babb18747976")
    if a.profile == "gpu":
        template["Description"] = "GPU g4dn RES 2026.06 derived runtime; requires separately validated NVIDIA-capable AMI"
        # Task 7 temporary GPU test only: no open-by-default DCV and no orphan EBS.
        template["Parameters"]["ClientCidr"]["Default"] = "127.0.0.1/32"
        instance = template["Resources"]["Workstation"]["Properties"]
        instance["BlockDeviceMappings"][0]["Ebs"]["DeleteOnTermination"] = True
        instance["InstanceInitiatedShutdownBehavior"] = "terminate"
        instance["MetadataOptions"]["HttpPutResponseHopLimit"] = 2
        template["Parameters"]["InstanceType"]["Default"] = "g4dn.xlarge"
        template["Parameters"]["InstanceType"]["AllowedValues"] = ["g4dn.xlarge"]
        template["Resources"]["Workstation"]["Properties"]["Tags"][0]["Value"] = "studio-workstation-res-gpu"
        if a.lifecycle == "persistent":
            instance["InstanceInitiatedShutdownBehavior"] = "stop"
            template["Parameters"]["InstanceType"]["AllowedValues"] = ["g4dn.xlarge", "g4dn.2xlarge"]
        else:
            instance["Tags"].extend([{"Key":"Task7","Value":"PR61"},{"Key":"Issue","Value":"60"}])
    elif a.lifecycle != "temporary":
        raise ValueError("persistent lifecycle is only supported for GPU")
    props = template["Resources"]["Workstation"]["Properties"]
    props["PropagateTagsToVolumeOnCreation"] = True
    props["Tags"][0]["Value"] = {"Ref": "AWS::StackName"}
    props["Tags"].extend([{"Key": "StudioStackId", "Value": {"Ref": "AWS::StackId"}},
                          {"Key": "StudioProfile", "Value": a.profile}])
    # Reuse RES's package selection without its watchdog-only presence shortcut.
    # State Manager retains post-DCV retries independently of the local CLI.
    template["Resources"]["EfsClientDocument"] = {
        "Type": "AWS::SSM::Document", "Properties": {"DocumentType": "Command", "Content": {
            "schemaVersion": "2.2", "description": "Install the EFS helper using RES after DCV is ready",
            "mainSteps": [
                {"action": "aws:runShellScript", "name": "resReady", "inputs": {
                    "runCommand": ["test -f /root/bootstrap/semaphore/linux_vdi_config_host_ready.lock"],
                    "timeoutSeconds": "30", "onFailure": "exit"}},
                {"action": "aws:runShellScript", "name": "needsInstallation", "inputs": {
                    "runCommand": ["! /usr/sbin/mount.efs --version >/dev/null 2>&1"],
                    "timeoutSeconds": "30", "onFailure": "successAndExit"}},
                {"action": "aws:runShellScript", "name": "install", "inputs": {
                    "runCommand": [
                        "/bin/bash -se <<'STUDIO_RES_EFS'",
                        "exec 9>/run/studio-efs-install.lock",
                        "flock -n 9",
                        "/usr/sbin/mount.efs --version >/dev/null 2>&1 && exit 0",
                        "BASE_OS=ubuntu2404",
                        "SCRIPT_DIR=/root/bootstrap/latest/scripts/virtual-desktop-host",
                        'source "$SCRIPT_DIR/../common/linux/debian/nfs_utils.sh"',
                        "efs_build_dir=$(mktemp -d)",
                        "trap '/bin/rm -rf -- \"$efs_build_dir\"' EXIT",
                        'cd "$efs_build_dir"',
                        "export NEEDRESTART_SUSPEND=1",
                        'command -v "$(get_stunnel_package_name)" >/dev/null || install_stunnel_impl',
                        "efs_original_path=$PATH",
                        "install_efs_mount_helper_impl",
                        'export PATH="$efs_original_path"',
                        '/usr/sbin/mount.efs --version >/dev/null 2>&1 || '
                        'DEBIAN_FRONTEND=noninteractive apt install -y --reinstall "$efs_build_dir"/efs-utils/build/amazon-efs-utils*deb',
                        "STUDIO_RES_EFS"],
                    "timeoutSeconds": "1200", "onFailure": "exit"}},
                {"action": "aws:runShellScript", "name": "verifyHelper", "inputs": {
                    "runCommand": ["/usr/sbin/mount.efs --version"], "timeoutSeconds": "30", "onFailure": "exit"}}]}}}
    template["Resources"]["EfsClientAssociationRole"] = {
        "Type": "AWS::IAM::Role", "Properties": {
            "AssumeRolePolicyDocument": {"Version": "2012-10-17", "Statement": [{
                "Effect": "Allow", "Principal": {"Service": "ssm.amazonaws.com"},
                "Action": "sts:AssumeRole"}]},
            "Policies": [{"PolicyName": "InstallEfsClient", "PolicyDocument": {
                "Version": "2012-10-17", "Statement": [{"Effect": "Allow",
                    "Action": "ssm:SendCommand", "Resource": [
                        {"Fn::Sub": "arn:${AWS::Partition}:ssm:${AWS::Region}:${AWS::AccountId}:document/${EfsClientDocument}"},
                        {"Fn::Sub": "arn:${AWS::Partition}:ec2:${AWS::Region}:${AWS::AccountId}:instance/${Workstation}"}]}]}}]}}
    template["Resources"]["EfsClientAssociation"] = {
        "Type": "AWS::SSM::Association", "Properties": {
            "Name": {"Ref": "EfsClientDocument"},
            "AssociationDispatchAssumeRole": {"Fn::GetAtt": ["EfsClientAssociationRole", "Arn"]},
            "Targets": [{"Key": "tag:StudioStackId", "Values": [{"Ref": "AWS::StackId"}]}],
            "ScheduleExpression": "rate(30 minutes)"}}
    template["Resources"]["Workstation"]["Properties"]["UserData"]={"Fn::Base64":{"Fn::Sub":render(a.bucket,a.prefix,a.user,a.profile,a.rez_version_id,a.rez_sha256)}}
    a.output.parent.mkdir(parents=True,exist_ok=True)
    a.output.write_text(json.dumps(template,indent=2)+"\n")
    print("Generated reviewable CloudFormation:",a.output)
if __name__=="__main__": main()
