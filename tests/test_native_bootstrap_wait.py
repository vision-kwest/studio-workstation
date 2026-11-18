"""Bounded native RES readiness without speculative bootstrap repairs."""
import subprocess
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
        self.assertEqual(ready.call_count, 96)

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
