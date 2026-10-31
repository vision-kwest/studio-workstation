"""Regression checks for DCV GL package selection. Run: python3 -m unittest discover -s tests"""
import importlib.util
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("dcv_repair", ROOT / "scripts/repair_gpu_dcv_gl_selection.py")
MOD = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MOD)

class TestDcvSelection(unittest.TestCase):
    def test_selects_gl_not_gltest(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            packages = root / "packages"
            packages.mkdir()
            for name in ("nice-dcv-gl_2026_amd64.ubuntu2404.deb",
                         "nice-dcv-gltest_2026_amd64.ubuntu2404.deb"):
                (packages / name).touch()
            mock = root / "dpkg-deb"
            mock.write_text('#!/bin/bash\ncase "$2" in *gltest*) echo nice-dcv-gltest;; *) echo nice-dcv-gl;; esac\n')
            mock.chmod(0o755)
            script = "set -euo pipefail\nshopt -s nullglob\n" + MOD.NEW + '\nprintf "%s\\n" "${gl[0]}"\n'
            env = __import__("os").environ.copy()
            env["PATH"] = str(root) + ":" + env["PATH"]
            p = subprocess.run(["bash", "-c", script], cwd=root, env=env, capture_output=True, text=True)
            self.assertEqual(p.returncode, 0, p.stderr)
            self.assertEqual(p.stdout.splitlines()[-1], "packages/nice-dcv-gl_2026_amd64.ubuntu2404.deb")

    def test_rejects_missing_gl(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "packages").mkdir()
            script = "set -euo pipefail\nshopt -s nullglob\n" + MOD.NEW
            p = subprocess.run(["bash", "-c", script], cwd=root, capture_output=True, text=True)
            self.assertEqual(p.returncode, 43)

    def test_preserves_cpu_dcv_logic(self):
        original = """name: studio-gpu-dcv-res-stage
phases:
- name: build
  steps:
  - name: InstallDCV
    action: ExecuteBash
    inputs:
      commands:
      - |
        set -euxo pipefail
        gl=(packages/nice-dcv-gl*_amd64.ubuntu2404.deb)
        test "${#gl[@]}" -eq 1
        printf "%s  %s\\n" "$expected" dcv.tgz | sha256sum -c -
        apt-get install -y "./${server[0]}" "./${viewer[0]}" "./${xdcv[0]}" "./${gl[0]}"
  - name: RebootAfterDCV
    action: Reboot
"""
        doc = MOD.generate(original)
        MOD.validate(doc)
        cmd = doc["phases"][0]["steps"][0]["inputs"]["commands"][0]
        self.assertIn('package_name=$(dpkg-deb -f "$candidate" Package)', cmd)
        self.assertIn('apt-get install -y', cmd)
        self.assertIn('sha256sum -c -', cmd)

if __name__ == "__main__":
    unittest.main()
