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


    def test_gpu_preview_uses_replacement_cpu(self):
        h = independent_eip.legacy
        cpu, gpu = "i-aaaaaaaaaaaaaaaaa", "i-bbbbbbbbbbbbbbbbb"
        address = {"InstanceId": cpu, "AssociationId": "eipassoc-old",
                   "Tags": [{"Key": "StudioDcvInstanceId", "Value": cpu}]}
        owner = {"instance": cpu, "stack": "studio-workstation-cpu-rez77",
                 "eni": "eni-cpu", "association": "eipassoc-old"}
        vm = {"State": {"Name": "running"}, "ImageId": h.GPU_AMI,
              "InstanceType": "g4dn.xlarge",
              "IamInstanceProfile": {"Arn": h.BASE_PROFILE},
              "VpcId": "vpc-01bbe5d01838f70b2",
              "NetworkInterfaces": [{"NetworkInterfaceId": "eni-gpu",
                                     "Attachment": {"DeviceIndex": 0, "Status": "attached"}}]}
        with (patch.object(independent_eip, "_address", return_value=address),
              patch.object(independent_eip, "_cpu_owner", return_value=owner),
              patch.object(h, "_stack_iid", return_value=gpu),
              patch.object(h, "_items", return_value={gpu: vm}),
              patch.object(h, "_gpu_guard", return_value={"terminate_at": "future"}) as guard,
              patch("runtime.pr65_gpu_create.preflight") as preflight):
            result = independent_eip.gpu_preview(lambda *a: None)
        self.assertEqual(result["from"], cpu)
        self.assertEqual(result["to"], gpu)
        self.assertEqual(result["cpu"]["stack"], "studio-workstation-cpu-rez77")
        guard.assert_called_once()
        preflight.assert_called_once()

    def test_gpu_preview_allows_clean_unassociated_eip(self):
        h = independent_eip.legacy
        gpu = "i-bbbbbbbbbbbbbbbbb"
        vm = {"State": {"Name": "running"}, "ImageId": h.GPU_AMI,
              "InstanceType": "g4dn.xlarge", "VpcId": "vpc-01bbe5d01838f70b2",
              "IamInstanceProfile": {"Arn": h.BASE_PROFILE},
              "NetworkInterfaces": [{"NetworkInterfaceId": "eni-gpu",
                                     "Attachment": {"DeviceIndex": 0, "Status": "attached"}}]}
        with (patch.object(independent_eip, "_address", return_value={"Tags": []}),
              patch.object(independent_eip, "_cpu_owner", side_effect=AssertionError("CPU not needed")),
              patch.object(h, "_stack_iid", return_value=gpu),
              patch.object(h, "_items", return_value={gpu: vm}),
              patch.object(h, "_gpu_guard", return_value={"terminate_at": "future"}),
              patch("runtime.pr65_gpu_create.preflight")):
            p = independent_eip.gpu_preview(lambda *a: None)
        self.assertIsNone(p["from"])
        self.assertIsNone(p["old_association"])

    def test_handoff_from_unassociated_has_no_cpu_return(self):
        gpu = "i-bbbbbbbbbbbbbbbbb"
        plan = {"from": None, "to": gpu, "cpu": None, "old_association": None,
                "gpu_eni": "eni-gpu", "guard": {}}
        after = {"InstanceId": gpu, "NetworkInterfaceId": "eni-gpu",
                 "AssociationId": "eipassoc-gpu",
                 "Tags": [{"Key": "StudioDcvInstanceId", "Value": gpu}]}
        calls = []
        def aws(region, service, op, *args):
            calls.append(op)
            return {"AssociationId": "eipassoc-gpu"} if op == "associate-address" else {}
        with (patch.object(independent_eip, "gpu_preview", return_value=plan),
              patch.object(independent_eip, "_address", side_effect=[{"Tags": []}, after])):
            result = independent_eip.handoff_gpu(aws, lambda *a: None, check_guest=lambda *a: None)
        self.assertEqual(result["owner"], gpu)
        self.assertEqual(calls, ["associate-address", "create-tags"])

    def test_restore_refuses_missing_cpu_return_tag(self):
        with patch.object(independent_eip, "_address", return_value={
            "InstanceId": "i-bbbbbbbbbbbbbbbbb",
            "Tags": [{"Key": "StudioDcvInstanceId", "Value": "i-bbbbbbbbbbbbbbbbb"}]}):
            with self.assertRaisesRegex(ValueError, "missing verified CPU return stack"):
                independent_eip.restore_cpu(lambda *a: None, "i-bbbbbbbbbbbbbbbbb")

    def test_handoff_refuses_unverified_guest_before_any_writes(self):
        plan = {"to": "i-bbbbbbbbbbbbbbbbb"}
        with patch.object(independent_eip, "gpu_preview", return_value=plan):
            with self.assertRaisesRegex(ValueError, "guest TLS failed"):
                independent_eip.handoff_gpu(
                    lambda *a: self.fail("AWS write before TLS"),
                    lambda *a: None,
                    check_guest=lambda *a: (_ for _ in ()).throw(ValueError("guest TLS failed")))

if __name__ == "__main__":
    unittest.main()
