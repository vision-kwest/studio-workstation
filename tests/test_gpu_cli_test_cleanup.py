"""Offline contract tests for scoped GPU test cleanup; no AWS calls."""
import unittest
from unittest.mock import Mock
from scripts.gpu_cli_test_cleanup import reconcile


class CleanupTests(unittest.TestCase):
    def setUp(self):
        self.cf = Mock()
        self.ec2 = Mock()
        self.stack = "studio-gpu-cli-test"
        self.token = "test-unique-token-1234"
        self.cf.describe_stacks.return_value = {"Stacks": [{
            "StackName": self.stack, "StackStatus": "CREATE_COMPLETE",
            "Tags": [{"Key": "StudioCleanupToken", "Value": self.token}],
        }]}
        self.ec2.describe_instances.return_value = {"Reservations": []}
        self.ec2.describe_volumes.return_value = {"Volumes": []}
        self.ec2.describe_security_groups.return_value = {"SecurityGroups": []}

    def test_create_in_progress_is_not_deleted(self):
        self.cf.describe_stacks.return_value["Stacks"][0]["StackStatus"] = "CREATE_IN_PROGRESS"
        r = reconcile(self.cf, self.ec2, self.stack, self.token)
        self.assertFalse(r["complete"])
        self.cf.delete_stack.assert_not_called()

    def test_rollback_triggers_delete(self):
        self.cf.describe_stacks.return_value["Stacks"][0]["StackStatus"] = "ROLLBACK_COMPLETE"
        r = reconcile(self.cf, self.ec2, self.stack, self.token)
        self.assertTrue(r["complete"])

    def test_delete_failed_retries(self):
        self.cf.describe_stacks.return_value["Stacks"][0]["StackStatus"] = "DELETE_FAILED"
        r = reconcile(self.cf, self.ec2, self.stack, self.token)
        self.assertFalse(r["complete"])
        self.cf.delete_stack.assert_called_once_with(StackName=self.stack)

    def test_stopped_instance_is_terminated(self):
        self.cf.describe_stacks.return_value["Stacks"] = []
        self.ec2.describe_instances.return_value = {"Reservations": [{"Instances": [{
            "InstanceId": "i-test", "State": {"Name": "stopped"},
            "BlockDeviceMappings": [{"Ebs": {"VolumeId": "vol-test"}}],
        }]}]}
        r = reconcile(self.cf, self.ec2, self.stack, self.token)
        self.assertFalse(r["complete"])
        self.ec2.terminate_instances.assert_called_once_with(InstanceIds=["i-test"])
        self.ec2.create_tags.assert_called_once()

    def test_running_instance_is_terminated(self):
        self.cf.describe_stacks.return_value["Stacks"] = []
        self.ec2.describe_instances.return_value = {"Reservations": [{"Instances": [{
            "InstanceId": "i-test", "State": {"Name": "running"},
        }]}]}
        reconcile(self.cf, self.ec2, self.stack, self.token)
        self.ec2.terminate_instances.assert_called_once()

    def test_retained_ebs_deleted_only_when_available(self):
        self.cf.describe_stacks.return_value["Stacks"] = []
        self.ec2.describe_volumes.return_value = {"Volumes": [{"VolumeId": "vol-test", "State": "available"}]}
        r = reconcile(self.cf, self.ec2, self.stack, self.token)
        self.assertFalse(r["complete"])
        self.ec2.delete_volume.assert_called_once_with(VolumeId="vol-test")

    def test_attached_ebs_never_deleted(self):
        self.cf.describe_stacks.return_value["Stacks"] = []
        self.ec2.describe_volumes.return_value = {"Volumes": [{"VolumeId": "vol-test", "State": "in-use"}]}
        reconcile(self.cf, self.ec2, self.stack, self.token)
        self.ec2.delete_volume.assert_not_called()

    def test_wrong_token_fails_closed(self):
        with self.assertRaises(RuntimeError):
            reconcile(self.cf, self.ec2, self.stack, "wrong-token-1234")
        self.cf.delete_stack.assert_not_called()
        self.ec2.terminate_instances.assert_not_called()

    def test_no_resources_complete(self):
        self.cf.describe_stacks.return_value["Stacks"] = []
        self.assertTrue(reconcile(self.cf, self.ec2, self.stack, self.token)["complete"])


if __name__ == "__main__":
    unittest.main()
