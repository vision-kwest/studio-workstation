"""Prevent release IAM drift: future versions must remain readable by the EC2 role."""
import json
from pathlib import Path
import unittest

ROOT=Path(__file__).resolve().parents[1]
POLICY=ROOT/"infra/workstation-release-read-policy.json"
BUCKET="studio-workstation-releases-401233741942-us-east-1"
NAMES=("installation-scripts.tar.gz","virtual-desktop.tar.gz")

class ReleaseRolePolicyTests(unittest.TestCase):
    def test_versioned_release_read_only(self):
        policy=json.loads(POLICY.read_text())
        self.assertEqual(policy["Version"],"2012-10-17")
        statements=policy["Statement"]
        self.assertEqual(len(statements),1)
        statement=statements[0]
        self.assertEqual(statement["Effect"],"Allow")
        self.assertEqual(statement["Action"],["s3:GetObject"])
        self.assertEqual(set(statement["Resource"]),{
            f"arn:aws:s3:::{BUCKET}/releases/*/res-2026.06/{name}"
            for name in NAMES
        })
        for version in ("v0.1.0","v0.1.1","v0.1.2"):
            for name in NAMES:
                key=f"arn:aws:s3:::{BUCKET}/releases/{version}/res-2026.06/{name}"
                self.assertTrue(any(
                    __import__("fnmatch").fnmatchcase(key,pattern)
                    for pattern in statement["Resource"]
                ),key)

    def test_no_iam_or_s3_write_privileges(self):
        policy=json.loads(POLICY.read_text())
        actions=[a for s in policy["Statement"] for a in s["Action"]]
        self.assertEqual(actions,["s3:GetObject"])

if __name__=="__main__":unittest.main()
