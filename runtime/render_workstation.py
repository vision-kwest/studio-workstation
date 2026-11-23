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
PINNED_GPU_PREFIX="releases/issue100-res-native-20261115/res-2026.06"
PINNED_GPU_APP_PREFIX="releases/task7-pr61-postreboot-a8ec326/res-2026.06"
PINNED_GPU_BUCKET="studio-workstation-releases-401233741942-us-east-1"
GPU_ARCHIVES={
    "installation-scripts.tar.gz": ("sgj5wZfqMTRUImWDDbxR08emK755aU7A", "6d3187049ecee202167cc462492431e3cb9a2a22d78300a8189db3fa2924ee7d"),
    "virtual-desktop.tar.gz": ("nzyYrLhYAQ.OTFNpFE_xSHPaK06T1V53", "e7686d6240db9062b5ea62f94859bd657de8c1231e8a3ba097704f9faa45d147"),
}
def render(bucket,prefix,user,profile="cpu",rez_version_id=None,rez_sha256=None):
    if not re.fullmatch(r"[a-z0-9.-]{3,63}",bucket): raise ValueError("bucket")
    if not re.fullmatch(r"[A-Za-z0-9_./-]+",prefix) or ".." in prefix: raise ValueError("prefix")
    if not re.fullmatch(r"[a-z_][a-z0-9_-]{0,30}",user): raise ValueError("user")
    if profile not in ("cpu", "gpu"): raise ValueError("profile")
    if bool(rez_version_id) != bool(rez_sha256): raise ValueError("Rez release requires version ID and SHA-256")
    if rez_version_id and (not re.fullmatch(r"[A-Za-z0-9_.-]{1,160}",rez_version_id) or not re.fullmatch(r"[a-f0-9]{64}",rez_sha256)): raise ValueError("Invalid Rez release pin")
    gpu_family = "NVIDIA" if profile == "gpu" else "NONE"
    pinned = profile == "gpu" and bucket == PINNED_GPU_BUCKET and prefix.strip("/") == PINNED_GPU_PREFIX
    prefix=prefix.strip("/")
    app_prefix = PINNED_GPU_APP_PREFIX if pinned else prefix
    app="s3://"+bucket+"/"+app_prefix+"/virtual-desktop.tar.gz"
    boot="s3://"+bucket+"/"+prefix+"/installation-scripts.tar.gz"
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
        "APP_VERSION_LINE", "STUDIO_APP_VERSION_ID="+GPU_ARCHIVES["virtual-desktop.tar.gz"][0] if pinned else "").replace(
        "APP_SHA_LINE", "STUDIO_APP_SHA256="+GPU_ARCHIVES["virtual-desktop.tar.gz"][1] if pinned else "")
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
        bootstrap_version_id=GPU_ARCHIVES["installation-scripts.tar.gz"][0] if pinned else None,
        bootstrap_sha256=GPU_ARCHIVES["installation-scripts.tar.gz"][1] if pinned else None)
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
    template["Resources"]["Workstation"]["Properties"]["UserData"]={"Fn::Base64":{"Fn::Sub":render(a.bucket,a.prefix,a.user,a.profile,a.rez_version_id,a.rez_sha256)}}
    a.output.parent.mkdir(parents=True,exist_ok=True)
    a.output.write_text(json.dumps(template,indent=2)+"\n")
    print("Generated reviewable CloudFormation:",a.output)
if __name__=="__main__": main()
