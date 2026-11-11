"""Issue #88: hostname plan must use independent EIP ownership."""
import io
import json
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch
from scripts import studio_workstation_cli as cli


class HostnamePlanTests(unittest.TestCase):
    def test_gpu_plan_uses_replacement_cpu_preview(self):
        preview = {"from": "i-replacement", "to": "i-gpu", "guard": {"ok": True}}
        with patch("runtime.independent_eip.gpu_preview", return_value=preview) as independent:
            with patch("runtime.issue43_hostname_handoff.gpu_preview",
                       side_effect=AssertionError("legacy preview must not be called")):
                with redirect_stdout(io.StringIO()) as output:
                    cli.main(["hostname-plan", "--stack", cli.GPU_ACCEPTANCE_STACK])
        self.assertEqual(json.loads(output.getvalue()), preview)
        independent.assert_called_once()

    def test_reject_other_stack_before_preview(self):
        with patch("runtime.independent_eip.gpu_preview") as preview:
            with self.assertRaisesRegex(ValueError, "guarded acceptance stack"):
                cli.main(["hostname-plan", "--stack", "studio-other"])
        preview.assert_not_called()


if __name__ == "__main__":
    unittest.main()
