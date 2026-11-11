"""Offline-only Task 4 destruction checks. No AWS access; all APIs mocked."""
import contextlib
import copy
import io
import json
import subprocess
import unittest
from unittest import mock

from runtime import issue43_destroy as destroy
from runtime import issue43_gpu_acm_setup as acm
from runtime import issue43_hostname_handoff as h
from runtime import independent_eip
from scripts import studio_workstation_cli as cli
from test_issue43_gpu_acm_setup import IAMFake
from test_issue43_hostname_handoff import GPU, VOLUME


class FakeCleanup(IAMFake):
    def __init__(self):
        super().__init__()
        self.gpu["State"]["Name"] = "running"
        self.volume["Encrypted"] = True
        self.stack = {"StackName": h.GPU_STACK,
                      "StackId": "arn:aws:cloudformation:us-east-1:" + h.ACCOUNT +
                                 ":stack/" + h.GPU_STACK + "/abcdef",
                      "StackStatus": "CREATE_COMPLETE",
                      "Tags": [{"Key": "StudioStack", "Value": h.GPU_STACK},
                               {"Key": "StudioCleanupToken", "Value": h.GPU_TOKEN}]}
        self.sg = {"GroupId": "sg-0123456789abcdef0",
                   "Tags": copy.deepcopy(self.volume["Tags"])}
        self.lt = {"LaunchTemplateId": "lt-0123456789abcdef0",
                   "Tags": copy.deepcopy(self.volume["Tags"])}
        self.deleted_volume = False
        self.deleted_sg = False
        self.deleted_lt = False
        self.events = []
        self.foreign_tagged = False
        self.stuck_volume = False
        self.fail_schedule = False

    def __call__(self, region, *args):
        service, operation = args[:2]
        if (service, operation) == ("cloudformation", "describe-stacks"):
            return {"Stacks": [copy.deepcopy(self.stack)]}
        if (service, operation) == ("cloudformation", "list-stack-resources"):
            return {"StackResourceSummaries": [
                {"LogicalResourceId": "Workstation", "ResourceType": "AWS::EC2::Instance",
                 "PhysicalResourceId": GPU, "ResourceStatus": "CREATE_COMPLETE"},
                {"LogicalResourceId": "WorkstationSG", "ResourceType": "AWS::EC2::SecurityGroup",
                 "PhysicalResourceId": self.sg["GroupId"], "ResourceStatus": "CREATE_COMPLETE"},
                {"LogicalResourceId": "CleanupVolumeTagLaunchTemplate",
                 "ResourceType": "AWS::EC2::LaunchTemplate",
                 "PhysicalResourceId": self.lt["LaunchTemplateId"],
                 "ResourceStatus": "CREATE_COMPLETE"}]}
        if (service, operation) == ("ec2", "describe-instances") and "--filters" in args:
            return {"Reservations": [{"Instances": [copy.deepcopy(self.gpu)]}]}
        if (service, operation) == ("ec2", "describe-volumes"):
            if "--filters" in args:
                volumes = [] if self.deleted_volume else [copy.deepcopy(self.volume)]
                if self.foreign_tagged and self.deleted_volume:
                    volumes.append({"VolumeId": "vol-99999999999999999"})
                return {"Volumes": volumes}
            if self.deleted_volume:
                raise subprocess.CalledProcessError(254, args, stderr="InvalidVolume.NotFound")
            return {"Volumes": [copy.deepcopy(self.volume)]}
        if (service, operation) == ("ec2", "describe-security-groups"):
            if self.deleted_sg:
                if "--filters" in args:
                    return {"SecurityGroups": []}
                raise subprocess.CalledProcessError(254, args, stderr="InvalidGroup.NotFound")
            return {"SecurityGroups": [copy.deepcopy(self.sg)]}
        if (service, operation) == ("ec2", "describe-launch-templates"):
            if self.deleted_lt:
                if "--filters" in args:
                    return {"LaunchTemplates": []}
                raise subprocess.CalledProcessError(254, args, stderr="InvalidLaunchTemplateId.NotFound")
            return {"LaunchTemplates": [copy.deepcopy(self.lt)]}
        if (service, operation) == ("scheduler", "list-schedules"):
            return {"Schedules": [{"Name": name} for name in self.schedules]}
        if (service, operation) == ("scheduler", "get-schedule"):
            name = args[args.index("--name") + 1]
            if name not in self.schedules:
                raise subprocess.CalledProcessError(254, args, stderr="ResourceNotFoundException")
        if service == "iam" and operation in ("delete-role-policy", "update-assume-role-policy"):
            self.events.append("iam-revoke")
        if (service, operation) == ("ec2", "disassociate-address"):
            self.events.append("eip-detach")
        if (service, operation) == ("ec2", "associate-address"):
            self.events.append("eip-return")
        return super().__call__(region, *args)

    def run(self, cmd, *, capture=True):
        self.events.append(" ".join(cmd[1:3]))
        if "stop-instances" in cmd:
            self.gpu["State"]["Name"] = "stopping"
        elif "instance-stopped" in cmd:
            self.gpu["State"]["Name"] = "stopped"
        elif "delete-stack" in cmd:
            self.stack["StackStatus"] = "DELETE_COMPLETE"
            self.gpu["State"]["Name"] = "terminated"
            if not self.stuck_volume:
                self.volume["State"] = "available"
                self.volume["Attachments"] = []
            self.deleted_sg = True
            self.deleted_lt = True
        elif "stack-delete-complete" in cmd:
            assert self.stack["StackStatus"] == "DELETE_COMPLETE"
        elif "delete-volume" in cmd:
            assert self.volume["State"] == "available"
            self.deleted_volume = True
        elif "delete-schedule" in cmd:
            name = cmd[cmd.index("--name") + 1]
            if self.fail_schedule:
                raise subprocess.CalledProcessError(254, cmd, stderr="AccessDenied")
            del self.schedules[name]
        return ""


class ScopedDestroyTests(unittest.TestCase):
    def setUp(self):
        self.fake = FakeCleanup()
        self.fake._install = acm._install_grant
        acm._install_grant(self.fake, GPU)
        self.fake.events.clear()

    def destroy(self):
        # Existing legacy fixture has no CloudFormation CPU instance tags.
        # Keep it focused on GPU teardown; independent CPU ownership has
        # dedicated tests in test_independent_eip.py.
        def checked_cpu(aws, address):
            if self.fake.cpu["State"]["Name"] != "stopped":
                raise ValueError("CPU must remain stopped")
            return {"instance": h.CPU_ID}
        with (mock.patch.object(independent_eip, "_cpu_owner",
                                side_effect=checked_cpu),
              mock.patch.object(independent_eip, "restore_cpu",
                                side_effect=self._restore_legacy_cpu),
              mock.patch.object(acm, "revoke_gpu",
                                side_effect=self._revoke_legacy_gpu)):
            return destroy.destroy_gpu(self.fake, self.fake.run, h.GPU_STACK,
                                       h.REGION, sleep=lambda *_: None)

    def _restore_legacy_cpu(self, aws, gpu_id):
        result = h.restore_cpu(aws, gpu_id, allow_live=True)
        return result

    def _revoke_legacy_gpu(self, aws, gpu_id, *, independent=False):
        return acm._revoke_grant(aws, gpu_id)


    def test_complete_guarded_destroy_preserves_shared_cpu_and_revokes_gpu(self):
        # Simulate the GPU owning the shared EIP at shutdown.
        self.fake.eip["InstanceId"] = GPU
        self.fake.eip["NetworkInterfaceId"] = self.fake.gpu["NetworkInterfaces"][0]["NetworkInterfaceId"]
        for tag in self.fake.eip["Tags"]:
            if tag["Key"] == "StudioDcvInstanceId":
                tag["Value"] = GPU
        self.fake.eip["Tags"].append(
            {"Key": "StudioDcvReturnStack", "Value": h.CPU_STACK})
        result = self.destroy()
        self.assertEqual(result["state"], "DELETE_COMPLETE")
        self.assertEqual(result["eip_owner"], h.CPU_ID)
        self.assertTrue(self.fake.deleted_volume)
        self.assertTrue(self.fake.deleted_sg)
        self.assertTrue(self.fake.deleted_lt)
        self.assertFalse(self.fake.schedules)
        self.assertEqual(self.fake.trust["Statement"], [acm._statement(acm.CPU_SESSION)])
        actions = self.fake.events
        self.assertLess(actions.index("ec2 stop-instances"), actions.index("eip-detach"))
        self.assertLess(actions.index("eip-return"), actions.index("iam-revoke"))
        self.assertLess(actions.index("iam-revoke"), actions.index("cloudformation delete-stack"))
        self.assertLess(actions.index("ec2 delete-volume"), actions.index("scheduler delete-schedule"))

    def test_previously_stopped_gpu_and_cpu_owned_eip_still_clean(self):
        self.fake.gpu["State"]["Name"] = "stopped"
        self.destroy()
        self.assertNotIn("ec2 stop-instances", self.fake.events)
        self.assertNotIn("eip-detach", self.fake.events)
        self.assertFalse(self.fake.schedules)

    def test_wrong_volume_tag_fails_before_any_mutation(self):
        self.fake.volume["Tags"][0]["Value"] = "unrelated-token"
        with self.assertRaisesRegex(ValueError, "ownership"):
            self.destroy()
        self.assertEqual(self.fake.events, [])

    def test_unapproved_security_group_fails_before_any_mutation(self):
        self.fake.sg["Tags"].clear()
        with self.assertRaisesRegex(ValueError, "ownership"):
            self.destroy()
        self.assertEqual(self.fake.events, [])

    def test_bad_scheduler_target_blocks_delete(self):
        self.fake.schedules["studio-pr65-v-" + VOLUME]["Target"]["Input"] = '{"VolumeId":"vol-foreign"}'
        with self.assertRaisesRegex(ValueError, "schedule target"):
            self.destroy()
        self.assertEqual(self.fake.events, [])

    def test_foreign_eip_blocks_destroy_and_preserves_schedules(self):
        self.fake.eip["InstanceId"] = "i-foreign"
        with self.assertRaisesRegex(ValueError, "EIP owner"):
            self.destroy()
        self.assertEqual(self.fake.events, [])
        self.assertEqual(len(self.fake.schedules), 2)

    def test_shared_cpu_running_blocks_destroy_before_writes(self):
        self.fake.cpu["State"]["Name"] = "running"
        with self.assertRaisesRegex(ValueError, "CPU must remain stopped"):
            self.destroy()
        self.assertEqual(self.fake.events, [])

    def test_retained_volume_still_in_use_prevents_schedule_deletion(self):
        self.fake.stuck_volume = True
        with self.assertRaisesRegex(RuntimeError, "Retained EBS not detached"):
            self.destroy()
        self.assertEqual(len(self.fake.schedules), 2)
        self.assertFalse(self.fake.deleted_volume)

    def test_tagged_orphan_volume_prevents_schedule_removal(self):
        self.fake.foreign_tagged = True
        with self.assertRaisesRegex(RuntimeError, "owned Volumes remain"):
            self.destroy()
        self.assertEqual(len(self.fake.schedules), 2)

    def test_scheduler_access_denied_fails_without_claiming_complete(self):
        self.fake.fail_schedule = True
        with self.assertRaises(subprocess.CalledProcessError):
            self.destroy()
        self.assertGreaterEqual(len(self.fake.schedules), 1)
        self.assertTrue(self.fake.deleted_volume)

    def test_wrong_region_or_stack_never_calls_aws(self):
        f = mock.Mock()
        with self.assertRaises(ValueError):
            destroy.destroy_gpu(f, f, "foreign-stack", "us-east-1")
        with self.assertRaises(ValueError):
            destroy.destroy_gpu(f, f, h.GPU_STACK, "us-west-2")
        f.assert_not_called()

    def test_cpu_anchor_destroy_routes_through_guarded_cleanup(self):
        # The original CPU is replaceable once the independent EIP is released.
        # The real destroy_cpu verifies ownership and refuses attached EIPs.
        with (mock.patch.object(cli, "describe") as desc,
              mock.patch.object(destroy, "destroy_cpu",
                                return_value={"state": "DELETE_COMPLETE"}) as cleanup,
              contextlib.redirect_stdout(io.StringIO())):
            cli.main(["destroy", "--stack", cli.DEFAULT_STACK, "--delete-stack"])
        desc.assert_not_called()
        cleanup.assert_called_once_with(cli.aws, cli.run, cli.DEFAULT_STACK, cli.DEFAULT_REGION)

    def test_cpu_anchor_destroy_still_requires_explicit_flag(self):
        with (mock.patch.object(destroy, "destroy_cpu") as cleanup,
              mock.patch.object(cli, "aws") as aws,
              contextlib.redirect_stderr(io.StringIO())):
            with self.assertRaises(SystemExit):
                cli.main(["destroy", "--stack", cli.DEFAULT_STACK])
        cleanup.assert_not_called()
        aws.assert_not_called()

    def test_gpu_destroy_cli_dispatches_exact_scope(self):
        with (mock.patch.object(cli, "describe") as desc,
              mock.patch.object(destroy, "destroy_gpu",
                                return_value={"state": "DELETE_COMPLETE"}) as cleanup,
              contextlib.redirect_stdout(io.StringIO())):
            cli.main(["destroy", "--stack", h.GPU_STACK, "--delete-stack"])
        cleanup.assert_called_once_with(cli.aws, cli.run, h.GPU_STACK, h.REGION)
        desc.assert_not_called()

    def test_gpu_destroy_requires_delete_stack_flag_before_any_aws(self):
        with (mock.patch.object(destroy, "destroy_gpu") as cleanup,
              mock.patch.object(cli, "aws") as aws,
              contextlib.redirect_stderr(io.StringIO())):
            with self.assertRaises(SystemExit):
                cli.main(["destroy", "--stack", h.GPU_STACK])
        cleanup.assert_not_called()
        aws.assert_not_called()


if __name__ == "__main__":
    unittest.main()
