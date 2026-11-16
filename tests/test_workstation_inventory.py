"""Behavior tests for explicit workstation selection and read-only AWS discovery."""
import contextlib
import copy
import io
import json
import subprocess
import unittest
from unittest import mock

from runtime import workstation_inventory as inventory
from scripts import studio_workstation_cli as cli

H = inventory.hostname


def fixture(name, number, instance_type="g4dn.xlarge", state="running"):
    arn = f"arn:aws:cloudformation:us-east-1:{H.ACCOUNT}:stack/{name}/identity-{number}"
    iid = "i-" + f"{number:017x}"
    stack = {"StackName": name, "StackId": arn, "StackStatus": "CREATE_COMPLETE",
             "Parameters": [{"ParameterKey": "InstanceProfileName", "ParameterValue": "StudioWorkstationSSMRole"},
                            {"ParameterKey": "InstanceType", "ParameterValue": instance_type}]}
    vm = {"InstanceId": iid, "InstanceType": instance_type, "State": {"Name": state},
          "PublicIpAddress": f"203.0.113.{number}",
          "Tags": [{"Key": "aws:cloudformation:stack-id", "Value": arn},
                   {"Key": "aws:cloudformation:logical-id", "Value": "Workstation"}]}
    record = {"LogicalResourceId": "Workstation", "ResourceType": "AWS::EC2::Instance",
              "PhysicalResourceId": iid, "StackId": arn, "StackName": name}
    return stack, vm, record


class InventoryTests(unittest.TestCase):
    def setUp(self):
        data = [fixture("studio-gpu-art", 1), fixture("studio-gpu-ai", 2),
                fixture("studio-cpu-dev", 3, "m7i.large", "stopped")]
        self.stacks, self.vms, records = map(list, zip(*data))
        self.resources = {s["StackId"]: [r] for s, r in zip(self.stacks, records)}
        self.vms[1]["PublicIpAddress"] = H.EIP_IP
        self.address = {"AllocationId": H.EIP_ID, "InstanceId": self.vms[1]["InstanceId"],
                        "PublicIp": H.EIP_IP,
                        "Tags": [{"Key": "StudioDcvInstanceId", "Value": self.vms[1]["InstanceId"]}]}
        self.calls = []

    def aws(self, region, service, operation, *args):
        self.calls.append((region, service, operation, args))
        if (service, operation) == ("cloudformation", "describe-stacks"):
            return {"Stacks": self.stacks}
        if (service, operation) == ("cloudformation", "list-stack-resources"):
            return {"StackResourceSummaries": self.resources[args[1]]}
        if (service, operation) == ("ec2", "describe-instances"):
            return {"Reservations": [{"Instances": self.vms}]}
        if (service, operation) == ("ec2", "describe-addresses"):
            return {"Addresses": [self.address] if self.address else []}
        self.fail("Unexpected API (inventory must be read-only): " + operation)

    def test_multiple_profiles_have_independent_urls_and_one_hostname_owner(self):
        rows = inventory.list_workstations(self.aws, "us-east-1")
        by_name = {row["name"]: row for row in rows}
        self.assertEqual(len(rows), 3)
        self.assertEqual(by_name["studio-gpu-art"]["dcv_url"], "https://203.0.113.1:8443")
        self.assertEqual(by_name["studio-gpu-ai"]["dcv_url"], "https://" + H.HOST + ":8443")
        self.assertIsNone(by_name["studio-cpu-dev"]["dcv_url"])
        self.assertEqual(sum(row["hostname_owner"] for row in rows), 1)
        self.assertTrue(all(row["dcv_verified"] is False for row in rows))
        self.assertTrue(all(args[1].startswith("arn:") for _, service, op, args in self.calls
                            if op == "list-stack-resources"))

    def test_stale_stack_remains_visible_without_an_endpoint(self):
        self.vms.pop(0)
        rows = inventory.list_workstations(self.aws, "us-east-1")
        stale = next(row for row in rows if row["name"] == "studio-gpu-art")
        self.assertEqual(stale["state"], "missing")
        self.assertIsNone(stale["dcv_url"])

    def test_stack_failure_before_ec2_is_not_hidden(self):
        self.stacks[0]["StackStatus"] = "ROLLBACK_COMPLETE"
        self.resources[self.stacks[0]["StackId"]] = []
        row = next(r for r in inventory.list_workstations(self.aws, "us-east-1")
                   if r["name"] == "studio-gpu-art")
        self.assertEqual(row["state"], "not-created")
        self.assertEqual(row["stack_status"], "ROLLBACK_COMPLETE")

    def test_immutable_stack_mismatch_never_advertises_url(self):
        self.vms[0]["Tags"][0]["Value"] += "-replacement"
        row = next(r for r in inventory.list_workstations(self.aws, "us-east-1")
                   if r["name"] == "studio-gpu-art")
        self.assertEqual(row["state"], "ownership-mismatch")
        self.assertIsNone(row["dcv_url"])

    def test_foreign_shared_stack_is_excluded(self):
        self.stacks.append({"StackName": "studio-release-bucket", "StackId": "other", "StackStatus": "CREATE_COMPLETE"})
        self.assertEqual(len(inventory.list_workstations(self.aws, "us-east-1")), 3)

    def test_managed_tag_discovers_new_stack_without_historical_profile_parameter(self):
        self.stacks[0]["Parameters"] = []
        self.stacks[0]["Tags"] = [{"Key": "StudioManaged", "Value": "true"}]
        self.assertEqual(len(inventory.list_workstations(self.aws, "us-east-1")), 3)

    def test_unassociated_eip_and_current_ip_changes(self):
        self.address = {}
        self.vms[1]["PublicIpAddress"] = "203.0.113.99"
        rows = inventory.list_workstations(self.aws, "us-east-1")
        self.assertTrue(all(not r["hostname_owner"] for r in rows))
        self.assertIn("https://203.0.113.99:8443", [r["dcv_url"] for r in rows])

    def test_wrong_owner_tag_does_not_advertise_hostname(self):
        self.address["Tags"] = []
        self.assertTrue(all(not r["hostname_owner"] for r in inventory.list_workstations(self.aws, "us-east-1")))

    def test_ambiguous_resource_fails(self):
        self.resources[self.stacks[0]["StackId"]] *= 2
        with self.assertRaisesRegex(ValueError, "Ambiguous"):
            inventory.list_workstations(self.aws, "us-east-1")

    def test_permission_error_is_not_an_empty_inventory(self):
        with self.assertRaises(subprocess.CalledProcessError):
            inventory.list_workstations(mock.Mock(side_effect=subprocess.CalledProcessError(1, "aws")), "us-east-1")


class NamedCliTests(unittest.TestCase):
    def invoke(self, args):
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            cli.main(args)

    def test_named_actions_and_stack_alias_reuse_existing_dispatch(self):
        for action in ("start", "stop", "status", "connect", "destroy", "check-ready", "hostname-assign", "extend-expiration"):
            extra = ["--additional-minutes", "30"] if action == "extend-expiration" else []
            for selector in (["studio-cpu-art"], ["--stack", "studio-cpu-art"], ["--name", "studio-cpu-art"]):
                with self.subTest(action=action, selector=selector), mock.patch.object(cli, "command") as command:
                    self.invoke([action, *selector, *extra])
                    self.assertEqual(command.call_args.args[0].stack, "studio-cpu-art")

    def test_no_target_never_silently_uses_deleted_default(self):
        for action in ("start", "stop", "status", "connect", "destroy"):
            with self.subTest(action=action), mock.patch.object(cli, "command") as command:
                with self.assertRaises(SystemExit):
                    self.invoke([action])
                command.assert_not_called()

    def test_conflicting_selectors_are_rejected(self):
        for args in (["start", "one", "--stack", "two"], ["start", "--stack", "one", "--name", "two"]):
            with mock.patch.object(cli, "command") as command, self.assertRaises(SystemExit):
                self.invoke(args)
            command.assert_not_called()

    def test_cpu_name_generation_is_unique(self):
        with mock.patch.object(cli, "command") as command:
            self.invoke(["create", "--profile", "cpu", "--dry-run"])
            first = command.call_args.args[0].stack
            self.invoke(["create", "--profile", "cpu", "--dry-run"])
            second = command.call_args.args[0].stack
        self.assertRegex(first, r"^studio-cpu-[a-f0-9]{12}$")
        self.assertNotEqual(first, second)

    def test_gpu_restriction_is_not_bypassed_by_cpu_default(self):
        with mock.patch.object(cli, "command") as command, self.assertRaises(SystemExit):
            self.invoke(["create", "--profile", "gpu"])
        command.assert_not_called()

    def test_list_dispatch_is_read_only(self):
        with mock.patch.object(inventory, "list_workstations", return_value=[]) as listing, mock.patch.object(cli, "run") as run:
            self.invoke(["list"])
        listing.assert_called_once_with(cli.aws, cli.DEFAULT_REGION)
        run.assert_not_called()

    def test_describe_pins_ec2_to_immutable_stack_id(self):
        stack, vm, resource = fixture("studio-cpu-art", 1, "m7i.large")
        for wrong in (False, True):
            instance = copy.deepcopy(vm)
            if wrong:
                instance["Tags"][0]["Value"] += "-other"
            with mock.patch.object(cli, "aws", side_effect=[{"StackResourceDetail": resource}, {"Reservations": [{"Instances": [instance]}]}]):
                if wrong:
                    with self.assertRaisesRegex(ValueError, "selected CloudFormation stack ID"):
                        cli.describe(stack["StackName"], "us-east-1")
                else:
                    self.assertEqual(cli.describe(stack["StackName"], "us-east-1")["stack_id"], stack["StackId"])

    def test_wrong_resource_type_fails_before_ec2(self):
        stack, _, resource = fixture("studio-cpu-art", 1)
        resource["ResourceType"] = "AWS::S3::Bucket"
        with mock.patch.object(cli, "aws", return_value={"StackResourceDetail": resource}) as aws:
            with self.assertRaisesRegex(ValueError, "identity mismatch"):
                cli.describe(stack["StackName"], "us-east-1")
        self.assertEqual(aws.call_count, 1)
