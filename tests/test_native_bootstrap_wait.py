"""Bounded native RES readiness without speculative bootstrap repairs."""
import json
import os
from pathlib import Path
import subprocess
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from scripts import studio_workstation_cli as cli


class NativeBootstrapWaitTests(unittest.TestCase):
    def test_progress_can_continue_beyond_previous_retry_budget(self):
        failures = [ValueError('GPU readiness failed: native install in progress')] * 48
        with patch.object(cli, 'gpu_ready', side_effect=failures + [None]) as ready:
            cli.wait_gpu_bootstrap('cpu-test', 'us-east-1', gpu=False, sleep=lambda _: None)
        self.assertEqual(ready.call_count, 49)
        self.assertFalse(ready.call_args.kwargs['gpu'])

    def test_default_budget_is_finite_and_preserves_last_diagnostic(self):
        failure = ValueError('GPU readiness failed: SSM command=abc Checking RES marker')
        with patch.object(cli, 'gpu_ready', side_effect=failure) as ready:
            with self.assertRaisesRegex(RuntimeError, 'SSM command=abc Checking RES marker'):
                cli.wait_gpu_bootstrap('cpu-test', 'us-east-1', sleep=lambda _: None)
        self.assertEqual(ready.call_count, 240)

    def test_setup_window_allows_slow_readiness_but_bounds_elapsed_time_and_attempts(self):
        for success_at, attempts, expected_calls, expected_seconds in (
                (110, 240, 110, 2185), (None, 240, 180, 3600), (None, 2, 2, 25)):
            with self.subTest(success_at=success_at, attempts=attempts):
                clock = [0]
                def pause(seconds):
                    clock[0] += seconds
                def probe(*args, **kwargs):
                    pause(5)
                    if success_at is None or ready.call_count < success_at:
                        raise ValueError('GPU readiness failed: shared storage mount')
                with patch.object(cli.time, 'monotonic', side_effect=lambda: clock[0]), \
                        patch.object(cli, 'gpu_ready', side_effect=probe) as ready:
                    if success_at:
                        cli.wait_gpu_bootstrap('test', 'us-east-1', attempts=attempts, sleep=pause)
                    else:
                        with self.assertRaisesRegex(RuntimeError, 'shared storage mount'):
                            cli.wait_gpu_bootstrap('test', 'us-east-1', attempts=attempts, sleep=pause)
                self.assertEqual(ready.call_count, expected_calls)
                self.assertEqual(clock[0], expected_seconds)

    def test_explicit_small_budget_still_stops(self):
        with patch.object(cli, 'gpu_ready', side_effect=ValueError('GPU readiness timed out')) as ready:
            with self.assertRaises(RuntimeError):
                cli.wait_gpu_bootstrap('test', 'us-east-1', attempts=2, sleep=lambda _: None)
        self.assertEqual(ready.call_count, 2)

    def test_permission_denial_is_not_retried(self):
        denied = subprocess.CalledProcessError(1, ['aws'], stderr='AccessDenied')
        with patch.object(cli, 'gpu_ready', side_effect=denied) as ready:
            with self.assertRaises(subprocess.CalledProcessError):
                cli.wait_gpu_bootstrap('test', 'us-east-1', sleep=lambda _: None)
        self.assertEqual(ready.call_count, 1)

    def test_unexpected_validation_failure_is_not_retried(self):
        with patch.object(cli, 'gpu_ready', side_effect=ValueError('Ownership mismatch')) as ready:
            with self.assertRaisesRegex(ValueError, 'Ownership mismatch'):
                cli.wait_gpu_bootstrap('test', 'us-east-1', sleep=lambda _: None)
        self.assertEqual(ready.call_count, 1)


class InitialBootstrapFailureTests(unittest.TestCase):
    def probe(self, result, *, wait=False):
        pauses = []
        with patch.object(cli, "instance_id", return_value="i-test"), \
                patch.object(cli.time, "sleep"), \
                patch.object(cli, "aws", side_effect=[{"Command": {"CommandId": "probe-1"}}, result]) as aws:
            error = None
            try:
                if wait:
                    cli.wait_gpu_bootstrap("studio-test", "us-east-1", sleep=pauses.append)
                else:
                    cli.gpu_ready("studio-test", "us-east-1", gpu=False)
            except (RuntimeError, ValueError) as exc:
                error = exc
            self.assertEqual(aws.call_count, 2)
            args = aws.call_args_list[0].args
            commands = json.loads(args[args.index("--parameters") + 1])["commands"]
        return commands, error, pauses

    def test_native_probe_only_reports_confirmed_initial_failure(self):
        commands, error, _ = self.probe({"Status": "Success"})
        self.assertIsNone(error)
        prefix = "\n".join(commands[:commands.index("echo 'Checking RES desktop readiness marker'")])
        stub = '''systemctl() {
  case "$1" in
    is-failed) test "$UNIT_STATE" = failed ;;
    show) echo "$UNIT_RESULT" ;;
    is-system-running) echo "$SYSTEM_STATE"; return 1 ;;
  esac
}
'''
        with TemporaryDirectory() as directory:
            for unit, result, system, marker, expected in (
                    ("failed", "exit-code", "degraded", "", 42),
                    ("activating", "exit-code", "degraded", "", 0),
                    ("inactive", "success", "running", "", 0),
                    ("failed", "signal", "degraded", "", 0),
                    ("failed", "exit-code", "starting", "", 0),
                    ("failed", "exit-code", "stopping", "", 0),
                    ("failed", "exit-code", "degraded", "app_installed.lock", 0),
                    ("failed", "exit-code", "degraded", "linux_vdi_config_host_ready.lock", 0)):
                with self.subTest(unit=unit, result=result, system=system, marker=marker):
                    path = Path(directory) / marker if marker else None
                    if path:
                        path.touch()
                    script = stub + prefix.replace("/root/bootstrap/semaphore/", directory + "/")
                    completed = subprocess.run(["/bin/sh", "-c", script], capture_output=True, text=True,
                                               env={**os.environ, "UNIT_STATE": unit,
                                                    "UNIT_RESULT": result, "SYSTEM_STATE": system})
                    self.assertEqual(completed.returncode, expected, completed.stderr)
                    self.assertEqual("RES_BOOTSTRAP_FAILED" in completed.stdout, expected == 42)
                    if path:
                        path.unlink()

    def test_terminal_response_ends_wait_without_retry_or_guest_output(self):
        result = {"Status": "Failed", "ResponseCode": 42,
                  "StandardOutputContent": "RES_BOOTSTRAP_FAILED\nSECRET_OUTPUT",
                  "StandardErrorContent": "SECRET_ERROR"}
        _, error, pauses = self.probe(result, wait=True)
        self.assertIsInstance(error, RuntimeError)
        self.assertIn("cloud-final.service", str(error))
        self.assertIn("SSM command=probe-1", str(error))
        self.assertNotIn("SECRET", str(error))
        self.assertEqual(pauses, [])
        for override in ({"Status": "Cancelled"}, {"Status": "TimedOut"}, {"ResponseCode": 1},
                         {"StandardOutputContent": "prefix RES_BOOTSTRAP_FAILED suffix"}):
            with self.subTest(override=override):
                _, error, _ = self.probe({**result, **override})
                self.assertIsInstance(error, ValueError)

    def test_error_message_preserves_cause_without_subprocess_arguments(self):
        for stderr in (None, "AccessDenied"):
            error = subprocess.CalledProcessError(1, ["aws", "--secret", "SECRET_ARGUMENT"], stderr=stderr)
            self.assertNotIn("SECRET_ARGUMENT", cli.error_message(error))
            self.assertIn(stderr or "exit code 1", cli.error_message(error))
        self.assertEqual(cli.error_message(ValueError("Ownership mismatch")), "Ownership mismatch")
        self.assertEqual(cli.error_message(RuntimeError("RES bootstrap failed")), "RES bootstrap failed")


if __name__ == "__main__":
    unittest.main()
