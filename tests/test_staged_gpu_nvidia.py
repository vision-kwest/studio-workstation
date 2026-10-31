"""Regression tests for staged RES GPU installer. Run with python3 -m unittest discover -s tests."""
import importlib.util
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("staged_gpu", ROOT / "scripts/prepare_gpu_nvidia_staged.py")
MOD = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MOD)

class TestStagedGpu(unittest.TestCase):
    def test_reboot_before_install(self):
        source = {"name": "studio-gpu-nvidia-res-stage",
                  "phases": [{"name": "build", "steps": [
                      {"name": "InstallResNvidiaGrid"},
                      {"name": "RebootAfterNvidia"},
                      {"name": "ConfirmNvidia", "action": "ExecuteBash",
                       "inputs": {"commands": ["echo confirmed"]}}]}]}
        result = MOD.generate(source)
        MOD.validate(result)
        steps = result["phases"][0]["steps"]
        self.assertEqual(steps[1]["name"], "RebootAfterNouveau")
        self.assertIn("update-initramfs", steps[0]["inputs"]["commands"][0])
        self.assertIn("nouveau remains loaded", steps[2]["inputs"]["commands"][0])
        self.assertNotIn("|| true", steps[2]["inputs"]["commands"][0])
        self.assertIn("|| return $?", MOD.PATCH)
        self.assertIn("chr(10).join", MOD.PATCH)

if __name__ == "__main__":
    unittest.main()
