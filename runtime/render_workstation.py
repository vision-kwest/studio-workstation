#!/usr/bin/env python3
"""Offline CloudFormation renderer using unchanged RES 2026.06 workstation user data."""
import argparse
import json
import re
from pathlib import Path
from jinja2 import Environment, FileSystemLoader, StrictUndefined

ROOT=Path(__file__).resolve().parent
REGION=chr(36)+"{AWS::Region}"
def render(bucket,prefix,user):
    if not re.fullmatch(r"[a-z0-9.-]{3,63}",bucket): raise ValueError("bucket")
    if not re.fullmatch(r"[A-Za-z0-9_./-]+",prefix) or ".." in prefix: raise ValueError("prefix")
    if not re.fullmatch(r"[a-z_][a-z0-9_-]{0,30}",user): raise ValueError("user")
    prefix=prefix.strip("/")
    app="s3://"+bucket+"/"+prefix+"/virtual-desktop.tar.gz"
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
CONFIG
chmod 600 /etc/studio-standalone.env
set -a
source /etc/studio-standalone.env
set +a
/bin/bash scripts/virtual-desktop-host/linux/install.sh -p false -m vdi-app -g NONE -e standalone -n standalone -o USERNAME -d CONSOLE -i standalone-console -a REGION
""".replace("USERNAME",user).replace("APPURI",app).replace("REGION",REGION)
    env=Environment(loader=FileSystemLoader(str(ROOT/"upstream")),undefined=StrictUndefined,keep_trailing_newline=True)
    userdata=env.get_template("bootstrap_userdata_linux_base_non_substitution.sh.jinja2").render(
        base_os="ubuntu2404",aws_region=REGION,bootstrap_package_uri=boot,
        install_commands=[command],infra_config=None,proxy_config=None)
    if len(userdata.encode())>16384: raise ValueError("EC2 user-data exceeds 16KiB")
    return userdata
def main():
    p=argparse.ArgumentParser()
    p.add_argument("--bucket",required=True)
    p.add_argument("--prefix",required=True)
    p.add_argument("--user",default="workstation")
    p.add_argument("--output",type=Path,required=True)
    a=p.parse_args()
    template=json.loads((ROOT/"workstation-template.json").read_text())
    template["Resources"]["Workstation"]["Properties"]["UserData"]={"Fn::Base64":{"Fn::Sub":render(a.bucket,a.prefix,a.user)}}
    template["Resources"]["WorkstationRole"]["Properties"]["Policies"][0]["PolicyDocument"]["Statement"][0]["Resource"]=[
        "arn:aws:s3:::"+a.bucket+"/"+a.prefix.strip("/")+"/"+name for name in
        ("installation-scripts.tar.gz","virtual-desktop.tar.gz")]
    a.output.parent.mkdir(parents=True,exist_ok=True)
    a.output.write_text(json.dumps(template,indent=2)+"\n")
    print("Generated reviewable CloudFormation:",a.output)
if __name__=="__main__": main()
