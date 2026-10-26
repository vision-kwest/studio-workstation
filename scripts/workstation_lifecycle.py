#!/usr/bin/env python3
"""Explicit stop/start of the existing workstation; no implicit resource creation."""
import argparse
import json
import subprocess

REGION="us-east-1"
def aws(*args):
    return subprocess.check_output(["aws",*args,"--region",REGION,"--output","json"],text=True)
def main():
    p=argparse.ArgumentParser()
    p.add_argument("action",choices=["status","stop","start"])
    p.add_argument("--stack",default="studio-workstation-cpu-v011")
    p.add_argument("--confirm-instance",help="Required for stop/start; must match resolved instance ID")
    a=p.parse_args()
    r=json.loads(aws("cloudformation","describe-stack-resource","--stack-name",a.stack,"--logical-resource-id","Workstation"))
    instance=r["StackResourceDetail"]["PhysicalResourceId"]
    current=json.loads(aws("ec2","describe-instances","--instance-ids",instance))["Reservations"][0]["Instances"][0]
    print(json.dumps({"instance":instance,"state":current["State"]["Name"],"public_ip":current.get("PublicIpAddress")},indent=2))
    if a.action=="status":return
    if a.confirm_instance!=instance:
        p.error("Refusing lifecycle mutation: --confirm-instance must equal "+instance)
    if a.action=="stop":
        print(aws("ec2","stop-instances","--instance-ids",instance))
        subprocess.check_call(["aws","ec2","wait","instance-stopped","--instance-ids",instance,"--region",REGION])
    else:
        print(aws("ec2","start-instances","--instance-ids",instance))
        subprocess.check_call(["aws","ec2","wait","instance-status-ok","--instance-ids",instance,"--region",REGION])
    print("Lifecycle action completed; recheck SSM, guest DCV and public IP. No browser login is implied.")
if __name__=="__main__":main()
