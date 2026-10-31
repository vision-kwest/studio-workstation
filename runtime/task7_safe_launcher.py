#!/usr/bin/env python3
"""Fail-closed Task 7 one-instance launcher. Never used by CI to launch."""
import argparse
import datetime as dt
import hashlib
import json
import re
import time

AMI = "ami-084caad6c89407d25"
REGION = "us-east-1"
ROLE = "arn:aws:iam::401233741942:role/studio-task7-pr61-terminator"
BUCKET = "studio-workstation-releases-401233741942-us-east-1"
TEMPLATE = "releases/task7-pr61-c73e905/res-2026.06/gpu-template.json"
SUBNET = "subnet-0986937ce71fb97cb"
VPC = "vpc-01bbe5d01838f70b2"
PROFILE = "StudioWorkstationSSMRole"


def validate_template(t):
    p = t["Resources"]["Workstation"]["Properties"]
    assert t["Parameters"]["InstanceType"]["AllowedValues"] == ["g4dn.xlarge"]
    assert t["Parameters"]["ClientCidr"]["Default"] == "127.0.0.1/32"
    assert p["MetadataOptions"]["HttpTokens"] == "required"
    assert p["InstanceInitiatedShutdownBehavior"] == "terminate"
    assert p["BlockDeviceMappings"][0]["Ebs"]["DeleteOnTermination"] is True
    tags = {v["Key"]: v["Value"] for v in p["Tags"]}
    assert tags["Task7"] == "PR61" and tags["Issue"] == "60"
    u = p["UserData"]["Fn::Base64"]["Fn::Sub"]
    assert "RENDER_OFFLINE" not in u and len(u.encode()) <= 16384
    assert "installation-scripts.tar.gz" in u and "virtual-desktop.tar.gz" in u
    assert "releases/task7-pr61-c73e905/res-2026.06" in u
    assert "STUDIO_STANDALONE=true" in u and "-g NVIDIA" in u
    assert not re.search(r"\$\{(?!AWS::Region|![^}]+\})[^}]+\}", u)
    return p


def resolve_userdata(u):
    # CloudFormation Fn::Sub: pseudo-parameter and escaped shell variables only.
    u = u.replace("${AWS::Region}", REGION)
    return re.sub(r"\$\{!([^}]+)\}", lambda m: "${" + m.group(1) + "}", u)


def terminate(ec2, instance_id):
    ec2.terminate_instances(InstanceIds=[instance_id])


def launch(ec2, scheduler, t, minutes=90):
    p = validate_template(t)
    if not 15 <= minutes <= 120:
        raise ValueError("deadline must be 15-120 minutes")
    sg = ec2.create_security_group(GroupName="studio-task7-pr61-" + str(int(time.time())),
                                  Description="Task 7 closed DCV test; no ingress",
                                  VpcId=VPC, TagSpecifications=[{"ResourceType":"security-group",
                                  "Tags":[{"Key":"Task7","Value":"PR61"},{"Key":"Issue","Value":"60"}]}])
    sgid = sg["GroupId"]
    instance_id = None
    schedule_name = None
    try:
        # No inbound ingress; SSM outbound via default VPC internet gateway.
        resp = ec2.run_instances(ImageId=AMI, InstanceType="g4dn.xlarge", MinCount=1, MaxCount=1,
            SubnetId=SUBNET, SecurityGroupIds=[sgid],
            IamInstanceProfile={"Name":PROFILE},
            MetadataOptions={"HttpTokens":"required","HttpEndpoint":"enabled","HttpPutResponseHopLimit":2},
            InstanceInitiatedShutdownBehavior="terminate",
            BlockDeviceMappings=[{"DeviceName":"/dev/sda1","Ebs":{"VolumeSize":100,"VolumeType":"gp3",
                                "Encrypted":True,"DeleteOnTermination":True}}],
            UserData=resolve_userdata(p["UserData"]["Fn::Base64"]["Fn::Sub"]),
            TagSpecifications=[{"ResourceType":"instance","Tags":p["Tags"]}])
        instance_id = resp["Instances"][0]["InstanceId"]
        schedule_name = "studio-task7-pr61-" + instance_id
        deadline = dt.datetime.now(dt.timezone.utc) + dt.timedelta(minutes=minutes)
        schedule = scheduler.create_schedule(
            Name=schedule_name, GroupName="default", State="ENABLED",
            ScheduleExpression="at(" + deadline.strftime("%Y-%m-%dT%H:%M:%S") + ")",
            ScheduleExpressionTimezone="UTC",
            FlexibleTimeWindow={"Mode":"OFF"}, ActionAfterCompletion="DELETE",
            Target={"Arn":"arn:aws:scheduler:::aws-sdk:ec2:terminateInstances",
                    "RoleArn":ROLE, "Input":json.dumps({"InstanceIds":[instance_id]}),
                    "RetryPolicy":{"MaximumRetryAttempts":3,"MaximumEventAgeInSeconds":3600}})
        got = scheduler.get_schedule(Name=schedule_name, GroupName="default")
        target = got["Target"]
        if not (got["State"]=="ENABLED" and got["ScheduleExpression"] ==
                "at(" + deadline.strftime("%Y-%m-%dT%H:%M:%S") + ")" and
                got["ScheduleExpressionTimezone"]=="UTC" and target["RoleArn"]==ROLE and
                target["Arn"]=="arn:aws:scheduler:::aws-sdk:ec2:terminateInstances" and
                json.loads(target["Input"])=={"InstanceIds":[instance_id]}):
            raise RuntimeError("termination schedule readback mismatch")
        return {"instance_id":instance_id,"security_group":sgid,
                "schedule":schedule_name,"deadline_utc":deadline.isoformat(),
                "template_sha256":hashlib.sha256(json.dumps(t,sort_keys=True).encode()).hexdigest()}
    except BaseException:
        if instance_id:
            # Do not suppress a failed termination: surface it immediately.
            terminate(ec2,instance_id)
        else:
            ec2.delete_security_group(GroupId=sgid)
        raise


def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--execute",action="store_true",help="Actually create one GPU instance")
    ap.add_argument("--minutes",type=int,default=90)
    a=ap.parse_args()
    import boto3
    s3=boto3.client("s3",region_name=REGION)
    data=s3.get_object(Bucket=BUCKET,Key=TEMPLATE)["Body"].read()
    t=json.loads(data)
    validate_template(t)
    if not a.execute:
        print(json.dumps({"validated":True,"mode":"dry-run","bytes":len(data),
                          "sha256":hashlib.sha256(data).hexdigest(),"launch":"disabled"}))
        return
    ec2=boto3.client("ec2",region_name=REGION)
    scheduler=boto3.client("scheduler",region_name=REGION)
    print(json.dumps(launch(ec2,scheduler,t,a.minutes)))


if __name__=="__main__":
    main()
