"""Offline mock tests for independent scoped cleanup. No AWS calls."""
import unittest
from unittest.mock import Mock
from scripts.gpu_cli_test_cleanup import reconcile

STACK = "studio-gpu-cli-acceptance1"
TOKEN = "unique-token-acceptance1"
TAGS = [{"Key": "StudioCleanupToken", "Value": TOKEN},
        {"Key": "StudioStack", "Value": STACK}]

class MissingStack(Exception):
    response = {"Error": {"Code": "ValidationError", "Message": "Stack does not exist"}}

class CleanupTests(unittest.TestCase):
    def setUp(self):
        self.cf, self.ec2 = Mock(), Mock()
        self.cf.describe_stacks.return_value = {"Stacks": [
            {"StackName": STACK, "StackStatus": "CREATE_COMPLETE", "Tags": TAGS}]}
        self.ec2.describe_instances.return_value = {"Reservations": []}
        self.ec2.describe_volumes.return_value = {"Volumes": []}
        self.ec2.describe_security_groups.return_value = {"SecurityGroups": []}
        self.ec2.describe_launch_templates.return_value = {"LaunchTemplates": []}

    def run_cleanup(self):
        return reconcile(self.cf, self.ec2, STACK, TOKEN)

    def absent(self):
        self.cf.describe_stacks.side_effect = MissingStack()

    def state(self, status):
        self.cf.describe_stacks.return_value["Stacks"][0]["StackStatus"] = status

    def instance(self, state, tags=TAGS):
        self.ec2.describe_instances.return_value = {"Reservations": [
            {"Instances": [{"InstanceId": "i-test", "State": {"Name": state}, "Tags": tags}]}]}

    def test_create_in_progress(self):
        self.state("CREATE_IN_PROGRESS")
        self.assertFalse(self.run_cleanup()["complete"])
        self.cf.delete_stack.assert_not_called()

    def test_rollback_in_progress(self):
        self.state("ROLLBACK_IN_PROGRESS")
        self.assertEqual(self.run_cleanup()["reason"], "stack busy")

    def test_rollback_complete(self):
        self.state("ROLLBACK_COMPLETE")
        self.assertFalse(self.run_cleanup()["complete"])
        self.cf.delete_stack.assert_called_once_with(StackName=STACK)

    def test_delete_failed(self):
        self.state("DELETE_FAILED")
        self.assertFalse(self.run_cleanup()["complete"])
        self.cf.delete_stack.assert_called_once()

    def test_delete_in_progress(self):
        self.state("DELETE_IN_PROGRESS")
        self.assertFalse(self.run_cleanup()["complete"])
        self.cf.delete_stack.assert_not_called()

    def test_stopped(self):
        self.absent()
        self.instance("stopped")
        self.assertFalse(self.run_cleanup()["complete"])
        self.ec2.terminate_instances.assert_called_once_with(InstanceIds=["i-test"])

    def test_running(self):
        self.absent()
        self.instance("running")
        self.run_cleanup()
        self.ec2.terminate_instances.assert_called_once()

    def test_shutting_down(self):
        self.absent()
        self.instance("shutting-down")
        self.assertFalse(self.run_cleanup()["complete"])
        self.ec2.terminate_instances.assert_not_called()

    def test_wrong_instance_tags(self):
        self.instance("stopped", [])
        with self.assertRaises(RuntimeError):
            self.run_cleanup()
        self.cf.delete_stack.assert_not_called()

    def test_wrong_stack_tags(self):
        self.cf.describe_stacks.return_value["Stacks"][0]["Tags"] = []
        with self.assertRaises(RuntimeError):
            self.run_cleanup()
        self.cf.delete_stack.assert_not_called()

    def test_attached_volume(self):
        self.absent()
        self.ec2.describe_volumes.return_value = {"Volumes": [
            {"VolumeId": "vol-test", "State": "in-use", "Tags": TAGS}]}
        self.assertFalse(self.run_cleanup()["complete"])
        self.ec2.delete_volume.assert_not_called()

    def test_retained_volume(self):
        self.absent()
        self.ec2.describe_volumes.return_value = {"Volumes": [
            {"VolumeId": "vol-test", "State": "available", "Tags": TAGS}]}
        self.assertFalse(self.run_cleanup()["complete"])
        self.ec2.delete_volume.assert_called_once_with(VolumeId="vol-test")

    def test_security_group(self):
        self.absent()
        self.ec2.describe_security_groups.return_value = {"SecurityGroups": [
            {"GroupId": "sg-test", "Tags": TAGS}]}
        self.assertFalse(self.run_cleanup()["complete"])
        self.ec2.delete_security_group.assert_called_once_with(GroupId="sg-test")

    def test_launch_template(self):
        self.absent()
        self.ec2.describe_launch_templates.return_value = {"LaunchTemplates": [
            {"LaunchTemplateId": "lt-test", "Tags": TAGS}]}
        self.assertFalse(self.run_cleanup()["complete"])
        self.ec2.delete_launch_template.assert_called_once_with(LaunchTemplateId="lt-test")

    def test_complete_only_after_absence(self):
        self.absent()
        self.assertTrue(self.run_cleanup()["complete"])

    def test_delete_complete_record_not_absence(self):
        self.state("DELETE_COMPLETE")
        self.assertFalse(self.run_cleanup()["complete"])

    def test_pagination_second_page(self):
        self.absent()
        self.ec2.describe_volumes.side_effect = [
            {"Volumes": [], "NextToken": "next"},
            {"Volumes": [{"VolumeId": "vol-test", "State": "available", "Tags": TAGS}]}]
        self.assertFalse(self.run_cleanup()["complete"])
        self.ec2.delete_volume.assert_called_once()

    def test_repeated_pagination_token(self):
        self.ec2.describe_volumes.side_effect = [
            {"Volumes": [], "NextToken": "next"},
            {"Volumes": [], "NextToken": "next"}]
        with self.assertRaises(RuntimeError):
            self.run_cleanup()
        self.cf.delete_stack.assert_not_called()

    def test_permission_error(self):
        self.ec2.describe_volumes.side_effect = PermissionError("denied")
        with self.assertRaises(PermissionError):
            self.run_cleanup()

    def test_wrong_volume_tags(self):
        self.absent()
        self.ec2.describe_volumes.return_value = {"Volumes": [
            {"VolumeId": "vol-test", "State": "available", "Tags": []}]}
        with self.assertRaises(RuntimeError):
            self.run_cleanup()
        self.ec2.delete_volume.assert_not_called()

    def test_terminated_instance_does_not_trigger_termination(self):
        self.absent()
        self.instance("terminated")
        self.assertTrue(self.run_cleanup()["complete"])
        self.ec2.terminate_instances.assert_not_called()

    def test_stop_start_transitions_remain_scoped(self):
        self.absent()
        for state in ("stopped", "pending", "running", "stopping"):
            with self.subTest(state=state):
                self.ec2.terminate_instances.reset_mock()
                self.instance(state)
                self.assertFalse(self.run_cleanup()["complete"])
                self.ec2.terminate_instances.assert_called_once_with(InstanceIds=["i-test"])

    def test_resource_deletion_requires_second_inventory(self):
        self.absent()
        self.ec2.describe_volumes.side_effect = [
            {"Volumes": [{"VolumeId": "vol-test", "State": "available", "Tags": TAGS}]},
            {"Volumes": []}]
        self.assertFalse(self.run_cleanup()["complete"])
        self.assertTrue(self.run_cleanup()["complete"])
        self.ec2.delete_volume.assert_called_once()

    def test_wrong_launch_template_tags_fail_closed(self):
        self.absent()
        self.ec2.describe_launch_templates.return_value = {"LaunchTemplates": [
            {"LaunchTemplateId": "lt-test", "Tags": []}]}
        with self.assertRaises(RuntimeError):
            self.run_cleanup()
        self.ec2.delete_launch_template.assert_not_called()

    def test_wrong_security_group_tags_fail_closed(self):
        self.absent()
        self.ec2.describe_security_groups.return_value = {"SecurityGroups": [
            {"GroupId": "sg-test", "Tags": []}]}
        with self.assertRaises(RuntimeError):
            self.run_cleanup()
        self.ec2.delete_security_group.assert_not_called()

    def test_unknown_stack_status_fails_closed(self):
        self.state("SOMETHING_UNEXPECTED")
        with self.assertRaises(RuntimeError):
            self.run_cleanup()
        self.cf.delete_stack.assert_not_called()

    def test_delete_api_failure_not_success(self):
        self.absent()
        self.ec2.describe_volumes.return_value = {"Volumes": [
            {"VolumeId": "vol-test", "State": "available", "Tags": TAGS}]}
        self.ec2.delete_volume.side_effect = PermissionError("denied")
        with self.assertRaises(PermissionError):
            self.run_cleanup()

    def test_invalid_scope(self):
        with self.assertRaises(ValueError):
            reconcile(self.cf, self.ec2, "production", TOKEN)
        self.cf.describe_stacks.assert_not_called()

if __name__ == "__main__":
    unittest.main()
