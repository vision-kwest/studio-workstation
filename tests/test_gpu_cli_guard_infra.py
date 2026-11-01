"""Offline infrastructure policy and deadline contract tests; no AWS SDK."""
import json
import unittest
from pathlib import Path
from unittest.mock import patch
from datetime import datetime, timezone
from scripts import gpu_cli_test_cleanup as cleanup

ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = json.loads((ROOT / "infra/gpu-cli-test-cleanup.json").read_text())

class GuardInfrastructureTests(unittest.TestCase):
    def test_template_has_no_workstation_resources(self):
        types = {r["Type"] for r in TEMPLATE["Resources"].values()}
        self.assertNotIn("AWS::EC2::Instance", types)
        self.assertNotIn("AWS::EC2::Volume", types)
        self.assertNotIn("AWS::ImageBuilder::ImagePipeline", types)

    def test_scheduler_independent_and_enabled(self):
        schedule = TEMPLATE["Resources"]["CleanupSchedule"]["Properties"]
        self.assertEqual(schedule["ScheduleExpression"], "rate(5 minutes)")
        self.assertEqual(schedule["State"], "ENABLED")
        self.assertEqual(schedule["FlexibleTimeWindow"]["Mode"], "OFF")
        self.assertEqual(schedule["Target"]["Arn"],
                         {"Fn::GetAtt": ["CleanupFunction", "Arn"]})

    def test_schedule_role_exact_lambda(self):
        policy = TEMPLATE["Resources"]["ScheduleRole"]["Properties"]["Policies"][0]["PolicyDocument"]["Statement"]
        self.assertEqual(policy[0]["Action"], "lambda:InvokeFunction")
        self.assertEqual(policy[0]["Resource"], {"Fn::GetAtt": ["CleanupFunction", "Arn"]})
        trust = TEMPLATE["Resources"]["ScheduleRole"]["Properties"]["AssumeRolePolicyDocument"]["Statement"][0]
        self.assertIn("aws:SourceArn", trust["Condition"]["StringEquals"])

    def test_write_actions_are_scoped(self):
        statements = TEMPLATE["Resources"]["CleanupRole"]["Properties"]["Policies"][0]["PolicyDocument"]["Statement"]
        for s in statements:
            if s["Sid"] in ("ReadInventory", "OwnLogs", "OneDurableInventory"):
                continue
            self.assertNotEqual(s["Resource"], "*")
            if s["Action"] != "cloudformation:DeleteStack":
                self.assertIn("ec2:ResourceTag/StudioCleanupToken", s["Condition"]["StringEquals"])
                self.assertIn("ec2:ResourceTag/StudioStack", s["Condition"]["StringEquals"])

    def test_lambda_alarm_and_deadline(self):
        resources = TEMPLATE["Resources"]
        self.assertEqual(resources["ErrorAlarm"]["Properties"]["TreatMissingData"], "breaching")
        env = resources["CleanupFunction"]["Properties"]["Environment"]["Variables"]
        self.assertEqual(env["STUDIO_FAIL_PENDING"], "true")
        self.assertIn("STUDIO_NOT_BEFORE_UTC", env)

    def test_logs_policy_has_no_lambda_resource_cycle(self):
        statements = TEMPLATE["Resources"]["CleanupRole"]["Properties"]["Policies"][0]["PolicyDocument"]["Statement"]
        logs = next(x for x in statements if x["Sid"] == "OwnLogs")
        self.assertNotIn("${CleanupFunction}", logs["Resource"]["Fn::Sub"])
        self.assertIn("${TestStack}", logs["Resource"]["Fn::Sub"])

    def test_guard_template_names_fit_aws_limits(self):
        pattern = TEMPLATE["Parameters"]["TestStack"]["AllowedPattern"]
        import re
        self.assertTrue(re.fullmatch(pattern, "studio-gpu-cli-test1"))
        self.assertFalse(re.fullmatch(pattern, "studio-gpu-cli-" + "x" * 40))

    def test_cloudformation_delete_only_exact_test_stack(self):
        statements = TEMPLATE["Resources"]["CleanupRole"]["Properties"]["Policies"][0]["PolicyDocument"]["Statement"]
        delete = next(x for x in statements if x["Sid"] == "DeleteOnlyNamedStack")
        self.assertEqual(delete["Action"], "cloudformation:DeleteStack")
        self.assertIn(":stack/${TestStack}/*", delete["Resource"]["Fn::Sub"])
        self.assertNotIn("cloudformation:*", json.dumps(statements))

    def test_scheduler_cannot_invoke_other_functions(self):
        statements = TEMPLATE["Resources"]["ScheduleRole"]["Properties"]["Policies"][0]["PolicyDocument"]["Statement"]
        invokes = [s for s in statements if s["Action"] == "lambda:InvokeFunction"]
        self.assertEqual(len(invokes), 1)
        self.assertEqual(invokes[0]["Resource"], {"Fn::GetAtt": ["CleanupFunction", "Arn"]})

    def test_lambda_cannot_create_or_tag_ec2(self):
        statements = TEMPLATE["Resources"]["CleanupRole"]["Properties"]["Policies"][0]["PolicyDocument"]["Statement"]
        actions = [a for st in statements for a in
                   (st["Action"] if isinstance(st["Action"], list) else [st["Action"]])]
        self.assertNotIn("ec2:CreateTags", actions)
        self.assertNotIn("ec2:RunInstances", actions)
        self.assertNotIn("iam:PassRole", actions)
        self.assertNotIn("*", actions)

    def test_scheduler_dlq_is_independent_and_retained(self):
        resources = TEMPLATE["Resources"]
        queue = resources["SchedulerDLQ"]
        self.assertEqual(queue["Type"], "AWS::SQS::Queue")
        self.assertEqual(queue["Properties"]["MessageRetentionPeriod"], 1209600)
        self.assertTrue(queue["Properties"]["SqsManagedSseEnabled"])
        schedule = resources["CleanupSchedule"]["Properties"]
        self.assertEqual(schedule["Target"]["DeadLetterConfig"]["Arn"],
                         {"Fn::GetAtt": ["SchedulerDLQ", "Arn"]})

    def test_scheduler_role_can_send_only_to_dlq(self):
        policy = TEMPLATE["Resources"]["ScheduleRole"]["Properties"]["Policies"][0]["PolicyDocument"]["Statement"]
        send = [s for s in policy if s["Action"] == "sqs:SendMessage"]
        self.assertEqual(len(send), 1)
        self.assertEqual(send[0]["Resource"], {"Fn::GetAtt": ["SchedulerDLQ", "Arn"]})
        self.assertNotIn("sqs:*", json.dumps(policy))

    def test_scheduler_delivery_and_dlq_alarms(self):
        resources = TEMPLATE["Resources"]
        self.assertEqual(resources["SchedulerDLQAlarm"]["Properties"]["MetricName"],
                         "ApproximateNumberOfMessagesVisible")
        self.assertEqual(resources["SchedulerDeliveryFailuresAlarm"]["Properties"]["MetricName"],
                         "TargetErrorCount")
        for name in ("SchedulerDLQAlarm", "SchedulerDeliveryFailuresAlarm"):
            alarm = resources[name]["Properties"]
            self.assertEqual(alarm["Threshold"], 0)
            self.assertEqual(alarm["ComparisonOperator"], "GreaterThanThreshold")
            self.assertEqual(alarm["AlarmActions"], [{"Ref": "AlarmTopic"}])

    def test_inventory_is_durable_and_role_scoped(self):
        resources = TEMPLATE["Resources"]
        table = resources["CleanupInventory"]
        self.assertEqual(table["Type"], "AWS::DynamoDB::Table")
        self.assertEqual(table["DeletionPolicy"], "Retain")
        self.assertEqual(table["UpdateReplacePolicy"], "Retain")
        self.assertTrue(table["Properties"]["PointInTimeRecoverySpecification"]["PointInTimeRecoveryEnabled"])
        policy = resources["CleanupRole"]["Properties"]["Policies"][0]["PolicyDocument"]["Statement"]
        statement = next(x for x in policy if x["Sid"] == "OneDurableInventory")
        self.assertEqual(statement["Resource"], {"Fn::GetAtt": ["CleanupInventory", "Arn"]})
        self.assertEqual(set(statement["Action"]), {"dynamodb:GetItem", "dynamodb:UpdateItem"})
        env = resources["CleanupFunction"]["Properties"]["Environment"]["Variables"]
        self.assertEqual(env["STUDIO_INVENTORY_TABLE"], {"Ref": "CleanupInventory"})
        self.assertEqual(resources["CleanupFunction"]["Properties"]["ReservedConcurrentExecutions"], 1)

    def test_deadline_requires_timezone(self):
        # Naive deadline is rejected before any boto3 client is created.
        with patch.dict("os.environ", {"STUDIO_TEST_STACK": "studio-gpu-cli-test",
                                      "STUDIO_CLEANUP_TOKEN": "token-1234567890",
                                      "STUDIO_NOT_BEFORE_UTC": "2026-11-01T00:00:00"}):
            with self.assertRaises(ValueError):
                cleanup.handler({}, None)

if __name__ == "__main__":
    unittest.main()
