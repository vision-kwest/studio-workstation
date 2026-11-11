"""Task 4: offline CPU teardown and protected shared-EIP owner tests."""
import contextlib
import copy
import io
import subprocess
import unittest
from unittest import mock

from runtime import issue43_destroy as d
from runtime import issue43_hostname_handoff as h
from scripts import studio_workstation_cli as cli
from test_issue43_hostname_handoff import FakeAWS

STACK = "studio-workstation-cpu-review"
CPU = "i-aaaaaaaaaaaaaaaaa"
VOL = "vol-bbbbbbbbbbbbbbbbb"
SG = "sg-ccccccccccccccccc"
ARN = "arn:aws:cloudformation:us-east-1:" + h.ACCOUNT + ":stack/" + STACK + "/cpu-1234"


class CpuAWS(FakeAWS):
    def __init__(self):
        super().__init__()
        self.cpu["VpcId"] = "vpc-01bbe5d01838f70b2"
        self.cpu["IamInstanceProfile"] = {"Arn": h.BASE_PROFILE}
        self.stack_status = "CREATE_COMPLETE"
        self.deleted_sg = False
        self.deleted_volume = False
        self.stuck_volume = False
        self.moves = []
        self.vm = {"InstanceId": CPU, "State": {"Name": "running"},
                   "InstanceType": "m7i.large",
                   "Tags": [{"Key": "Name", "Value": "studio-workstation-res-cpu"},
                            {"Key": "aws:cloudformation:stack-id", "Value": ARN}],
                   "BlockDeviceMappings": [{"DeviceName": "/dev/sda1",
                                            "Ebs": {"VolumeId": VOL,
                                                    "DeleteOnTermination": False}}]}
        self.disk = {"VolumeId": VOL, "Encrypted": True, "State": "in-use",
                     "Attachments": [{"VolumeId": VOL, "InstanceId": CPU,
                                      "Device": "/dev/sda1"}]}
        self.group = {"GroupId": SG, "Tags": [
            {"Key": "aws:cloudformation:stack-id", "Value": ARN}]}

    def __call__(self, region, *args):
        service, op = args[:2]
        if (service, op) == ("cloudformation", "describe-stacks"):
            return {"Stacks": [{"StackName": STACK, "StackId": ARN,
                                 "StackStatus": self.stack_status}]}
        if (service, op) == ("cloudformation", "list-stack-resources"):
            return {"StackResourceSummaries": [
                {"LogicalResourceId": "Workstation", "ResourceType": "AWS::EC2::Instance",
                 "PhysicalResourceId": CPU, "ResourceStatus": "CREATE_COMPLETE"},
                {"LogicalResourceId": "WorkstationSG", "ResourceType": "AWS::EC2::SecurityGroup",
                 "PhysicalResourceId": SG, "ResourceStatus": "CREATE_COMPLETE"}]}
        if (service, op) == ("ec2", "describe-instances") and CPU in args:
            return {"Reservations": [{"Instances": [copy.deepcopy(self.vm)]}]}
        if (service, op) == ("ec2", "describe-security-groups"):
            if self.deleted_sg:
                raise subprocess.CalledProcessError(254, args, stderr="InvalidGroup.NotFound")
            return {"SecurityGroups": [copy.deepcopy(self.group)]}
        if (service, op) == ("ec2", "describe-volumes"):
            if self.deleted_volume:
                raise subprocess.CalledProcessError(254, args, stderr="InvalidVolume.NotFound")
            return {"Volumes": [copy.deepcopy(self.disk)]}
        return super().__call__(region, *args)

    def run(self, args, *, capture=True):
        self.moves.append(" ".join(args[1:3]))
        if "delete-stack" in args:
            self.stack_status = "DELETE_COMPLETE"
            self.vm["State"]["Name"] = "terminated"
            self.deleted_sg = True
            if not self.stuck_volume:
                self.disk["State"] = "available"
                self.disk["Attachments"] = []
        elif "delete-volume" in args:
            assert self.disk["State"] == "available"
            self.deleted_volume = True
        return ""


class CpuDestroyTests(unittest.TestCase):
    def setUp(self):
        self.cloud = CpuAWS()

    def invoke(self):
        return d.destroy_cpu(self.cloud, self.cloud.run, STACK, h.REGION,
                             sleep=lambda _: None)

    def test_cpu_destroy_removes_retained_disk_and_preserves_shared_eip(self):
        result = self.invoke()
        self.assertEqual(result["state"], "DELETE_COMPLETE")
        self.assertEqual(result["volume_removed"], VOL)
        self.assertTrue(self.cloud.deleted_volume)
        self.assertTrue(self.cloud.deleted_sg)
        self.assertEqual(self.cloud.eip["InstanceId"], h.CPU_ID)
        self.assertLess(self.cloud.moves.index("cloudformation delete-stack"),
                        self.cloud.moves.index("ec2 delete-volume"))

    def test_cpu_delete_does_not_touch_foreign_or_non_encrypted_volume(self):
        self.cloud.disk["Encrypted"] = False
        with self.assertRaisesRegex(ValueError, "root EBS"):
            self.invoke()
        self.assertEqual(self.cloud.moves, [])

    def test_cpu_delete_refuses_wrong_stack_id_on_security_group(self):
        self.cloud.group["Tags"][0]["Value"] = "arn:aws:cloudformation:other"
        with self.assertRaisesRegex(ValueError, "security group"):
            self.invoke()
        self.assertEqual(self.cloud.moves, [])

    def test_cpu_delete_never_leaves_in_use_volume_unreported(self):
        self.cloud.stuck_volume = True
        with self.assertRaisesRegex(RuntimeError, "not safely detached"):
            self.invoke()
        self.assertFalse(self.cloud.deleted_volume)

    def test_cpu_delete_does_not_repurpose_shared_eip(self):
        self.cloud.eip["InstanceId"] = CPU
        with self.assertRaisesRegex(ValueError, "Release shared EIP"):
            self.invoke()
        self.assertEqual(self.cloud.moves, [])

    def test_original_cpu_anchor_is_subject_to_normal_stack_preflight(self):
        runner = mock.Mock(return_value={"Stacks": []})
        with self.assertRaisesRegex(ValueError, "Ambiguous CPU CloudFormation stack"):
            d.destroy_cpu(runner, runner, h.CPU_STACK, h.REGION)
        runner.assert_called_once_with(h.REGION, "cloudformation", "describe-stacks",
                                       "--stack-name", h.CPU_STACK)

    def test_cpu_destroy_preserves_unassociated_eip(self):
        for key in ("InstanceId", "AssociationId", "NetworkInterfaceId"):
            self.cloud.eip.pop(key, None)
        self.cloud.eip["Tags"] = [t for t in self.cloud.eip["Tags"]
                                  if t["Key"] != "StudioDcvInstanceId"]
        result = self.invoke()
        self.assertEqual(result["state"], "DELETE_COMPLETE")
        self.assertEqual(result["shared_eip_preserved"], h.EIP_IP)
        self.assertIsNone(self.cloud.eip.get("InstanceId"))
        self.assertTrue(self.cloud.deleted_volume)

    def test_cli_routes_non_anchor_cpu_destroy_to_verified_engine(self):
        with (mock.patch.object(cli, "describe") as desc,
              mock.patch.object(d, "destroy_cpu",
                                return_value={"state": "DELETE_COMPLETE"}) as cleanup,
              contextlib.redirect_stdout(io.StringIO())):
            cli.main(["destroy", "--stack", STACK, "--delete-stack"])
        cleanup.assert_called_once_with(cli.aws, cli.run, STACK, h.REGION)
        desc.assert_not_called()


if __name__ == "__main__":
    unittest.main()
