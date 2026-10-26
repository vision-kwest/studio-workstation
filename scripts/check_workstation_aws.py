#!/usr/bin/env python3
"""Read-only AWS-side acceptance checks for a deployed standalone workstation."""
import argparse
import json
import subprocess
import sys

def aws(*args):
    return subprocess.check_output(["aws", *args, "--region", "us-east-1", "--output", "json"], text=True)

def main():
    p=argparse.ArgumentParser()
    p.add_argument("--stack",default="studio-workstation-cpu-v011")
    args=p.parse_args()
    resource=json.loads(aws("cloudformation","describe-stack-resource","--stack-name",args.stack,
                            "--logical-resource-id","Workstation"))
    instance=resource["StackResourceDetail"]["PhysicalResourceId"]
    data=json.loads(aws("ec2","describe-instances","--instance-ids",instance))
    vm=data["Reservations"][0]["Instances"][0]
    ssm=json.loads(aws("ssm","describe-instance-information","--filters",f"Key=InstanceIds,Values={instance}"))
    agent=ssm["InstanceInformationList"]
    result={"stack":args.stack,"instance":instance,"state":vm["State"]["Name"],
            "public_ip":vm.get("PublicIpAddress"),"ssm":agent[0]["PingStatus"] if agent else "NotRegistered",
            "dcv_url":f"https://{vm['PublicIpAddress']}:8443" if vm.get("PublicIpAddress") else None}
    print(json.dumps(result,indent=2))
    if result["state"]!="running" or result["ssm"]!="Online":
        print("NOT READY: EC2 must be running and SSM Online",file=sys.stderr)
        return 1
    print("AWS control-plane checks passed; run scripts/check_workstation_guest.sh inside SSM for DCV and desktop checks.")
    return 0

if __name__=="__main__":
    sys.exit(main())
