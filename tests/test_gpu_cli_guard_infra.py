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
            if s["Sid"] == "ReadInventory" or s["Sid"] == "OwnLogs":
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

    def test_deadline_requires_timezone(self):
        # Naive deadline is rejected before any boto3 client is created.
        with patch.dict("os.environ", {"STUDIO_TEST_STACK": "studio-gpu-cli-test",
                                      "STUDIO_CLEANUP_TOKEN": "token-1234567890",
                                      "STUDIO_NOT_BEFORE_UTC": "2026-11-01T00:00:00"}):
            with self.assertRaises(ValueError):
                cleanup.handler({}, None)

if __name__ == "__main__":
    unittest.main()
