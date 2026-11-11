import unittest
from unittest.mock import patch
from runtime import independent_eip

class IndependentEipTests(unittest.TestCase):
    def test_target_rejects_non_studio_stack(self):
        with self.assertRaisesRegex(ValueError, "Invalid Studio stack"):
            independent_eip._target(lambda *args: None, "foreign-stack")

    def test_release_unassociated_is_noop(self):
        address = {"AllocationId": independent_eip.legacy.EIP_ID, "Tags": []}
        with patch.object(independent_eip, "_address", return_value=address):
            self.assertEqual(independent_eip.release(lambda *a: self.fail("unexpected AWS write"))["state"], "unassociated")

    def test_assign_rejects_existing_association(self):
        with patch.object(independent_eip, "_target", return_value=("i-test", "eni-test")), patch.object(
            independent_eip, "_address", return_value={"AssociationId": "eipassoc-old", "Tags": []}
        ):
            with self.assertRaisesRegex(ValueError, "not unassociated"):
                independent_eip.assign(lambda *a: self.fail("unexpected AWS write"), "studio-test", lambda *a: None)

    def test_release_refuses_running_owner(self):
        address = {"InstanceId": "i-old", "AssociationId": "eipassoc-old",
                   "Tags": [{"Key": "StudioDcvInstanceId", "Value": "i-old"}]}
        vm = {"VpcId": "vpc-01bbe5d01838f70b2",
              "IamInstanceProfile": {"Arn": independent_eip.legacy.BASE_PROFILE},
              "State": {"Name": "running"}}
        with patch.object(independent_eip.legacy, "_items", return_value={"i-old": vm}), patch.object(
            independent_eip, "_address", return_value=address
        ):
            with self.assertRaisesRegex(ValueError, "Stop the current"):
                independent_eip.release(lambda *a: self.fail("unexpected AWS write"))

    def test_guest_tls_fails_closed(self):
        def aws(region, service, operation, *args):
            if operation == "send-command":
                return {"Command": {"CommandId": "test"}}
            return {"Status": "Failed"}
        with self.assertRaisesRegex(RuntimeError, "readiness failed"):
            independent_eip._guest_tls(aws, "i-test", sleep=lambda _: None)

    def test_release_refuses_stale_unassociated_tag(self):
        with patch.object(independent_eip, "_address", return_value={
            "Tags": [{"Key": "StudioDcvInstanceId", "Value": "i-stale"}]
        }):
            with self.assertRaises(ValueError):
                independent_eip.release(lambda *a: self.fail("unexpected AWS write"))

if __name__ == "__main__":
    unittest.main()
