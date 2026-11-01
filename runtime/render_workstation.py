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
PINNED_GPU_PREFIX="releases/pr65-pinned-download-084e704e/res-2026.06"
PINNED_GPU_APP_PREFIX="releases/task7-pr61-postreboot-a8ec326/res-2026.06"
PINNED_GPU_BUCKET="studio-workstation-releases-401233741942-us-east-1"
GPU_ARCHIVES={
    "installation-scripts.tar.gz": ("bN0To3QzfVqVsN5P_6YmTraGIQuHcrtB", "084e704e5c3e79152b0324664f153a826ced40b878c1a87e31cf62e4f9551bb5"),
    "virtual-desktop.tar.gz": ("nzyYrLhYAQ.OTFNpFE_xSHPaK06T1V53", "e7686d6240db9062b5ea62f94859bd657de8c1231e8a3ba097704f9faa45d147"),
}
def render(bucket,prefix,user,profile="cpu"):
    if not re.fullmatch(r"[a-z0-9.-]{3,63}",bucket): raise ValueError("bucket")
    if not re.fullmatch(r"[A-Za-z0-9_./-]+",prefix) or ".." in prefix: raise ValueError("prefix")
    if not re.fullmatch(r"[a-z_][a-z0-9_-]{0,30}",user): raise ValueError("user")
    if profile not in ("cpu", "gpu"): raise ValueError("profile")
    gpu_family = "NVIDIA" if profile == "gpu" else "NONE"
    pinned = profile == "gpu" and bucket == PINNED_GPU_BUCKET and prefix.strip("/") == PINNED_GPU_PREFIX
    prefix=prefix.strip("/")
    app_prefix = PINNED_GPU_APP_PREFIX if pinned else prefix
    app="s3://"+bucket+"/"+app_prefix+"/virtual-desktop.tar.gz"
    boot="s3://"+bucket+"/"+prefix+"/installation-scripts.tar.gz"
    command="""set -e
if ! id -u USERNAME >/dev/null 2>&1; then
  useradd --create-home --shell /bin/bash USERNAME
  passwd -l USERNAME
fi
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
/bin/bash scripts/virtual-desktop-host/linux/install.sh -p false -m vdi-app -g GPUFAMILY -e standalone -n standalone -o USERNAME -d CONSOLE -i standalone-console -a REGION
""".replace("USERNAME",user).replace("APPURI",app).replace("REGION",REGION).replace("GPUFAMILY",gpu_family).replace(
        "APP_VERSION_LINE", "STUDIO_APP_VERSION_ID="+GPU_ARCHIVES["virtual-desktop.tar.gz"][0] if pinned else "").replace(
        "APP_SHA_LINE", "STUDIO_APP_SHA256="+GPU_ARCHIVES["virtual-desktop.tar.gz"][1] if pinned else "")
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
    p.add_argument("--lifecycle",choices=["temporary","persistent"],default="temporary",help="GPU only; persistent enables stop/start")
    p.add_argument("--cleanup-token",default=None)
    p.add_argument("--test-stack",default=None)
    p.add_argument("--bucket",required=True)
    p.add_argument("--prefix",required=True)
    p.add_argument("--user",default="workstation")
    p.add_argument("--output",type=Path,required=True)
    a=p.parse_args()
    template=json.loads((ROOT/"workstation-template.json").read_text())
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
            instance["BlockDeviceMappings"][0]["Ebs"]["DeleteOnTermination"] = False
        else:
            instance["Tags"].extend([{"Key":"Task7","Value":"PR61"},{"Key":"Issue","Value":"60"}])
        if a.cleanup_token:
            if a.lifecycle != "persistent" or not a.test_stack or not re.fullmatch(r"[a-z0-9-]{12,64}", a.cleanup_token):
                raise ValueError("cleanup scope requires persistent GPU and exact test stack")
            tags = [{"Key":"StudioCleanupToken","Value":a.cleanup_token},
                    {"Key":"StudioStack","Value":a.test_stack}]
            instance["Tags"].extend(tags)
            template["Resources"]["WorkstationSG"]["Properties"]["Tags"] = list(tags)
            # EC2 instance tags do NOT automatically tag its root EBS volume.
            # Launch-time volume TagSpecifications persist even if EC2 terminates
            # before the first independent cleanup invocation.
            template["Resources"]["CleanupVolumeTagLaunchTemplate"] = {
                "Type": "AWS::EC2::LaunchTemplate",
                "Properties": {
                    "LaunchTemplateData": {
                        "TagSpecifications": [{
                            "ResourceType": "volume",
                            "Tags": list(tags),
                        }]
                    },
                    "TagSpecifications": [{
                        "ResourceType": "launch-template",
                        "Tags": list(tags),
                    }],
                },
            }
            instance["LaunchTemplate"] = {
                "LaunchTemplateId": {"Ref": "CleanupVolumeTagLaunchTemplate"},
                "Version": {"Fn::GetAtt": ["CleanupVolumeTagLaunchTemplate", "LatestVersionNumber"]},
            }
    elif a.cleanup_token:
        raise ValueError("cleanup scope only supported for GPU")
    elif a.lifecycle != "temporary":
        raise ValueError("persistent lifecycle is only supported for GPU")
    template["Resources"]["Workstation"]["Properties"]["UserData"]={"Fn::Base64":{"Fn::Sub":render(a.bucket,a.prefix,a.user,a.profile)}}
    a.output.parent.mkdir(parents=True,exist_ok=True)
    a.output.write_text(json.dumps(template,indent=2)+"\n")
    print("Generated reviewable CloudFormation:",a.output)
if __name__=="__main__": main()
