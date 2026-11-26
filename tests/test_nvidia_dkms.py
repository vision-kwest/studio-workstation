"""Execute the overlay's GPU gate against fake native commands, without root/AWS."""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

from runtime import build_res_overlay as overlay

DCV = b'      /bin/bash "${SCRIPT_DIR}/../../dcv/linux/dcv_server.sh" -o $BASE_OS -g $GPU_FAMILY -s "${SCRIPT_DIR}/.."'
CURRENT, NEXT = "7.0.0-1014-aws", "7.0.0-1017-aws"
VERSIONS = ("595.91.07", "620.70.06")
NATIVE = r'''
uname() { echo "$CURRENT_KERNEL"; }
nvidia-smi() { return 99; }  # The new module need not be loaded before RES reboots.
modinfo() {
  if [[ "$2" == "$CURRENT_KERNEL" ]]; then
    printf '%s\n' "$MODULE_VERSION"
    [[ "$FAIL" != modinfo ]]
  else
    printf '%s\n' "$NEXT_MODULE_VERSION"
    [[ "$FAIL" != modinfo-next ]]
  fi
}
apt() { echo "apt $*" >> "$TEST_ROOT/calls"; [[ "$FAIL" != apt ]]; }
dkms() {
  echo "dkms $*" >> "$TEST_ROOT/calls"
  [[ "$2 $3 $4 $5 $6" == "-m nvidia -v $DRIVER_VERSION -k" ]] || return 2
  if [[ "$1" == install ]]; then
    [[ "$FAIL" != dkms ]] || return 1
    [[ "$FAIL" == unregistered ]] || echo "nvidia/$DRIVER_VERSION, $7, x86_64: installed" > "$TEST_ROOT/status.$7"
    return 0
  fi
  cat "$TEST_ROOT/status.$7" || return 1
  [[ "$FAIL" != status ]]
}
'''


class NvidiaDkmsTests(unittest.TestCase):
    def gate(self, *, version=VERSIONS[0], status="installed", autoinstall="yes",
             module=None, next_module=None, fail="", gpu="NVIDIA"):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "boot").mkdir()
            for kernel in ("7.0.0-1010-aws", CURRENT, NEXT):
                (root / "boot" / ("vmlinuz-" + kernel)).touch()
            conf = root / f"src/nvidia-{version}/dkms.conf"
            conf.parent.mkdir(parents=True)
            conf.write_text('AUTOINSTALL="' + autoinstall + '"\n')
            (root / ("status." + CURRENT)).write_text(
                f"nvidia/{version}, {CURRENT}, x86_64: {status}\n" if status else "")
            script = overlay.patch_post_reboot_dpms(DCV).replace(DCV, b":").decode()
            for original, replacement in (("/boot/", str(root / "boot") + "/"),
                                          ("/usr/src/", str(root / "src") + "/"),
                                          ("/etc/X11/xorg.conf.d", str(root / "xorg"))):
                script = script.replace(original, replacement)
            result = subprocess.run(["bash", "-c", NATIVE + script], text=True, capture_output=True,
                                    env={**os.environ, "TEST_ROOT": directory, "STUDIO_STANDALONE": "true",
                                         "CURRENT_KERNEL": CURRENT, "GPU_FAMILY": gpu,
                                         "DRIVER_VERSION": version,
                                         "MODULE_VERSION": version if module is None else module,
                                         "NEXT_MODULE_VERSION": version if next_module is None else next_module,
                                         "FAIL": fail})
            calls = (root / "calls").read_text() if (root / "calls").exists() else ""
            return result, calls, (root / "xorg/99-disable-dpms.conf").exists()

    def test_current_registration_is_required_before_building_next_kernel(self):
        for failure in ({"status": ""}, {"status": "added"}, {"status": "built"},
                        {"autoinstall": "no"}, {"module": ""}, {"module": "other"},
                        {"module": "../620.70.06"}, {"module": "595.91.07\n620.70.06"},
                        {"module": VERSIONS[1]}, {"fail": "modinfo"}, {"fail": "status"}):
            with self.subTest(failure=failure):
                result, calls, completed = self.gate(**failure)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("registration or AUTOINSTALL missing", result.stderr)
                self.assertNotIn("apt ", calls)
                self.assertFalse(completed)

    def test_installed_next_kernel_needs_headers_build_and_verified_registration(self):
        for version in VERSIONS:
            with self.subTest(version=version):
                result, calls, completed = self.gate(version=version)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn(f"apt install -y linux-headers-{NEXT}", calls)
                self.assertIn(f"dkms install -m nvidia -v {version} -k {NEXT}", calls)
                self.assertIn(f"dkms status -m nvidia -v {version} -k {NEXT}", calls)
                self.assertNotIn("1010", calls)
                self.assertTrue(completed)
        for failure in ({"fail": "apt"}, {"fail": "dkms"}, {"fail": "unregistered"},
                        {"fail": "modinfo-next"}, {"next_module": VERSIONS[0]}):
            with self.subTest(failure=failure):
                result, _, completed = self.gate(version=VERSIONS[1], **failure)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("missing for installed kernel " + NEXT, result.stderr)
                self.assertFalse(completed)

    def test_cpu_and_amd_skip_nvidia_gate(self):
        for gpu in ("NONE", "AMD"):
            with self.subTest(gpu=gpu):
                result, calls, completed = self.gate(gpu=gpu, status="", fail="modinfo")
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(calls, "")
                self.assertTrue(completed)

    def test_prerequisites_are_independent_of_failing_legacy_batch(self):
        # Match RES's unset-IFS tail and unchecked caller, explicitly without set -e.
        source = br'''set +e
DKMS_INSTALLED=0
apt() {
  printf 'apt'; printf ' %s' "$@"; printf '\n'
  if [[ "$3" == dkms ]]; then
    [[ "$FAIL_EARLY" != true ]] || return 23
    DKMS_INSTALLED=1
    return 0
  fi
  return 42  # Atomic legacy APT failure: linux-modules-extra is unavailable.
}
install_gpu_driver_prerequisites() {
  IFS=$'\n'
  EVALUATED_GPU_DRIVER_PREREQUISITES=()
  EVALUATED_GPU_DRIVER_PREREQUISITES+=(gcc linux-headers-7.0.0-1014-aws linux-modules-extra-7.0.0-1014-aws)
  DEBIAN_FRONTEND=noninteractive apt install -y ${EVALUATED_GPU_DRIVER_PREREQUISITES[*]}
  unset IFS
}
install_gpu_driver_prerequisites
printf 'driver dkms=%s\n' "$DKMS_INSTALLED"
'''
        early = 'apt install -y dkms linux-headers-aws'
        legacy = 'apt install -y gcc linux-headers-7.0.0-1014-aws linux-modules-extra-7.0.0-1014-aws'
        for gpu, fail, code, expected in (
                ("NVIDIA", "false", 0, [early, legacy, 'driver dkms=1']),
                ("NVIDIA", "true", 23, [early]),
                ("NONE", "true", 0, [legacy, 'driver dkms=0']),
                ("AMD", "true", 0, [legacy, 'driver dkms=0'])):
            with self.subTest(gpu=gpu, fail_early=fail):
                result = subprocess.run(["bash", "-c", overlay.patch_gpu_prerequisites(source).decode()],
                                        text=True, capture_output=True,
                                        env={**os.environ, "GPU_FAMILY": gpu, "FAIL_EARLY": fail})
                self.assertEqual(result.returncode, code, result.stderr)
                self.assertEqual(result.stdout.splitlines(), expected)
        with self.assertRaises(ValueError):
            overlay.patch_gpu_prerequisites(b"changed upstream")


if __name__ == "__main__":
    unittest.main()
