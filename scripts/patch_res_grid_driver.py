#!/usr/bin/env python3
"""Narrow RES 2026.06 GRID download override. No AWS calls at patch time."""
from pathlib import Path
import subprocess
import sys

BUCKET = "studio-workstation-releases-401233741942-us-east-1"
KEY = "dependencies/nvidia-grid/595.91.07/NVIDIA-Linux-x86_64-595.91.07-grid-aws.run"
VERSION = "b94g7u7FkVYD9m63G6RE3xHNLZSXrz3r"
SHA256 = "cae8d82b8407c6371805485b148aa667550abf1775ea7999be69da81cf593156"
FILENAME = "NVIDIA-Linux-x86_64-595.91.07-grid-aws.run"

def replace_once(text, old, new):
    if text.count(old) != 1:
        raise ValueError(f"RES upstream anchor mismatch: {old[:90]!r}")
    return text.replace(old, new)

def patch(source):
    begin = source.index("function install_nvidia_grid_drivers () {")
    end = source.index("function install_nvidia_public_drivers()", begin)
    part = source[begin:end]
    original = "$AWS --region ${DRIVER_BUCKET_REGION} s3 cp --quiet --recursive $NVIDIA_S3_BUCKET_PATH ."
    replacement = (
        '  "$AWS" --region us-east-1 s3api get-object '
        f'--bucket {BUCKET} --key {KEY} --version-id {VERSION} {FILENAME}\n'
        f"  echo '{SHA256}  {FILENAME}' | sha256sum -c -"
    )
    part = replace_once(part, original, replacement)
    part = replace_once(
        part,
        "local x_server_pid=$(cat /tmp/.X0-lock)",
        'local x_server_pid=""\n  if [[ -f /tmp/.X0-lock ]]; then\n'
        '    x_server_pid=$(cat /tmp/.X0-lock)\n  fi'
    )
    part = replace_once(
        part,
        "/bin/sh NVIDIA-Linux-x86_64*.run --no-precompiled-interface --run-nvidia-xconfig --accept-license --silent",
        f"/bin/sh {FILENAME} --no-precompiled-interface --run-nvidia-xconfig --accept-license --silent"
    )
    result = source[:begin] + part + source[end:]
    if result.count("s3api get-object") != 1:
        raise ValueError("Expected exactly one pinned download")
    if "set_reboot_required \"Installed NVIDIA Grid Driver\"" not in result:
        raise ValueError("RES reboot marker missing")
    proc = subprocess.run(["bash", "-n"], input=result, text=True, capture_output=True)
    if proc.returncode:
        raise ValueError("Patched RES shell syntax invalid: " + proc.stderr)
    return result

def main():
    if len(sys.argv) != 2:
        raise SystemExit("usage: patch_res_grid_driver.py PATH_TO_GPU_DRIVERS_SH")
    path = Path(sys.argv[1])
    original = path.read_text()
    result = patch(original)
    path.write_text(result)
    print("RES GRID pinned override applied; bash -n passed")

if __name__ == "__main__":
    main()
