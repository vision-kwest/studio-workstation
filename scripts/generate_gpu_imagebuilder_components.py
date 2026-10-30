#!/usr/bin/env python3
"""Produce GPU Image Builder component YAML/recipe inputs from exact CPU audit.

Review-only. Never invokes AWS mutating APIs. Requires PyYAML.
"""
import argparse
import copy
import json
from pathlib import Path
import yaml

NAMES=["studio-cpu-gdm-first","studio-cpu-desktop-res-stage",
       "studio-cpu-dcv-res-stage","studio-cpu-dcv-smoke-test-res"]

def script(doc, step):
    steps=doc["phases"][0]["steps"]
    match=[x for x in steps if x["name"]==step]
    if len(match)!=1: raise ValueError("Expected one "+step)
    commands=match[0]["inputs"]["commands"]
    if len(commands)!=1: raise ValueError("Expected one command block "+step)
    return commands[0],commands

def replace_once(s,old,new):
    if s.count(old)!=1: raise ValueError("Expected exactly one source anchor: "+old[:90])
    return s.replace(old,new)

def generate(audit):
    components=audit["components"]
    if [c["name"] for c in components]!=NAMES or any(c["version"]!="0.5.0" for c in components):
        raise ValueError("Unexpected CPU component versions/order")
    docs=[yaml.safe_load(c["data"]) for c in components]
    for d in docs:
        if d.get("schemaVersion")!=1.0 and str(d.get("schemaVersion"))!="1.0":
            raise ValueError("Unexpected Image Builder schema version")
    desktop=copy.deepcopy(docs[1])
    desktop["name"]="studio-gpu-desktop-res-stage"
    desktop["description"]="RES GPU desktop: preserve GDM-first and reboot, omit dummy Xorg"
    src,commands=script(desktop,"InstallDesktop")
    if src.count("ubuntu-desktop xserver-xorg-video-dummy")!=2:
        raise ValueError("Expected two desktop apt commands")
    src=src.replace("ubuntu-desktop xserver-xorg-video-dummy","ubuntu-desktop")
    start=src.index("              cat > /etc/X11/xorg.conf <<'XORG'") if "              cat > /etc/X11/xorg.conf <<'XORG'" in src else src.index("cat > /etc/X11/xorg.conf <<'XORG'")
    end=src.index("\nXORG",start)+len("\nXORG")
    src=src[:start]+src[end:]
    # ConfirmDesktopStage has its own package assertion, separate from InstallDesktop.
    confirm, confirm_commands = script(desktop, "ConfirmDesktopStage")
    confirm_commands[0] = replace_once(
        confirm,
        "dpkg-query -W ubuntu-desktop gdm3 xserver-xorg-video-dummy",
        "dpkg-query -W ubuntu-desktop gdm3",
    )
    commands[0]=src
    gpu={
      "name":"studio-gpu-nvidia-res-stage",
      "description":"Run original RES 2026.06 g4dn GRID driver installer on GPU Image Builder host",
      "schemaVersion":1.0,
      "phases":[{"name":"build","steps":[
        {"name":"InstallResNvidiaGrid","action":"ExecuteBash","onFailure":"Abort","inputs":{"commands":["""set -euxo pipefail
export DEBIAN_FRONTEND=noninteractive NEEDRESTART_MODE=a
test "$(uname -m)" = x86_64
lspci | grep -qi NVIDIA
apt-get update -o Acquire::Retries=3
apt-get install -y mesa-utils gcc make binutils pciutils "linux-headers-$(uname -r)"
dpkg --audit
# Download the SAME immutable RES-derived installation scripts as the CPU runtime.
# Builder profile must be explicitly allowed to read this release archive.
mkdir -p /root/bootstrap/latest /root/bootstrap/logs
aws s3 cp s3://studio-workstation-releases-401233741942-us-east-1/releases/v0.1.3/res-2026.06/installation-scripts.tar.gz /tmp/res-installation-scripts.tar.gz --region us-east-1
echo '813259748335482b1877192a61a008b32e613038ee831305d9e4767c51c23ab7  /tmp/res-installation-scripts.tar.gz' | sha256sum -c -
tar -xzf /tmp/res-installation-scripts.tar.gz -C /root/bootstrap/latest
test -f /root/bootstrap/latest/scripts/dcv/linux/gpu_drivers.sh
test -f /root/bootstrap/latest/package_config.yml
# Use the original RES utility bootstrap. Its yq.sh installs mikefarah/yq
# (not the Python yq wrapper) and jq.sh provides the RES jq dependency.
RES_SCRIPTS=/root/bootstrap/latest/scripts/virtual-desktop-host/linux
/bin/bash /root/bootstrap/latest/scripts/common/linux/yq.sh -s "$RES_SCRIPTS"
/bin/bash /root/bootstrap/latest/scripts/common/linux/jq.sh -o ubuntu2404 -s "$RES_SCRIPTS"
command -v yq
command -v jq
# Confirm RES package configuration can be read before driver installation.
source /root/bootstrap/latest/scripts/common/linux/config_common.sh
test "$(get_string package_config.gpu_driver.nvidia.s3_bucket_path)" = "s3://ec2-linux-nvidia-drivers/latest/"
# Detect drift in the observed mutable GRID distribution before RES installs it.
aws s3api head-object --bucket ec2-linux-nvidia-drivers --key latest/NVIDIA-Linux-x86_64-595.91.07-grid-aws.run --region us-east-1 --query ContentLength --output text | grep -qx 429294370
# Original RES uses signed aws s3 cp for public GRID distribution.
# Explicit builder IAM s3:GetObject is required; do not patch upstream installer.
source /root/bootstrap/latest/scripts/common/linux/bootstrap_common.sh
source /root/bootstrap/latest/scripts/common/linux/config_common.sh
/bin/bash /root/bootstrap/latest/scripts/common/linux/disable_nouveau_drivers.sh -o ubuntu2404 -g NVIDIA -s /root/bootstrap/latest/scripts/virtual-desktop-host/linux
/bin/bash /root/bootstrap/latest/scripts/dcv/linux/gpu_drivers.sh -o ubuntu2404 -g NVIDIA -s /root/bootstrap/latest/scripts/virtual-desktop-host/linux
command -v nvidia-smi
command -v nvidia-xconfig
"""]}},
        {"name":"RebootAfterNvidia","action":"Reboot","onFailure":"Abort","inputs":{"delaySeconds":15}},
        {"name":"ConfirmNvidia","action":"ExecuteBash","onFailure":"Abort","inputs":{"commands":["""set -euxo pipefail
nvidia-smi -L | grep -E 'Tesla T4|NVIDIA T4|T4'
test -e /dev/nvidia0
modinfo nvidia
test -f /etc/X11/xorg.conf
grep -qi nvidia /etc/X11/xorg.conf
"""]}}
      ]}]
    }
    dcv=copy.deepcopy(docs[2])
    dcv["name"]="studio-gpu-dcv-res-stage"
    dcv["description"]="CPU DCV stage plus original RES GPU nice-dcv-gl package"
    src,commands=script(dcv,"InstallDCV")
    src=replace_once(src,'xdcv=(packages/nice-xdcv_*_amd64.ubuntu2404.deb)','xdcv=(packages/nice-xdcv_*_amd64.ubuntu2404.deb)\n              gl=(packages/nice-dcv-gl*_amd64.ubuntu2404.deb)')
    src=replace_once(src,'test "${#xdcv[@]}" -eq 1','test "${#xdcv[@]}" -eq 1\n              test "${#gl[@]}" -eq 1')
    src=replace_once(src,'"./${xdcv[0]}"','"./${xdcv[0]}" "./${gl[0]}"')
    commands[0]=src
    smoke=copy.deepcopy(docs[3])
    smoke["name"]="studio-gpu-dcv-smoke-test-res"
    smoke["description"]="Original DCV smoke test plus T4 hardware verification"
    src,commands=script(smoke,"CheckDesktop")
    # Keep the validated CPU console checks; add RES-style Xorg validation.
    src += """
              nvidia-smi -L | grep -E 'Tesla T4|NVIDIA T4|T4'
              test -e /dev/nvidia0
              modinfo nvidia
              dpkg-query -W nice-dcv-gl
              test -f /etc/X11/xorg.conf
              grep -qi nvidia /etc/X11/xorg.conf
              command -v glxinfo
              # RES validates Xorg via its -auth file and xhost dcv access.
              auth=$(ps -eo args | grep '[X]org ' | sed -n 's/.*-auth \\([^ ]*\\).*/\\1/p' | head -n 1)
              test -n "$auth"
              test -r "$auth"
              DISPLAY=:0 XAUTHORITY="$auth" xhost | grep -F 'SI:localuser:dcv'
              DISPLAY=:0 XAUTHORITY="$auth" glxinfo -B | tee /tmp/studio-gpu-glxinfo.txt
              grep -Eiq 'OpenGL renderer string:.*(Tesla T4|NVIDIA T4|T4)' /tmp/studio-gpu-glxinfo.txt
              ! grep -Eiq 'llvmpipe|softpipe' /tmp/studio-gpu-glxinfo.txt
"""
    commands[0]=src
    return {"gpu_desktop":desktop,"gpu_nvidia":gpu,"gpu_dcv":dcv,"gpu_smoke":smoke}

def main():
    p=argparse.ArgumentParser()
    p.add_argument("--audit",type=Path,required=True)
    p.add_argument("--output-dir",type=Path,required=True)
    a=p.parse_args()
    docs=generate(json.loads(a.audit.read_text()))
    a.output_dir.mkdir(parents=True,exist_ok=True)
    for key,doc in docs.items():
        dest=a.output_dir/(key+".yml")
        dest.write_text(yaml.safe_dump(doc,sort_keys=False,width=120))
        print("REVIEW ONLY:",dest)
    print("No Image Builder resources created; validate prerequisites and component scripts before deployment.")

if __name__=="__main__":
    main()
