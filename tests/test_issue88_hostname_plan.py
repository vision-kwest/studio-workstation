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


    def test_return_plan_uses_independent_replacement_owner(self):
        preview = {"from": "i-gpu", "to": "i-replacement",
                   "cpu_stack": "studio-workstation-cpu-rez77"}
        with patch("runtime.independent_eip.cpu_return_preview",
                   return_value=preview) as independent:
            with patch("runtime.issue43_hostname_handoff.cpu_return_preview",
                       side_effect=AssertionError("original CPU preview called")):
                with redirect_stdout(io.StringIO()) as output:
                    cli.main(["hostname-return-plan", "--gpu-instance-id",
                              "i-0123456789abcdef0"])
        self.assertEqual(json.loads(output.getvalue()), preview)
        independent.assert_called_once()

    def test_acm_prepare_uses_independent_owner(self):
        with patch("runtime.issue43_gpu_acm_setup.prepare_gpu",
                   return_value={"acm": "verified"}) as prepare:
            with redirect_stdout(io.StringIO()):
                cli.main(["hostname-acm-prepare", "--execute"])
        prepare.assert_called_once()
        self.assertTrue(prepare.call_args.kwargs["independent"])

    def test_handoff_uses_independent_implementation(self):
        with patch("runtime.independent_eip.handoff_gpu",
                   return_value={"owner": "i-gpu"}) as handoff:
            with patch("runtime.issue43_hostname_handoff.handoff_gpu",
                       side_effect=AssertionError("legacy handoff called")):
                with redirect_stdout(io.StringIO()):
                    cli.main(["hostname-handoff", "--execute"])
        handoff.assert_called_once()

    def test_return_uses_independent_implementation(self):
        with patch("runtime.independent_eip.restore_cpu",
                   return_value={"owner": "i-cpu"}) as restore:
            with redirect_stdout(io.StringIO()):
                cli.main(["hostname-return", "--gpu-instance-id",
                          "i-0baaf4feb21862d94", "--execute"])
        restore.assert_called_once()


if __name__ == "__main__":
    unittest.main()
