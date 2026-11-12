"""Regression for slow RES GPU bootstrap after EC2 launch (Issue #84)."""
import unittest
from unittest.mock import patch
from scripts import studio_workstation_cli as cli


class GpuBootstrapWaitTests(unittest.TestCase):
    def test_eventually_ready_after_previous_twelve_attempt_window(self):
        checks = []
        def ready(stack, region):
            checks.append((stack, region))
            if len(checks) < 16:
                raise ValueError("GPU readiness failed: RES still bootstrapping")
        with patch.object(cli, "gpu_ready", side_effect=ready):
            cli.wait_gpu_bootstrap("studio-gpu-cli-accept65", "us-east-1",
                                   sleep=lambda _: None)
        self.assertEqual(len(checks), 16)

    def test_terminal_failure_fails_closed_without_retry(self):
        checks = []
        def ready(stack, region):
            checks.append(True)
            raise ValueError("GPU ownership mismatch")
        with patch.object(cli, "gpu_ready", side_effect=ready):
            with self.assertRaisesRegex(ValueError, "ownership mismatch"):
                cli.wait_gpu_bootstrap("studio-gpu-cli-accept65", "us-east-1",
                                       sleep=lambda _: None)
        self.assertEqual(len(checks), 1)

    def test_timeout_remains_bounded(self):
        with patch.object(cli, "gpu_ready",
                          side_effect=ValueError("GPU readiness failed")) as ready:
            with self.assertRaisesRegex(RuntimeError, "expiry schedules remain armed"):
                cli.wait_gpu_bootstrap("studio-gpu-cli-accept65", "us-east-1",
                                       attempts=3, sleep=lambda _: None)
        self.assertEqual(ready.call_count, 3)


if __name__ == "__main__":
    unittest.main()
