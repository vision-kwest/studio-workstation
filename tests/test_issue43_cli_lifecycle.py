"""Issue #43 Task 3: offline lifecycle and hostname orchestration contracts.

These tests mock every AWS and network write. Do not launch any resources.
"""
import contextlib
import io
import subprocess
import unittest
from unittest import mock

from scripts import studio_workstation_cli as cli
from runtime import issue43_hostname_handoff as handoff
from runtime import issue43_gpu_acm_setup as acm

GPU = "i-0123456789abcdef0"
CPU = handoff.CPU_ID
GPU_STACK = cli.GPU_ACCEPTANCE_STACK
CPU_STACK = cli.DEFAULT_STACK
TRUSTED = "https://dcv.kwesidavis.com:8443"


class LifecycleTests(unittest.TestCase):
    def command(self, args):
        with contextlib.redirect_stdout(io.StringIO()) as output:
            cli.main(args)
        return output.getvalue()

    def test_complete_gpu_setup_orders_bootstrap_acm_then_handoff(self):
        calls = []
        def wait(*args):
            calls.append("ready")
        def prepare(*args):
            calls.append("acm")
            return {"instance_id": GPU}
        def handoff_gpu(*args, **kwargs):
            calls.append("handoff")
            return {"owner": GPU, "hostname": handoff.HOST}
        with (mock.patch.object(acm, "prepare_gpu", side_effect=prepare),
              mock.patch.object(handoff, "handoff_gpu", side_effect=handoff_gpu),
              mock.patch.object(cli, "trusted_url") as unnecessary):
            url = cli.complete_gpu_setup(GPU_STACK, "us-east-1", GPU,
                                         manager=mock.Mock(), wait=wait)
        self.assertEqual(calls, ["ready", "acm", "handoff"])
        self.assertEqual(url, TRUSTED)
        unnecessary.assert_not_called()

    def test_acm_failure_blocks_eip_handoff(self):
        with (mock.patch.object(acm, "prepare_gpu",
                                side_effect=RuntimeError("ACM not ready")),
              mock.patch.object(handoff, "handoff_gpu") as move):
            with self.assertRaisesRegex(RuntimeError, "ACM not ready"):
                cli.complete_gpu_setup(GPU_STACK, "us-east-1", GPU,
                                       manager=mock.Mock(), wait=lambda *_: None)
        move.assert_not_called()

    def test_mismatched_instance_does_not_transfer_eip(self):
        with (mock.patch.object(acm, "prepare_gpu",
                                return_value={"instance_id": "i-000000001"}),
              mock.patch.object(handoff, "handoff_gpu") as move):
            with self.assertRaisesRegex(RuntimeError, "instance changed"):
                cli.complete_gpu_setup(GPU_STACK, "us-east-1", GPU,
                                       manager=mock.Mock(), wait=lambda *_: None)
        move.assert_not_called()

    def test_wrong_stack_or_region_rejected_without_aws(self):
        with (mock.patch.object(acm, "prepare_gpu") as prepare,
              mock.patch.object(handoff, "handoff_gpu") as move):
            with self.assertRaises(ValueError):
                cli.complete_gpu_setup("foreign-stack", "us-east-1", GPU)
            with self.assertRaises(ValueError):
                cli.complete_gpu_setup(GPU_STACK, "us-west-2", GPU)
        prepare.assert_not_called()
        move.assert_not_called()

    def test_gpu_stop_waits_for_stop_before_return_and_revocation(self):
        timeline = []
        states = [
            {"stack": GPU_STACK, "instance": GPU, "state": "running"},
            {"stack": GPU_STACK, "instance": GPU, "state": "stopped"},
        ]
        def run(args, **kwargs):
            if "stop-instances" in args:
                timeline.append("stop")
            elif "instance-stopped" in args:
                timeline.append("stopped")
        def finished(stack, iid):
            timeline.append("return-and-revoke")
            self.assertEqual((stack, iid), (GPU_STACK, GPU))
            return {"revoked": True}
        eip = {"InstanceId": GPU, "Tags": [
            {"Key": "StudioDcvInstanceId", "Value": GPU}]}
        with (mock.patch.object(cli, "describe", side_effect=states),
              mock.patch.object(cli, "run", side_effect=run),
              mock.patch.object(handoff, "_eip", return_value=eip),
              mock.patch.object(cli, "complete_gpu_stop", side_effect=finished)):
            self.command(["stop", "--stack", GPU_STACK])
        self.assertEqual(timeline, ["stop", "stopped", "return-and-revoke"])

    def test_unexpected_gpu_eip_owner_fails_before_stopping(self):
        info = {"stack": GPU_STACK, "instance": GPU, "state": "running"}
        with (mock.patch.object(cli, "describe", return_value=info),
              mock.patch.object(cli, "run") as run,
              mock.patch.object(handoff, "_eip", return_value={
                  "InstanceId": "i-foreign", "Tags": []}),
              contextlib.redirect_stderr(io.StringIO())):
            with self.assertRaises(SystemExit):
                cli.main(["stop", "--stack", GPU_STACK])
        run.assert_not_called()

    def test_complete_gpu_stop_restores_eip_then_revokes_exact_grant(self):
        timeline = []
        eip = {"InstanceId": GPU, "Tags": [
            {"Key": "StudioDcvInstanceId", "Value": GPU}]}
        with (mock.patch.object(handoff, "_eip", return_value=eip),
              mock.patch.object(handoff, "restore_cpu", side_effect=lambda *a, **kw:
                                (timeline.append("return") or {"owner": CPU})),
              mock.patch.object(acm, "revoke_gpu", side_effect=lambda *a:
                                (timeline.append("revoke") or {"revoked": True}))):
            result = cli.complete_gpu_stop(GPU_STACK, GPU, manager=mock.Mock())
        self.assertEqual(timeline, ["return", "revoke"])
        self.assertTrue(result["revoked"])

    def test_complete_gpu_stop_when_cpu_already_owns_ip_only_revokes(self):
        eip = {"InstanceId": CPU, "Tags": [
            {"Key": "StudioDcvInstanceId", "Value": CPU}]}
        with (mock.patch.object(handoff, "_eip", return_value=eip),
              mock.patch.object(handoff, "restore_cpu") as restore,
              mock.patch.object(acm, "revoke_gpu", return_value={"revoked": False}) as revoke):
            cli.complete_gpu_stop(GPU_STACK, GPU, manager=mock.Mock())
        restore.assert_not_called()
        revoke.assert_called_once()

    def test_gpu_restart_wires_setup_after_ec2_waiter(self):
        states = [
            {"stack": GPU_STACK, "instance": GPU, "state": "stopped"},
            {"stack": GPU_STACK, "instance": GPU, "state": "running"}]
        events = []
        def run(cmd, **kw):
            events.append("ec2-start" if "start-instances" in cmd else "ec2-wait")
        with (mock.patch.object(cli, "describe", side_effect=states),
              mock.patch.object(cli, "run", side_effect=run),
              mock.patch.object(cli, "complete_gpu_setup", side_effect=lambda *a:
                                (events.append("trusted-setup") or TRUSTED))):
            self.command(["start", "--stack", GPU_STACK])
        self.assertEqual(events, ["ec2-start", "ec2-wait", "trusted-setup"])

    def test_cpu_restart_verifies_owner_before_ec2_start(self):
        states = [
            {"stack": CPU_STACK, "instance": CPU, "state": "stopped"},
            {"stack": CPU_STACK, "instance": CPU, "state": "running"}]
        events = []
        with (mock.patch.object(cli, "describe", side_effect=states),
              mock.patch.object(cli, "trusted_owner", side_effect=lambda *a:
                                (events.append("owner-check") or (handoff.HOST, handoff.EIP_IP))),
              mock.patch.object(cli, "run", side_effect=lambda *a, **kw:
                                events.append("ec2")),
              mock.patch.object(cli, "wait_cpu_https", side_effect=lambda *a:
                                (events.append("tls") or TRUSTED))):
            self.command(["start", "--stack", CPU_STACK])
        self.assertEqual(events, ["owner-check", "ec2", "ec2", "tls"])

    def test_cpu_owner_mismatch_fails_before_ec2_start(self):
        vm = {"stack": CPU_STACK, "instance": CPU, "state": "stopped"}
        with (mock.patch.object(cli, "describe", return_value=vm),
              mock.patch.object(cli, "trusted_owner",
                                side_effect=ValueError("foreign EIP")),
              mock.patch.object(cli, "run") as run,
              contextlib.redirect_stderr(io.StringIO())):
            with self.assertRaises(SystemExit):
                cli.main(["start", "--stack", CPU_STACK])
        run.assert_not_called()

    def test_connect_auto_uses_trusted_url_for_approved_gpu(self):
        vm = {"stack": GPU_STACK, "instance": GPU, "state": "running"}
        with (mock.patch.object(cli, "describe", return_value=vm),
              mock.patch.object(cli, "trusted_url", return_value=TRUSTED) as tls,
              mock.patch.object(cli, "run") as run):
            out = self.command(["connect", "--stack", GPU_STACK])
        self.assertIn(TRUSTED, out)
        tls.assert_called_once_with(GPU_STACK, GPU)
        run.assert_not_called()

    def test_unapproved_stack_connect_retains_original_raw_ip_behavior(self):
        vm = {"stack": "other-cpu", "instance": "i-others", "state": "running",
              "public_ip": "198.51.100.10", "dcv_url": "https://198.51.100.10:8443"}
        with (mock.patch.object(cli, "describe", return_value=vm),
              mock.patch.object(cli, "trusted_url") as trusted):
            out = self.command(["connect", "--stack", "other-cpu"])
        self.assertIn("https://198.51.100.10:8443", out)
        trusted.assert_not_called()

    def test_readiness_retries_bootstrap_failure_but_not_permission_denial(self):
        attempts = []
        def check(*args):
            attempts.append("check")
            if len(attempts) < 3:
                raise ValueError("GPU readiness failed: bootstrap incomplete")
        with mock.patch.object(cli, "gpu_ready", side_effect=check):
            cli.wait_gpu_bootstrap(GPU_STACK, "us-east-1", attempts=3, sleep=lambda *_: None)
        self.assertEqual(len(attempts), 3)
        with mock.patch.object(cli, "gpu_ready",
                               side_effect=subprocess.CalledProcessError(
                                   254, "aws", stderr="AccessDenied")) as check:
            with self.assertRaises(subprocess.CalledProcessError):
                cli.wait_gpu_bootstrap(GPU_STACK, "us-east-1",
                                       attempts=3, sleep=lambda *_: None)
        check.assert_called_once()

    def test_ssm_session_retries_only_target_not_connected(self):
        commands = []
        replies = [
            mock.Mock(returncode=254, stderr="TargetNotConnected"),
            mock.Mock(returncode=0, stderr="")]
        def execute(cmd, **kwargs):
            commands.append(cmd)
            return replies.pop(0)
        cli.interactive_ssm(GPU, "us-east-1", execute=execute, sleep=lambda *_: None)
        self.assertEqual(len(commands), 2)
        self.assertIn("--no-cli-pager", commands[0])
        with self.assertRaisesRegex(RuntimeError, "AccessDenied"):
            cli.interactive_ssm(GPU, "us-east-1", execute=lambda *a, **kw:
                                mock.Mock(returncode=254, stderr="AccessDenied"),
                                sleep=lambda *_: None)

    def test_trusted_owner_refuses_ambiguous_stack_and_foreign_eip(self):
        with self.assertRaises(ValueError):
            cli.trusted_owner("different-stack", GPU)
        address = {"InstanceId": CPU, "Tags": [
            {"Key": "StudioDcvInstanceId", "Value": CPU}]}
        with (mock.patch.object(handoff, "_stack_iid", return_value=GPU),
              mock.patch.object(handoff, "_eip", return_value=address)):
            with self.assertRaisesRegex(ValueError, "not owned"):
                cli.trusted_owner(GPU_STACK, GPU)


if __name__ == "__main__":
    unittest.main()
