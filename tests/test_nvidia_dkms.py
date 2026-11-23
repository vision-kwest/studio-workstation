"""Execute the overlay's GPU gate against fake native commands, without root/AWS."""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

from runtime import build_res_overlay as overlay

DCV = b'      /bin/bash "${SCRIPT_DIR}/../../dcv/linux/dcv_server.sh" -o $BASE_OS -g $GPU_FAMILY -s "${SCRIPT_DIR}/.."'
CURRENT, NEXT = "7.0.0-1014-aws", "7.0.0-1017-aws"
NATIVE = r'''
uname() { echo 7.0.0-1014-aws; }
nvidia-smi() { :; }
modinfo() { echo "${MODULE_VERSION:-595.91.07}"; }
apt() { echo "apt $*" >> "$TEST_ROOT/calls"; [[ "$FAIL" != apt ]]; }
dkms() {
  echo "dkms $*" >> "$TEST_ROOT/calls"
  [[ "$2 $3 $4 $5 $6" == '-m nvidia -v 595.91.07 -k' ]] || return 2
  if [[ "$1" == install ]]; then
    [[ "$FAIL" != dkms ]] || return 1
    [[ "$FAIL" == unregistered ]] || echo "nvidia/595.91.07, $7, x86_64: installed" > "$TEST_ROOT/status.$7"
    return 0
  fi
  cat "$TEST_ROOT/status.$7" || return 1
  [[ "$FAIL" != status ]]
}
'''


class NvidiaDkmsTests(unittest.TestCase):
    def gate(self, *, status="installed", autoinstall="yes", module="595.91.07", fail="", gpu="NVIDIA"):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "boot").mkdir()
            for kernel in ("7.0.0-1010-aws", CURRENT, NEXT):
                (root / "boot" / ("vmlinuz-" + kernel)).touch()
            conf = root / "src/nvidia-595.91.07/dkms.conf"
            conf.parent.mkdir(parents=True)
            conf.write_text('AUTOINSTALL="' + autoinstall + '"\n')
            (root / ("status." + CURRENT)).write_text(
                f"nvidia/595.91.07, {CURRENT}, x86_64: {status}\n" if status else "")
            script = overlay.patch_post_reboot_dpms(DCV).replace(DCV, b":").decode()
            for original, replacement in (("/boot/", str(root / "boot") + "/"),
                                          ("/usr/src/", str(root / "src") + "/"),
                                          ("/etc/X11/xorg.conf.d", str(root / "xorg"))):
                script = script.replace(original, replacement)
            result = subprocess.run(["bash", "-c", NATIVE + script], text=True, capture_output=True,
                                    env={**os.environ, "TEST_ROOT": directory, "STUDIO_STANDALONE": "true",
                                         "GPU_FAMILY": gpu, "MODULE_VERSION": module, "FAIL": fail})
            calls = (root / "calls").read_text() if (root / "calls").exists() else ""
            return result, calls, (root / "xorg/99-disable-dpms.conf").exists()

    def test_current_registration_is_required_before_building_next_kernel(self):
        for failure in ({"status": ""}, {"status": "added"}, {"status": "built"},
                        {"autoinstall": "no"}, {"module": "other"}, {"fail": "status"}):
            with self.subTest(failure=failure):
                result, calls, completed = self.gate(**failure)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("registration or AUTOINSTALL missing", result.stderr)
                self.assertNotIn("apt ", calls)
                self.assertFalse(completed)

    def test_installed_next_kernel_needs_headers_build_and_verified_registration(self):
        result, calls, completed = self.gate()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(f"apt install -y linux-headers-{NEXT}", calls)
        self.assertIn(f"dkms install -m nvidia -v 595.91.07 -k {NEXT}", calls)
        self.assertIn(f"dkms status -m nvidia -v 595.91.07 -k {NEXT}", calls)
        self.assertNotIn("1010", calls)
        self.assertTrue(completed)
        for failure in ("apt", "dkms", "unregistered"):
            with self.subTest(failure=failure):
                result, _, completed = self.gate(fail=failure)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("missing for installed kernel " + NEXT, result.stderr)
                self.assertFalse(completed)

    def test_cpu_skips_gpu_gate(self):
        result, calls, completed = self.gate(gpu="NONE", status="")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(calls, "")
        self.assertTrue(completed)

    def test_prerequisites_preserve_non_nvidia_behavior_and_reject_source_drift(self):
        source = b'  EVALUATED_GPU_DRIVER_PREREQUISITES=()\nprintf "%s\\n" "${EVALUATED_GPU_DRIVER_PREREQUISITES[@]}"'
        for gpu, expected in (("NVIDIA", "dkms\nlinux-headers-aws\n"), ("NONE", "\n"), ("AMD", "\n")):
            result = subprocess.run(["bash", "-c", overlay.patch_gpu_prerequisites(source).decode()],
                                    text=True, capture_output=True, env={**os.environ, "GPU_FAMILY": gpu})
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout, expected)
        with self.assertRaises(ValueError):
            overlay.patch_gpu_prerequisites(b"changed upstream")


if __name__ == "__main__":
    unittest.main()
