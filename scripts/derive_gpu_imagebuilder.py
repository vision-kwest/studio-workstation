#!/usr/bin/env python3
"""Generate a review-only GPU Image Builder configuration from a live CPU audit.

No AWS writes. Preserve the original CPU recipe and component documents.
The GPU recipe is intentionally blocked until NVIDIA/GL stages are validated.
"""
import argparse
import copy
import json
from pathlib import Path

def generate(audit):
    pipeline=audit["pipeline"]
    infra=audit["infrastructure"]
    dist=audit["distribution"]
    recipe=audit["recipe"]
    if recipe.get("name")!="studio-cpu-ubuntu2404-dcv" or recipe.get("version")!="0.5.0":
        raise ValueError("Expected validated CPU recipe 0.5.0")
    expected=["studio-cpu-gdm-first","studio-cpu-desktop-res-stage",
              "studio-cpu-dcv-res-stage","studio-cpu-dcv-smoke-test-res"]
    actual=[c["name"] for c in audit["components"]]
    if actual!=expected:
        raise ValueError(f"Unexpected component order: {actual}")
    if infra.get("instanceTypes")!=["m7i.xlarge"]:
        raise ValueError("Unexpected CPU build instance type")
    if not dist or len(dist.get("distributions",[]))!=1:
        raise ValueError("Expected one existing distribution")
    gpu_infra={
        "name":"studio-gpu-image-builder",
        "description":"Separate GPU g4dn Image Builder infrastructure derived from validated CPU 0.5.0",
        "instanceTypes":["g4dn.xlarge"],
        "instanceProfileName":infra["instanceProfileName"],
        "terminateInstanceOnFailure":infra.get("terminateInstanceOnFailure",True),
    }
    # Copy explicit networking and logging settings only; never copy immutable ARN or dates.
    for field in ("subnetId","securityGroupIds","keyPair","logging",
                  "snsTopicArn","resourceTags","instanceMetadataOptions"):
        if field in infra: gpu_infra[field]=copy.deepcopy(infra[field])
    gpu_dist={
        "name":"studio-gpu-image-distribution",
        "distributions":copy.deepcopy(dist["distributions"]),
    }
    for d in gpu_dist["distributions"]:
        if d["region"]!="us-east-1": raise ValueError("Unexpected distribution region")
        cfg=d.setdefault("amiDistributionConfiguration",{})
        cfg["name"]="studio-gpu-dcv-{{ imagebuilder:buildDate }}"
        cfg["description"]="Ubuntu 24.04 RES-derived NVIDIA T4 DCV workstation GPU AMI"
    return {
        "status":"REVIEW_ONLY_NOT_DEPLOYABLE",
        "source_pipeline_arn":pipeline["arn"],
        "source_recipe_arn":recipe["arn"],
        "source_parent_ami":recipe["parentImage"],
        "gpu_infrastructure":gpu_infra,
        "gpu_distribution":gpu_dist,
        "component_plan":[
            {"source":actual[0],"action":"reuse pinned CPU 0.5.0 GDM-first component"},
            {"source":actual[1],"action":"derive GPU desktop component; remove dummy Xorg configuration and keep reboot/OS-upgrade guard"},
            {"source":"RES 2026.06 scripts/dcv/linux/gpu_drivers.sh",
             "action":"add GPU driver stage using upstream g4dn GRID branch; validate driver S3 access, kernel headers and reboot"},
            {"source":actual[2],"action":"derive GPU DCV stage; include nice-dcv-gl and original RES GPU Xorg configuration"},
            {"source":actual[3],"action":"derive GPU smoke test; assert nvidia-smi Tesla T4, Xorg NVIDIA renderer and DCV readiness"},
        ],
        "deployment_blockers":[
            "Pinned and verified NVIDIA GRID installer artifact and builder S3 permissions",
            "Review GPU desktop/DCV component YAML and reboot checkpoints",
            "Confirm driver compatibility with parent AMI kernel and Ubuntu 24.04",
            "Create separate GPU recipe and pipeline only after approval",
        ],
    }

def main():
    p=argparse.ArgumentParser()
    p.add_argument("--audit",type=Path,required=True)
    p.add_argument("--output",type=Path,required=True)
    a=p.parse_args()
    result=generate(json.loads(a.audit.read_text()))
    a.output.parent.mkdir(parents=True,exist_ok=True)
    a.output.write_text(json.dumps(result,indent=2)+"\n")
    print("Review-only GPU Image Builder draft:",a.output)
    print("No AWS resources created or modified.")

if __name__=="__main__":
    main()
