import importlib.util
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location("overlay", Path(__file__).resolve().parents[1] / "runtime/build_res_overlay.py")
overlay = importlib.util.module_from_spec(spec)
spec.loader.exec_module(overlay)

class XorgRegressionTests(unittest.TestCase):
    def test_dpms_patch_requires_pinned_source(self):
        with self.assertRaises(ValueError):
            overlay.patch_dcv_dpms(b"unknown")
    def test_xhost_patch_requires_pinned_source(self):
        with self.assertRaises(ValueError):
            overlay.patch_xserver_dcv_access(b"unknown")
    def test_xhost_is_standalone_only(self):
        original = b'        return "SI:localuser:dcv" in xhost_output'
        result = overlay.patch_xserver_dcv_access(original)
        self.assertIn(b'STUDIO_STANDALONE', result)
        self.assertIn(b'+SI:localuser:dcv', result)
        self.assertIn(original, result)
        self.assertNotIn(b'+SI:localuser:root', result)
