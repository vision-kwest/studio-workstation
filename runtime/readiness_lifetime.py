"""Native SSM readiness handoff for one CloudFormation-owned stop schedule."""
import json
import subprocess
import time
from pathlib import Path

SETUP_MINUTES = 60
RESOURCES = {"ReadinessLifetimeDocument": "AWS::SSM::Document",
             "ReadinessLifetimeRole": "AWS::IAM::Role",
             "ReadinessLifetimeAssociation": "AWS::SSM::Association"}


def managed(selected):
    found = {r["LogicalResourceId"]: r["ResourceType"] for r in selected["resources"]
             if r["LogicalResourceId"] in RESOURCES}
    if found and found != RESOURCES:
        raise ValueError("Incomplete server-side lifetime resources")
    return bool(found)


def checks(gpu):
    checks = ["set -eu", "test -f /root/bootstrap/semaphore/linux_vdi_config_host_ready.lock",
              "systemctl is-active gdm3 >/dev/null", "systemctl is-active dcvserver >/dev/null",
              "dcv describe-session console --json | python3 -c 'import json,sys; s=json.load(sys.stdin); assert s.get(\"id\")==\"console\" and s.get(\"owner\")==\"workstation\" and s.get(\"status\")==\"running\"'",
              "curl -k -fsS --connect-timeout 5 --max-time 10 https://127.0.0.1:8443/ >/dev/null"]
    if gpu:
        checks += ["nvidia-smi -L | grep -q 'Tesla T4'", "pgrep -x Xorg >/dev/null",
                   "DISPLAY=:0 XAUTHORITY=/run/user/119/gdm/Xauthority __GLX_VENDOR_LIBRARY_NAME=nvidia glxinfo -B | grep -q 'OpenGL renderer string: Tesla T4'"]
    checks += [
        "systemctl is-active studio-software-setup.service >/dev/null",
        "test \"$(systemctl show studio-software-setup.service -p SubState --value)\" = exited",
        "test \"$(systemctl show studio-software-setup.service -p Result --value)\" = success",
        "test \"$(systemctl show studio-software-setup.service -p ExecMainStatus --value)\" = 0",
        "findmnt -rn -t nfs4 -O rw --mountpoint /opt/studio/packages >/dev/null",
        "test \"$(readlink /studio)\" = /opt/studio/packages/shared",
        "desktop_user=$(getent passwd 2000 | cut -d: -f1)",
        "desktop_home=$(getent passwd 2000 | cut -d: -f6)",
        "test \"$(readlink \"$desktop_home/.local/share/applications\")\" = /studio/share/applications",
        "runuser -u \"$desktop_user\" -- python3 -c 'import os,tempfile; assert all(os.access(p,os.R_OK|os.W_OK|os.X_OK) for p in (\"/studio/apps\",\"/studio/models\",\"/studio/projects\")); f=tempfile.TemporaryFile(dir=\"/studio/projects\"); f.write(b\"ready\"); f.seek(0); assert f.read()==b\"ready\"; f.close()'",
        "echo STUDIO_WORKSTATION_READY"]
    return checks


def commands(gpu):
    return ["set -eu", "limit=$(( $(date -u +%s) + 600 ))",
            "deadline=$(date -u -d '{{ SetupDeadline }} UTC' +%s)",
            "if [ \"$deadline\" -lt \"$limit\" ]; then limit=$deadline; fi",
            "while [ \"$(date -u +%s)\" -lt \"$limit\" ]; do",
            "if bash -eu <<'STUDIO_READY'", *checks(gpu), "STUDIO_READY", "then exit 0; fi",
            "sleep 15", "done", "echo 'Workstation setup is not ready' >&2", "exit 1"]


def resources(token, stop_role, setup, profile):
    """No guest privileges or custom worker: CFN owns the document, role and trigger."""
    name = "studio-ready-" + token
    role = "arn:aws:iam::401233741942:role/" + name
    definition = "arn:aws:ssm:us-east-1:401233741942:automation-definition/" + name + ":*"
    payload = {"InstanceId": "{{ InstanceId }}", "ScheduleName": "studio-stop-" + token,
               "StopRole": stop_role, "StackId": {"Ref": "AWS::StackId"}, "CleanupToken": token,
               "SetupDescription": "{{ SetupDescription }}", "SetupDeadline": "{{ SetupDeadline }}"}
    source = Path(__file__).with_name("readiness_lifetime_handler.py").read_text()
    def action(operation):
        return {"Runtime": "python3.11", "Handler": "handle", "Script": source,
                "InputPayload": {**payload, "Operation": operation}}
    document = {"schemaVersion": "0.3", "description": "Start the selected lifetime after workstation readiness",
                "assumeRole": "{{ AutomationAssumeRole }}",
                "parameters": {key: {"type": "String"} for key in
                               ("InstanceId", "AutomationAssumeRole", "SetupDescription", "SetupDeadline")},
                "mainSteps": [{"name": "awaitReadiness", "action": "aws:loop", "inputs": {
                    "MaxIterations": 12, "LoopCondition": {"Variable": "{{ validateSetup.Done }}", "BooleanEquals": False}, "Steps": [
        {"name": "validateSetup", "action": "aws:executeScript", "timeoutSeconds": 30, "inputs": action("verify"),
         "outputs": [{"Name": "Done", "Selector": "$.Payload.Done", "Type": "Boolean"}]},
        {"name": "alreadyReady", "action": "aws:branch", "inputs": {
            "Choices": [{"Variable": "{{ validateSetup.Done }}", "BooleanEquals": True, "NextStep": "done"}],
            "Default": "waitRunning"}},
        {"name": "waitRunning", "action": "aws:waitForAwsResourceProperty", "timeoutSeconds": 300,
         "onFailure": "step:retryDelay", "isCritical": False, "inputs": {
            "Service": "ec2", "Api": "DescribeInstances", "InstanceIds": ["{{ InstanceId }}"],
            "PropertySelector": "$.Reservations[0].Instances[0].State.Name", "DesiredValues": ["running"]}},
        {"name": "waitOnline", "action": "aws:waitForAwsResourceProperty", "timeoutSeconds": 300,
         "onFailure": "step:retryDelay", "isCritical": False, "inputs": {
            "Service": "ssm", "Api": "DescribeInstanceInformation",
            "Filters": [{"Key": "InstanceIds", "Values": ["{{ InstanceId }}"]}],
            "PropertySelector": "$.InstanceInformationList[0].PingStatus", "DesiredValues": ["Online"]}},
        {"name": "checkReady", "action": "aws:runCommand", "timeoutSeconds": 700,
         "onFailure": "step:retryDelay", "isCritical": False, "nextStep": "handoff", "inputs": {
            "DocumentName": "AWS-RunShellScript", "InstanceIds": ["{{ InstanceId }}"], "TimeoutSeconds": 30,
            "Parameters": {"commands": commands(profile == "gpu"), "executionTimeout": ["630"]}}},
        {"name": "retryDelay", "action": "aws:sleep", "inputs": {"Duration": "PT300S"}}]}},
        {"name": "exhausted", "action": "aws:executeScript", "timeoutSeconds": 30, "isEnd": True, "inputs": {
            "Runtime": "python3.11", "Handler": "fail", "Script": "def fail(event, context):\n    raise RuntimeError('Workstation did not become ready; setup stop remains armed')"}},
        {"name": "handoff", "action": "aws:executeScript", "timeoutSeconds": 30, "inputs": action("handoff"), "isEnd": True},
        {"name": "done", "action": "aws:sleep", "inputs": {"Duration": "PT1S"}, "isEnd": True}]}
    document["parameters"]["SetupDeadline"]["allowedPattern"] = r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}$"
    statements = [
        {"Effect": "Allow", "Action": ["ec2:DescribeInstances", "ssm:DescribeInstanceInformation",
                                         "ssm:GetCommandInvocation", "ssm:ListCommands", "ssm:ListCommandInvocations"], "Resource": "*"},
        {"Effect": "Allow", "Action": "ssm:SendCommand", "Resource": [
            "arn:aws:ssm:us-east-1::document/AWS-RunShellScript",
            {"Fn::Sub": "arn:aws:ec2:us-east-1:401233741942:instance/${Workstation}"}]},
        {"Effect": "Allow", "Action": ["scheduler:GetSchedule", "scheduler:UpdateSchedule"],
         "Resource": {"Fn::GetAtt": ["AutoStopSchedule", "Arn"]}},
        {"Effect": "Allow", "Action": "iam:PassRole", "Resource": stop_role,
         "Condition": {"StringEquals": {"iam:PassedToService": "scheduler.amazonaws.com"}}},
        {"Effect": "Allow", "Action": "ssm:StartAutomationExecution", "Resource": definition},
        {"Effect": "Allow", "Action": "iam:PassRole", "Resource": role,
         "Condition": {"StringEquals": {"iam:PassedToService": "ssm.amazonaws.com"}}}]
    return {
        "ReadinessLifetimeDocument": {"Type": "AWS::SSM::Document", "Properties": {
            "Name": name, "DocumentType": "Automation", "DocumentFormat": "JSON", "Content": document}},
        "ReadinessLifetimeRole": {"Type": "AWS::IAM::Role", "Properties": {
            "RoleName": name, "AssumeRolePolicyDocument": {"Version": "2012-10-17", "Statement": [{
                "Effect": "Allow", "Principal": {"Service": "ssm.amazonaws.com"}, "Action": "sts:AssumeRole"}]},
            "Policies": [{"PolicyName": "ReadinessLifetime", "PolicyDocument": {"Version": "2012-10-17", "Statement": statements}}]}},
        "ReadinessLifetimeAssociation": {"Type": "AWS::SSM::Association", "Properties": {
            "Name": {"Ref": "ReadinessLifetimeDocument"}, "DocumentVersion": "$DEFAULT",
            "AssociationDispatchAssumeRole": {"Fn::GetAtt": ["ReadinessLifetimeRole", "Arn"]},
            "AutomationTargetParameterName": "InstanceId", "Targets": [{"Key": "ParameterValues", "Values": [{"Ref": "Workstation"}]}],
            "Parameters": {"AutomationAssumeRole": [role], "SetupDescription": [setup["Description"]],
                           "SetupDeadline": [setup["ScheduleExpression"][3:-1]]}}}}


def submit(aws, selected, setup):
    """Accept the durable operation before starting compute; a client exit cannot lose it."""
    name = "studio-ready-" + selected["token"]
    return aws("us-east-1", "ssm", "start-automation-execution", "--document-name", name,
               "--document-version", "$DEFAULT", "--client-token", setup["Description"].split()[2].rstrip(";"),
               "--parameters", json.dumps({"InstanceId": [selected["instance"]],
                   "AutomationAssumeRole": ["arn:aws:iam::401233741942:role/" + name],
                   "SetupDescription": [setup["Description"]], "SetupDeadline": [setup["ScheduleExpression"][3:-1]]}))


def cancel_pending(aws, selected, *, sleep=time.sleep):
    """Let a manual extension win over an in-flight readiness handoff."""
    name = "studio-ready-" + selected["token"]
    records = aws("us-east-1", "ssm", "describe-automation-executions", "--filters",
                  "Key=DocumentNamePrefix,Values=" + name)["AutomationExecutionMetadataList"]
    terminal = {"Success", "Failed", "TimedOut", "Cancelled", "Rejected", "CompletedWithSuccess", "CompletedWithFailure"}
    for record in records:
        if record["DocumentName"] != name or record["AutomationExecutionStatus"] in terminal:
            continue
        execution = record["AutomationExecutionId"]
        if record["AutomationExecutionStatus"] != "Cancelling":
            try:
                aws("us-east-1", "ssm", "stop-automation-execution", "--automation-execution-id", execution)
            except subprocess.CalledProcessError as exc:
                if "(InvalidAutomationStatusUpdateException)" not in (exc.stderr or ""):
                    raise
                # It may have finished after listing; the readback below must prove that.
        for _ in range(40):
            status = aws("us-east-1", "ssm", "get-automation-execution", "--automation-execution-id", execution)["AutomationExecution"]["AutomationExecutionStatus"]
            if status in terminal:
                break
            sleep(3)
        else:
            raise RuntimeError("Readiness handoff cancellation is unverified; timer was not extended")
