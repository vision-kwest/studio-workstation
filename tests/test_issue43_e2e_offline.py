"""Task 5: end-to-end CLI command routing with a fully mocked AWS boundary.

No AWS credentials, network access, EC2 launch, or destructive API calls.
"""
import contextlib
import io
import subprocess
import unittest
from unittest import mock

from scripts import studio_workstation_cli as cli
from runtime import issue43_destroy
from runtime import independent_eip


CPU = cli.DEFAULT_STACK
GPU = cli.GPU_ACCEPTANCE_STACK
CPU_ID = "i-0bbfbdc878d3a2320"
GPU_ID = "i-0107981a62f313cfc"
TLS = "https://dcv.kwesidavis.com:8443"


class OfflineLifecycleJourney(unittest.TestCase):
    def invoke(self, *argv):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            cli.main(list(argv))
        return output.getvalue()

    def test_cpu_create_preview_and_daily_lifecycle(self):
        state = {"value": "stopped"}
        writes = []
        eip = {"owner": CPU_ID}

        def address(*args):
            if eip["owner"] is None:
                return {"AllocationId": independent_eip.legacy.EIP_ID, "Tags": []}
            return {"AllocationId": independent_eip.legacy.EIP_ID,
                    "InstanceId": CPU_ID, "AssociationId": "eipassoc-test",
                    "Tags": [{"Key": "StudioDcvInstanceId", "Value": CPU_ID}]}

        def release(*args):
            self.assertEqual(state["value"], "stopped")
            eip["owner"] = None
            return {"allocation": independent_eip.legacy.EIP_ID,
                    "state": "unassociated", "previous_owner": CPU_ID}

        def describe(stack, region):
            self.assertEqual(stack, CPU)
            return {"stack": CPU, "instance": CPU_ID, "state": state["value"],
                    "public_ip": "54.159.180.104", "dcv_url": TLS}

        def run(argv, *, capture=True):
            # Render and validation are mocked; no real subprocess or AWS call.
            if "start-instances" in argv:
                self.assertEqual(state["value"], "stopped")
                state["value"] = "running"
            elif "stop-instances" in argv:
                self.assertEqual(state["value"], "running")
                state["value"] = "stopped"
            writes.append(argv)
            return ""

        with (mock.patch.object(cli, "describe", side_effect=describe),
              mock.patch.object(cli, "run", side_effect=run),
              mock.patch.object(independent_eip, "_address", side_effect=address),
              mock.patch.object(independent_eip, "release", side_effect=release) as released,
              mock.patch.object(cli, "wait_cpu_https", return_value=TLS),
              mock.patch.object(cli, "trusted_url", return_value=TLS),
              mock.patch.object(issue43_destroy, "destroy_cpu",
                                return_value={"state": "DELETE_COMPLETE"}) as destroy):
            self.invoke("create", "--stack", "studio-workstation-cpu-review",
                        "--profile", "cpu", "--client-cidr", "203.0.113.9/32", "--dry-run")
            self.assertIn("DRY RUN", self.invoke("create", "--stack", "studio-workstation-cpu-review",
                           "--profile", "cpu", "--dry-run"))
            self.assertFalse(any("deploy" in cmd or "create-stack" in cmd for cmd in writes))
            self.assertIn(TLS, self.invoke("start", "--stack", CPU))
            self.assertIn(TLS, self.invoke("connect", "--stack", CPU))
            self.assertIn("running", self.invoke("status", "--stack", CPU, "--hostname", cli.TRUSTED_DCV_HOSTNAME))
            self.invoke("stop", "--stack", CPU)
            self.assertEqual(state["value"], "stopped")
            self.assertIn(TLS, self.invoke("start", "--stack", CPU))
            self.invoke("stop", "--stack", CPU)
            self.assertIn("unassociated", self.invoke("hostname-release", "--execute"))
            self.assertIsNone(eip["owner"])
            self.assertIn("DELETE_COMPLETE", self.invoke("destroy", "--stack", CPU,
                                                          "--delete-stack"))
            released.assert_called_once_with(cli.aws)
            destroy.assert_called_once_with(cli.aws, cli.run, CPU, cli.DEFAULT_REGION)

    def test_gpu_start_connect_stop_resume_destroy(self):
        state = {"value": "stopped"}
        events = []

        def describe(stack, region):
            self.assertEqual(stack, GPU)
            return {"stack": GPU, "instance": GPU_ID, "state": state["value"],
                    "public_ip": "54.159.180.104", "dcv_url": TLS}

        def run(argv, *, capture=True):
            events.append(argv)
            if "start-instances" in argv:
                state["value"] = "running"
            if "stop-instances" in argv:
                state["value"] = "stopped"
            return ""

        from runtime import issue43_hostname_handoff as handoff
        with (mock.patch.object(cli, "describe", side_effect=describe),
              mock.patch.object(cli, "run", side_effect=run),
              mock.patch.object(cli, "shared_gpu_source_ready") as preflight,
              mock.patch.object(cli, "gpu_restart_preflight") as expiration,
              mock.patch.object(cli, "complete_gpu_setup", return_value=TLS) as setup,
              mock.patch.object(cli, "complete_gpu_stop", return_value={"revoked": True}) as stop,
              mock.patch.object(cli, "trusted_url", return_value=TLS),
              mock.patch.object(handoff, "_eip", return_value={
                  "InstanceId": GPU_ID,
                  "Tags": [{"Key": "StudioDcvInstanceId", "Value": GPU_ID}]}),
              mock.patch.object(issue43_destroy, "destroy_gpu",
                                return_value={"state": "DELETE_COMPLETE"}) as destroy):
            self.assertIn(TLS, self.invoke("start", "--stack", GPU))
            self.assertIn(TLS, self.invoke("connect", "--stack", GPU))
            self.invoke("stop", "--stack", GPU)
            self.assertEqual(state["value"], "stopped")
            self.assertIn(TLS, self.invoke("start", "--stack", GPU))
            self.invoke("stop", "--stack", GPU)
            self.invoke("destroy", "--stack", GPU, "--delete-stack")
            self.assertEqual(preflight.call_count, 2)
            self.assertEqual(expiration.call_count, 2)
            self.assertEqual(setup.call_count, 2)
            self.assertEqual(stop.call_count, 2)
            destroy.assert_called_once_with(cli.aws, cli.run, GPU, cli.DEFAULT_REGION)
            self.assertEqual(sum("start-instances" in cmd for cmd in events), 2)
            self.assertEqual(sum("stop-instances" in cmd for cmd in events), 2)

    def test_fail_closed_when_gpu_guard_absent(self):
        from runtime import pr65_gpu_create
        with (mock.patch.object(cli, "run", return_value=""),
              mock.patch.object(cli, "aws", side_effect=subprocess.CalledProcessError(
                  254, "aws", stderr="Stack with id studio-gpu-cli-accept65 does not exist")),
              mock.patch.object(cli, "gpu_release_preflight"),
              mock.patch.object(cli, "shared_gpu_source_ready"),
              mock.patch.object(pr65_gpu_create, "create_guarded",
                                return_value={"guard": {"guard_armed": False}}),
              mock.patch.object(cli, "complete_gpu_setup") as setup,
              contextlib.redirect_stderr(io.StringIO())):
            with self.assertRaises(SystemExit):
                self.invoke("create", "--stack", GPU, "--profile", "gpu",
                            "--cleanup-token", cli.GPU_ACCEPTANCE_TOKEN,
                            "--client-cidr", "0.0.0.0/0", "--allow-public-dcv")
            setup.assert_not_called()


    def test_cpu_create_dispatches_only_reviewed_deploy(self):
        calls = []
        def run(argv, *, capture=True):
            calls.append(argv)
            return ""
        with (mock.patch.object(cli, "run", side_effect=run),
              mock.patch.object(cli, "aws", side_effect=subprocess.CalledProcessError(
                  254, "aws", stderr="Stack with id studio-workstation-cpu-review does not exist")),
              mock.patch.object(cli, "describe", return_value={
                  "stack": "studio-workstation-cpu-review", "instance": "i-cpu", "state": "running"})):
            self.invoke("create", "--stack", "studio-workstation-cpu-review",
                        "--profile", "cpu", "--client-cidr", "203.0.113.9/32")
        self.assertEqual(sum("deploy" in cmd for cmd in calls), 1)
        self.assertEqual(sum("validate-template" in cmd for cmd in calls), 1)
        self.assertFalse(any("create-stack" in cmd for cmd in calls))

    def test_gpu_create_requires_armed_guard_before_trusted_handoff(self):
        from runtime import pr65_gpu_create
        outcome = {"guard": {"guard_armed": True, "instance_id": GPU_ID}}
        with (mock.patch.object(cli, "run", return_value=""),
              mock.patch.object(cli, "aws", side_effect=subprocess.CalledProcessError(
                  254, "aws", stderr="Stack with id studio-gpu-cli-accept65 does not exist")),
              mock.patch.object(cli, "gpu_release_preflight") as release,
              mock.patch.object(cli, "shared_gpu_source_ready") as source,
              mock.patch.object(pr65_gpu_create, "create_guarded",
                                return_value=outcome) as guarded,
              mock.patch.object(cli, "complete_gpu_setup", return_value=TLS) as setup):
            result = self.invoke("create", "--stack", GPU, "--profile", "gpu",
                                 "--cleanup-token", cli.GPU_ACCEPTANCE_TOKEN,
                                 "--client-cidr", "0.0.0.0/0", "--allow-public-dcv")
        self.assertIn(TLS, result)
        release.assert_called_once()
        source.assert_called_once()
        guarded.assert_called_once()
        setup.assert_called_once_with(GPU, cli.DEFAULT_REGION, GPU_ID)


if __name__ == "__main__":
    unittest.main()
