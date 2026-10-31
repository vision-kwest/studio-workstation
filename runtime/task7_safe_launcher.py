#!/usr/bin/env python3
"""Task 7 one-instance fail-closed EC2 launcher; not executed by CI."""
import argparse
import datetime as dt
import json
import sys
import time

AMI = "ami-084caad6c89407d25"
REGION = "us-east-1"
ROLE = "arn:aws:iam::401233741942:role/studio-task7-pr61-terminator"
TEMPLATE = "releases/task7-pr61-c73e905/res-2026.06/gpu-template.json"
BUCKET = "studio-workstation-releases-401233741942-us-east-1"


def validate_template(t):
    p = t["Resources"]["Workstation"]["Properties"]
    assert t["Parameters"]["InstanceType"]["AllowedValues"] == ["g4dn.xlarge"]
    assert t["Parameters"]["ClientCidr"]["Default"] == "127.0.0.1/32"
    assert p["MetadataOptions"]["HttpTokens"] == "required"
    assert p["InstanceInitiatedShutdownBehavior"] == "terminate"
    assert p["BlockDeviceMappings"][0]["Ebs"]["DeleteOnTermination"] is True
    assert {v["Key"]:v["Value"] for v in p["Tags"]}.items() >= {"Task7":"PR61","Issue":"60"}.items()
    assert "RENDER_OFFLINE" not in json.dumps(t)
    return p


def launch(ec2, scheduler, template, subnet, profile, deadline_minutes=90):
    """RunInstances returns ID before schedule; failure must terminate immediately."""
    p=validate_template(template)
    assert deadline_minutes >= 15 and deadline_minutes <= 120
    assert subnet == "subnet-0986937ce71fb97cb"
    assert profile == "StudioWorkstationSSMRole"
    # Caller supplies an existing SG with no inbound access; network is closed during test.
    raise NotImplementedError("Launch requires validated temporary security group and EC2 user-data encoding")


def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--dry-run",action="store_true",required=True)
    a=ap.parse_args()
    import boto3
    s3=boto3.client("s3",region_name=REGION)
    data=s3.get_object(Bucket=BUCKET,Key=TEMPLATE)["Body"].read()
    t=json.loads(data)
    validate_template(t)
    print(json.dumps({"validated":True,"mode":"dry-run","bytes":len(data),"launch":"disabled"}))


if __name__=="__main__":
    main()
