"""Issue #90: GPU stop cleanup remains recoverable after EC2 waiter timeout."""
import io
import subprocess
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch
from scripts import studio_workstation_cli as cli


class StopRecoveryTests(unittest.TestCase):
    def _run(self, initial, *, fail_wait=False, observed="stopped"):
        states = iter([{"instance": "i-0baaf4feb21862d94", "state": initial},
                       {"instance": "i-0baaf4feb21862d94", "state": observed},
                       {"instance": "i-0baaf4feb21862d94", "state": observed}])
        calls = []
        def run(cmd, **kwargs):
            calls.append(cmd)
            if fail_wait and cmd[1:4] == ["ec2", "wait", "instance-stopped"]:
                raise subprocess.CalledProcessError(255, cmd)
        with (patch.object(cli, "describe", side_effect=lambda *args: next(states)),
              patch.object(cli, "run", side_effect=run),
              patch.object(cli, "complete_gpu_stop", return_value={"revoked": True}) as cleanup,
              patch.object(cli, "aws"),
              redirect_stdout(io.StringIO())):
            cli.main(["stop", "--stack", cli.GPU_ACCEPTANCE_STACK])
        return calls, cleanup

    def test_already_stopped_gpu_completes_cleanup(self):
        calls, cleanup = self._run("stopped")
        self.assertFalse(any("stop-instances" in c for c in calls))
        cleanup.assert_called_once()

    def test_waiter_timeout_with_stopped_readback_completes_cleanup(self):
        _, cleanup = self._run("running", fail_wait=True)
        cleanup.assert_called_once()

    def test_waiter_timeout_while_still_stopping_fails_closed(self):
        with self.assertRaises((SystemExit, subprocess.CalledProcessError)):
            self._run("running", fail_wait=True, observed="stopping")


if __name__ == "__main__":
    unittest.main()
