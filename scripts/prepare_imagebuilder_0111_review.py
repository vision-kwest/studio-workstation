#!/usr/bin/env python3
"""Read-only live Image Builder 0.1.10 -> reviewable 0.1.11/1 inputs.

Requires boto3 and PyYAML. Does not call any AWS write API.
"""
import argparse
import copy
import json
import re
from pathlib import Path

import boto3
import yaml

REGION = "us-east-1"
ACCOUNT = "401233741942"
BASE = f"arn:aws:imagebuilder:{REGION}:{ACCOUNT}"
RECIPE = BASE + ":image-recipe/studio-gpu-ubuntu2404-dcv/0.1.10"
RELEASE = "s3://studio-workstation-releases-401233741942-us-east-1/releases/task7-pr61-postreboot-a8ec326/res-2026.06/installation-scripts.tar.gz"
ARCHIVE_SHA = "7ca921aa8a493d9399918f032b62f7e0e37ce7754fa3f1a2c805047507ecd7a1"
OLD_RELEASE = "s3://studio-workstation-releases-401233741942-us-east-1/releases/v0.1.3/res-2026.06/installation-scripts.tar.gz"
OLD_SHA = "813259748335482b1877192a61a008b32e613038ee831305d9e4767c51c23ab7"
VERSIONS = {
    "studio-gpu-nvidia-res-stage": ("0.1.6", "0.1.7"),
    "studio-gpu-dcv-res-stage": ("0.1.3", "0.1.4"),
    "studio-gpu-dcv-smoke-test-res": ("0.1.1", "0.1.2"),
}

def replace_once(value, old, new):
    if value.count(old) != 1:
        raise ValueError("Expected one source anchor: " + old[:100])
    return value.replace(old, new)

def command(doc):
    phases = doc["phases"]
    if len(phases) != 1:
        raise ValueError("Unexpected component phase count")
    steps = phases[0]["steps"]
    selected = [s for s in steps if s["action"] == "ExecuteBash"]
    return selected

def transform(name, data):
    doc = yaml.safe_load(data)
    steps = command(doc)
    if name == "studio-gpu-nvidia-res-stage":
        if [s["name"] for s in steps] != ["PrepareResAndDisableNouveau", "InstallResNvidiaGrid", "ConfirmNvidia"]:
            raise ValueError("NVIDIA steps drifted")
        s = steps[0]["inputs"]["commands"][0]
        s = replace_once(s, OLD_RELEASE, RELEASE)
        s = replace_once(s, OLD_SHA, ARCHIVE_SHA)
        steps[0]["inputs"]["commands"][0] = s
    elif name == "studio-gpu-dcv-res-stage":
        if [s["name"] for s in steps] != ["InstallDCV"]:
            raise ValueError("DCV steps drifted")
        s = steps[0]["inputs"]["commands"][0]
        pattern = r"(?m)^printf 'Section \"Extensions\".*' > /etc/X11/xorg.conf.d/99-disable-dpms.conf$"
        corrected = """printf '%s\\n' 'Section "Extensions"' ' Option "DPMS" "Disable"' 'EndSection' > /etc/X11/xorg.conf.d/99-disable-dpms.conf"""
        s, n = re.subn(pattern, lambda _: corrected, s)
        if n != 1:
            raise ValueError("DPMS anchor changed")
        steps[0]["inputs"]["commands"][0] = s
    elif name == "studio-gpu-dcv-smoke-test-res":
        if [s["name"] for s in steps] != ["CheckDesktop"]:
            raise ValueError("Smoke steps drifted")
        steps[0]["inputs"]["commands"][0] = """set -euxo pipefail
systemctl is-enabled gdm3
systemctl is-enabled dcvserver
systemctl is-active gdm3
systemctl is-active dcvserver
dpkg-query -W nice-dcv-server nice-dcv-gl
nvidia-smi -L | grep -E 'Tesla T4|NVIDIA T4|T4'
test -e /dev/nvidia0
modinfo nvidia
test -f /etc/X11/xorg.conf
grep -qi nvidia /etc/X11/xorg.conf
command -v glxinfo
test "$(passwd -S workstation | awk '{print $2}')" = L
# Console, Xhost, GLX rendering and READY are tested after RES postlaunch initialization.
"""
    else:
        raise ValueError("Unknown component " + name)
    for step in steps:
        for shell in step["inputs"]["commands"]:
            if "\\n      if [[" in shell:
                raise ValueError("Literal newline injection found")
    doc["description"] = doc.get("description", "") + " — Image Builder 0.1.11 review"
    return doc

def generate(client, output):
    current = client.get_image_recipe(imageRecipeArn=RECIPE)["imageRecipe"]
    ordered = [c["componentArn"] for c in current["components"]]
    if len(ordered) != 6 or not ordered[0].endswith("/studio-cpu-gdm-first/0.5.0/1") or not ordered[1].endswith("/studio-gpu-desktop-res-stage/0.1.2/1"):
        raise ValueError("Recipe ordering changed")
    new_arns = []
    manifest = {"source_recipe": RECIPE, "parent_image": current["parentImage"], "components": []}
    for arn in ordered:
        name = arn.split("/")[-3]
        if name not in VERSIONS:
            new_arns.append(arn)
            manifest["components"].append({"source": arn, "proposed": arn, "unchanged": True})
            continue
        old_version, new_version = VERSIONS[name]
        if not arn.endswith(f"/{name}/{old_version}/1"):
            raise ValueError("Unexpected component version " + arn)
        original = client.get_component(componentBuildVersionArn=arn)["component"]["data"]
        doc = transform(name, original)
        path = output / (name + "-" + new_version + ".yaml")
        path.write_text(yaml.safe_dump(doc, sort_keys=False))
        target = f"{BASE}:component/{name}/{new_version}/1"
        new_arns.append(target)
        manifest["components"].append({"source": arn, "proposed": target, "file": path.name})
    if current["parentImage"] != "ami-0d6dd9e8265d48906":
        raise ValueError("Parent AMI changed")
    recipe = {
        "name": "studio-gpu-ubuntu2404-dcv",
        "semanticVersion": "0.1.11",
        "parentImage": current["parentImage"],
        "components": [{"componentArn": arn} for arn in new_arns],
        "blockDeviceMappings": current.get("blockDeviceMappings", []),
        "description": "GPU 0.1.11 candidate; preserve RES launch-time console lifecycle",
    }
    (output / "image-recipe-0.1.11.json").write_text(json.dumps(recipe, indent=2) + "\n")
    (output / "source-manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    return manifest

def main():
    p = argparse.ArgumentParser()
    p.add_argument("--output-dir", type=Path, required=True)
    args = p.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    manifest = generate(boto3.client("imagebuilder", region_name=REGION), args.output_dir)
    print(json.dumps(manifest, indent=2))
    print("Review only: no AWS resource was registered or built.")

if __name__ == "__main__":
    main()
