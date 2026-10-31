#!/usr/bin/env python3
"""Generate review-only staged RES GPU Image Builder YAML from registered component YAML."""
import argparse
import copy
from pathlib import Path
import subprocess
import tempfile
import tarfile
import hashlib
import yaml

ROOT = "/root/bootstrap/latest"
BASE = ROOT + "/scripts/dcv"
ARCHIVE = "s3://studio-workstation-releases-401233741942-us-east-1/releases/v0.1.3/res-2026.06/installation-scripts.tar.gz"
ARCHIVE_SHA = "813259748335482b1877192a61a008b32e613038ee831305d9e4767c51c23ab7"
DRIVER_SHA = "cae8d82b8407c6371805485b148aa667550abf1775ea7999be69da81cf593156"
VERSION_ID = "b94g7u7FkVYD9m63G6RE3xHNLZSXrz3r"

PATCH = '''from pathlib import Path
p = Path("/root/bootstrap/latest/scripts/dcv/linux/gpu_drivers.sh")
s = p.read_text()
a = s.index("function install_nvidia_grid_drivers () {")
b = s.index("function install_nvidia_public_drivers()", a)
f = s[a:b]
old = '$AWS --region ${DRIVER_BUCKET_REGION} s3 cp --quiet --recursive $NVIDIA_S3_BUCKET_PATH .'
new = """$AWS --region us-east-1 s3api get-object --bucket studio-workstation-releases-401233741942-us-east-1 --key dependencies/nvidia-grid/595.91.07/NVIDIA-Linux-x86_64-595.91.07-grid-aws.run --version-id b94g7u7FkVYD9m63G6RE3xHNLZSXrz3r NVIDIA-Linux-x86_64-595.91.07-grid-aws.run
  echo 'cae8d82b8407c6371805485b148aa667550abf1775ea7999be69da81cf593156  NVIDIA-Linux-x86_64-595.91.07-grid-aws.run' | sha256sum -c -"""
assert f.count(old) == 1
f = f.replace(old, new)
old = 'local x_server_pid=$(cat /tmp/.X0-lock)'
new = chr(10).join([
    'local x_server_pid=""',
    '  if [[ -f /tmp/.X0-lock ]]; then',
    '    x_server_pid=$(cat /tmp/.X0-lock)',
    '  fi',
])
assert f.count(old) == 1
f = f.replace(old, new)
old = '/bin/sh NVIDIA-Linux-x86_64*.run --no-precompiled-interface --run-nvidia-xconfig --accept-license --silent'
new = '/bin/sh NVIDIA-Linux-x86_64-595.91.07-grid-aws.run --no-precompiled-interface --run-nvidia-xconfig --accept-license --silent || return $?'
assert f.count(old) == 1
f = f.replace(old, new)
old = '$NVIDIAXCONFIG --preserve-busid --enable-all-gpus'
new = '$NVIDIAXCONFIG --preserve-busid --enable-all-gpus || return $?'
assert f.count(old) == 1
f = f.replace(old, new)
p.write_text(s[:a] + f + s[b:])
'''

def runstep(name, body):
    return dict(name=name, action="ExecuteBash", onFailure="Abort", inputs={"commands":[body]})

def reboot(name):
    return dict(name=name, action="Reboot", onFailure="Abort", inputs={"delaySeconds":15})

def generate(doc):
    doc = copy.deepcopy(doc)
    assert doc["name"] == "studio-gpu-nvidia-res-stage"
    steps = doc["phases"][0]["steps"]
    assert [x["name"] for x in steps] == ["InstallResNvidiaGrid", "RebootAfterNvidia", "ConfirmNvidia"]
    pre = f"""set -euxo pipefail
export DEBIAN_FRONTEND=noninteractive NEEDRESTART_MODE=a NEEDRESTART_SUSPEND=1
test "$(uname -m)" = x86_64
lspci | grep -qi NVIDIA
apt-get update -o Acquire::Retries=3
apt-get install -y mesa-utils gcc make binutils pciutils "linux-headers-$(uname -r)"
dpkg --audit
mkdir -p {ROOT} /root/bootstrap/logs
aws s3 cp {ARCHIVE} /tmp/res-installation-scripts.tar.gz --region us-east-1
echo '{ARCHIVE_SHA}  /tmp/res-installation-scripts.tar.gz' | sha256sum -c -
tar -xzf /tmp/res-installation-scripts.tar.gz -C {ROOT}
test -f {ROOT}/scripts/common/linux/debian/grub_configuration.sh
test -f {ROOT}/scripts/dcv/linux/gpu_drivers.sh
RES_SCRIPTS={BASE}
/bin/bash {ROOT}/scripts/common/linux/yq.sh -s "$RES_SCRIPTS"
/bin/bash {ROOT}/scripts/common/linux/jq.sh -o ubuntu2404 -s "$RES_SCRIPTS"
command -v yq
command -v jq
source {ROOT}/scripts/common/linux/config_common.sh
test "$(get_string package_config.gpu_driver.nvidia.s3_bucket_path)" = "s3://ec2-linux-nvidia-drivers/latest/"
/bin/bash {ROOT}/scripts/common/linux/disable_nouveau_drivers.sh -o ubuntu2404 -g NVIDIA -s "$RES_SCRIPTS"
test "$(cat /root/bootstrap/reboot_required.txt)" = yes
update-initramfs -u -k "$(uname -r)"
"""
    install = f"""set -euxo pipefail
export DEBIAN_FRONTEND=noninteractive NEEDRESTART_MODE=a NEEDRESTART_SUSPEND=1
if lsmod | awk '$1 == "nouveau" {{found=1}} END {{exit !found}}'; then
  echo 'ERROR: nouveau remains loaded after reboot' >&2
  exit 42
fi
command -v aws
command -v python3
command -v yq
command -v jq
test -f {ROOT}/scripts/dcv/linux/gpu_drivers.sh
python3 - <<'PATCH'
{PATCH}PATCH
/bin/bash {ROOT}/scripts/dcv/linux/gpu_drivers.sh -o ubuntu2404 -g NVIDIA -s {BASE}
command -v nvidia-smi
command -v nvidia-xconfig
"""
    doc["phases"][0]["steps"] = [
        runstep("PrepareResAndDisableNouveau", pre),
        reboot("RebootAfterNouveau"),
        runstep("InstallResNvidiaGrid", install),
        reboot("RebootAfterNvidia"),
        steps[2],
    ]
    doc["description"] = "RES 2026.06 NVIDIA GRID with Nouveau reboot and pinned verified driver"
    return doc

def validate(doc, archive=None):
    steps = doc["phases"][0]["steps"]
    assert [s["name"] for s in steps] == [
        "PrepareResAndDisableNouveau", "RebootAfterNouveau",
        "InstallResNvidiaGrid", "RebootAfterNvidia", "ConfirmNvidia"]
    for step in steps:
        if step["action"] != "ExecuteBash":
            continue
        with tempfile.NamedTemporaryFile(mode="w", suffix=".sh") as f:
            f.write(step["inputs"]["commands"][0])
            f.flush()
            subprocess.run(["bash", "-n", f.name], check=True)
    if archive:
        assert hashlib.sha256(archive.read_bytes()).hexdigest() == ARCHIVE_SHA
        with tarfile.open(archive, "r:gz") as tf:
            member = next(m for m in tf if m.name.removeprefix("./") == "scripts/dcv/linux/gpu_drivers.sh")
            original = tf.extractfile(member).read().decode()
        with tempfile.TemporaryDirectory() as td:
            target = Path(td) / "gpu_drivers.sh"
            target.write_text(original)
            local = PATCH.replace('Path("/root/bootstrap/latest/scripts/dcv/linux/gpu_drivers.sh")', f'Path({str(target)!r})')
            subprocess.run(["python3", "-c", local], check=True)
            subprocess.run(["bash", "-n", str(target)], check=True)
            patched = target.read_text()
            assert 'local x_server_pid=""' + chr(10) + '  if [[ -f /tmp/.X0-lock ]]; then' in patched
            assert DRIVER_SHA in patched and VERSION_ID in patched
            assert patched.count("|| return $?") == 2
    print("PASS: RES patch, shell syntax, stage order and pinned driver")

def main():
    p = argparse.ArgumentParser()
    p.add_argument("--source", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--res-archive", type=Path)
    args = p.parse_args()
    doc = generate(yaml.safe_load(args.source.read_text()))
    validate(doc, args.res_archive)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(yaml.safe_dump(doc, sort_keys=False, width=120))
    print("REVIEW ONLY:", args.output)

if __name__ == "__main__":
    main()
