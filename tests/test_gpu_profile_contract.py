"""Offline GPU profile contracts: reuse original RES NVIDIA path, no AWS writes."""
import json
import sys
import unittest
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"runtime"))
from render_workstation import render

class GpuProfileContracts(unittest.TestCase):
    def test_gpu_uses_upstream_nvidia_flag(self):
        cpu=render("studio-release-bucket","releases/v0.1.3/res-2026.06","workstation","cpu")
        gpu=render("studio-release-bucket","releases/v0.1.3/res-2026.06","workstation","gpu")
        self.assertIn("-g NONE -e standalone",cpu)
        self.assertIn("-g NVIDIA -e standalone",gpu)
        self.assertIn("scripts/virtual-desktop-host/linux/install.sh",gpu)
        self.assertNotIn("-g NVIDIA -e standalone",cpu)
    def test_gl_uses_upstream_with_standalone_family_adapter(self):
        gl=(ROOT/"runtime/overrides/ideabootstrap/dcv/linux/gl.py").read_text()
        self.assertIn('os.environ.get("STUDIO_STANDALONE") == "true"',gl)
        self.assertIn('os.environ["STUDIO_GPU_FAMILIES"]',gl)
        self.assertIn("cluster_settings.get_setting(",gl)
    def test_gpu_template_profile_is_g4dn_only(self):
        t=json.loads((ROOT/"runtime/workstation-template.json").read_text())
        self.assertEqual(t["Resources"]["Workstation"]["Properties"]["UserData"],"RENDER_OFFLINE")
        # renderer switches instance type to g4dn.xlarge, never changes shared CPU template
        source=(ROOT/"runtime/render_workstation.py").read_text()
        self.assertIn('["g4dn.xlarge"]',source)
        self.assertIn('a.profile == "gpu"',source)
    def test_invalid_profile_fails(self):
        with self.assertRaises(ValueError):
            render("studio-release-bucket","releases/v0.1.3/res-2026.06","workstation","unknown")

if __name__=="__main__":
    unittest.main()
