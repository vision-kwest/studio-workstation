"""Static contract for the single RES 2026.06 NFS/EFS standalone guard."""
from pathlib import Path
import re
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / "runtime/overrides/scripts/virtual-desktop-host/linux/install.sh"

class EfsSkipTests(unittest.TestCase):
    def test_standalone_guard_preserves_managed_res_path(self):
        text = SCRIPT.read_text()
        self.assertEqual(text.count('/bin/bash "${SCRIPT_DIR}/../../common/linux/nfs_utils.sh"'), 1)
        pattern = r'if \[\[ "\$\{STUDIO_STANDALONE\}" != "true" \]\]; then\s+(/bin/bash "\$\{SCRIPT_DIR\}/../../common/linux/nfs_utils.sh"[^\n]+)\s+else\s+echo "Standalone: skipping unused NFS/EFS mount helper"\s+fi'
        self.assertRegex(text, pattern)
        self.assertIn('/bin/bash "${SCRIPT_DIR}/../../common/linux/sssd.sh"', text)
        self.assertIn('/bin/bash "${SCRIPT_DIR}/../../common/linux/s3_mountpoint.sh"', text)
        self.assertIn('install_prerequisites', text)
        self.assertIn('install_post_reboot.sh', text)

    def test_guard_logic(self):
        text=SCRIPT.read_text()
        block=re.search(r'    if \[\[ "\$\{STUDIO_STANDALONE\}" != "true" \]\]; then\n(.*?)\n    fi',text,re.S)
        self.assertIsNotNone(block)
        import subprocess
        for value,expected in (("true","skip"),("false","run"),("", "run")):
            shell='STUDIO_STANDALONE='+repr(value)+'; if [[ "${STUDIO_STANDALONE}" != "true" ]]; then echo run; else echo skip; fi'
            result=subprocess.run(["bash","-c",shell],check=True,capture_output=True,text=True)
            self.assertEqual(result.stdout.strip(),expected)

if __name__ == "__main__":
    unittest.main()
