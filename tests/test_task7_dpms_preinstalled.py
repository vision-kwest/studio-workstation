"""Verify the pinned RES post-reboot DPMS repair, including actual Bash output."""
import importlib.util
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("overlay", ROOT/"runtime/build_res_overlay.py")
overlay = importlib.util.module_from_spec(spec)
spec.loader.exec_module(overlay)

OLD = b'      /bin/bash "${SCRIPT_DIR}/../../dcv/linux/dcv_server.sh" -o $BASE_OS -g $GPU_FAMILY -s "${SCRIPT_DIR}/.."'

class DPMSPreinstalledTests(unittest.TestCase):
    def test_standalone_only_and_pinned_anchor(self):
        result = overlay.patch_post_reboot_dpms(OLD)
        self.assertEqual(result.count(OLD), 1)
        self.assertIn(b'if [[ "${STUDIO_STANDALONE}" == "true" ]]', result)
        self.assertIn(b"printf '%s\\n'", result)
        with self.assertRaises(ValueError):
            overlay.patch_post_reboot_dpms(b"upstream drift")

    def test_exact_printf_produces_multiline_xorg_config(self):
        patched = overlay.patch_post_reboot_dpms(OLD).decode()
        line = next(line.strip() for line in patched.splitlines() if line.strip().startswith("printf "))
        with tempfile.TemporaryDirectory() as d:
            path = Path(d)/"99-disable-dpms.conf"
            line = line.replace("/etc/X11/xorg.conf.d/99-disable-dpms.conf", str(path))
            subprocess.run(["bash", "-c", line], check=True)
            self.assertEqual(path.read_bytes(),
                b'Section "Extensions"\n    Option "DPMS" "Disable"\nEndSection\n')

if __name__ == "__main__":
    unittest.main()
