"""Entirely offline, mocked OpenUSD tools and Python ABI contract tests."""
import copy
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest import mock

from scripts import check_openusd_rez as smoke

USD_WORLD = '#usda 1.0\n(\n defaultPrim = "World"\n)\ndef Xform "World"\n{\n}\n'


class ToolRunner:
    def __init__(self, info):
        self.calls = []
        self.info = info
        self.outputs = {"usdcat": USD_WORLD, "usdchecker": "Success"}
        self.fail = {}
        self.timeout = None

    def __call__(self, command, **kwargs):
        self.calls.append((command, kwargs))
        action = command[3]
        if action == self.timeout:
            raise subprocess.TimeoutExpired(command, kwargs["timeout"])
        if action in self.fail:
            return subprocess.CompletedProcess(command, self.fail[action],
                                               "", self.fail[action] and "deliberate failure")
        if action == "/usr/bin/python3":
            out = smoke.PROBE_TAG + json.dumps(self.info) + "\n"
        else:
            out = self.outputs.get(action, "")
        return subprocess.CompletedProcess(command, 0, out, "")


class OpenUsdRezSmokeTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.work = Path(tmp.name)
        self.root = self.work / "packages"
        self.pkg = self.root / "prod" / "usd" / "26.05"
        self.usd = self.pkg / "lib/python/pxr/Usd"
        self.usd.mkdir(parents=True)
        (self.pkg / "package.py").write_text(
            'name = "usd"\nversion = "26.05"\n'
            'requires = ["platform-linux", "arch-x86_64"]\n',
            encoding="utf-8")
        (self.pkg / "manifest.json").write_text(json.dumps({
            "package_name": "usd", "package_version": "26.05",
            "python_version": "3.12.3", "openusd_git_tag": "v26.05",
        }), encoding="utf-8")
        (self.usd / "__init__.py").write_text("# fixture\n", encoding="utf-8")
        self.native = self.usd / "_usd.cpython-312-x86_64-linux-gnu.so"
        self.native.write_bytes(b"mock compiled extension, never imported")
        self.launcher = self.work / "studio-rez-env"
        self.launcher.write_text("#!/bin/bash\nexit 0\n")
        self.launcher.chmod(0o755)
        self.mountinfo = (f"25 10 0:55 / {self.root} ro,nosuid,nodev - "
                          "nfs4 127.0.0.1:/ rw,vers=4.1\n")
        self.info = {
            "python_implementation": "cpython",
            "python_version": [3, 12, 3],
            "soabi": "cpython-312-x86_64-linux-gnu",
            "usd_version": [0, 26, 5],
            "pxr_usd_file": str(self.usd / "__init__.py"),
            "pxr_native_file": str(self.native),
            "rez_packages_path": str(self.root / "prod"),
            "rez_config_file": "/opt/studio/studio-rez/config/rezconfig.prod.py",
        }
        self.runner = ToolRunner(self.info)

    def run_smoke(self):
        return smoke.smoke(root=self.root, launcher=self.launcher,
                           fixture=smoke.FIXTURE, mountinfo=self.mountinfo,
                           runner=self.runner)

    def test_success_calls_exact_rez_tools_and_import_probe_without_shell(self):
        result = self.run_smoke()
        self.assertEqual(result["status"], "verified")
        self.assertEqual(result["rez_request"], "usd-26.05")
        self.assertEqual(result["usdcat"], "passed")
        self.assertEqual(result["usdchecker"], "passed")
        self.assertTrue(result["python_abi"]["verified"])
        self.assertEqual(result["python_abi"]["python_version"], "3.12.3")
        self.assertIn("UNVERIFIED", result["efs_identity"])
        self.assertEqual([item[0][3] for item in self.runner.calls],
                         ["usdcat", "usdchecker", "/usr/bin/python3"])
        for command, opts in self.runner.calls:
            self.assertEqual(command[:3], [str(self.launcher), "usd-26.05", "--"])
            self.assertTrue(opts["capture_output"])
            self.assertEqual(opts["timeout"], smoke.TIMEOUT)
            self.assertFalse(opts["check"])
            self.assertNotIn("shell", opts)
        self.assertEqual(self.runner.calls[0][0][-1], str(smoke.FIXTURE))
        self.assertEqual(self.runner.calls[1][0][-1], str(smoke.FIXTURE))
        self.assertIn('importlib.import_module("pxr.Usd._usd")',
                      self.runner.calls[2][0][-1])
        self.assertIn("Usd.Stage.CreateInMemory()", self.runner.calls[2][0][-1])

    def test_fails_closed_when_only_dev_package_exists(self):
        shutil.rmtree(self.pkg)
        (self.root / "dev/usd/26.05").mkdir(parents=True)
        with self.assertRaisesRegex(RuntimeError, "preflight failed"):
            self.run_smoke()
        self.assertFalse(self.runner.calls)

    def test_missing_and_writable_efs_mount_rejected_before_rez(self):
        for mi in ("", self.mountinfo.replace(" ro,nosuid", " rw,nosuid"),
                   self.mountinfo.replace("nfs4 127", "ext4 127")):
            with self.subTest(mountinfo=mi):
                self.mountinfo = mi
                with self.assertRaisesRegex(RuntimeError, "preflight failed"):
                    self.run_smoke()
                self.assertFalse(self.runner.calls)

    def test_package_identity_drift_fails_before_external_commands(self):
        (self.pkg / "manifest.json").write_text(json.dumps({
            "package_name": "usd", "package_version": "26.04"
        }))
        with self.assertRaisesRegex(RuntimeError, "manifest identity"):
            self.run_smoke()
        self.assertFalse(self.runner.calls)

    def test_missing_launcher_rejected(self):
        self.launcher.unlink()
        with self.assertRaisesRegex(ValueError, "launcher is missing"):
            self.run_smoke()
        self.assertFalse(self.runner.calls)

    def test_symlinked_or_nonexecutable_launcher_rejected(self):
        self.launcher.chmod(0o644)
        with self.assertRaisesRegex(ValueError, "launcher is missing"):
            self.run_smoke()
        self.launcher.unlink()
        self.launcher.symlink_to(self.pkg / "package.py")
        with self.assertRaisesRegex(ValueError, "launcher is missing"):
            self.run_smoke()
        self.assertFalse(self.runner.calls)

    def test_bad_usd_fixture_is_rejected_before_rez(self):
        file = self.work / "broken.usda"
        file.write_text("#usda 1.0\n")
        with self.assertRaisesRegex(ValueError, "expected scene"):
            smoke.smoke(root=self.root, launcher=self.launcher, fixture=file,
                        mountinfo=self.mountinfo, runner=self.runner)
        self.assertFalse(self.runner.calls)

    def test_usdcat_nonzero_fails_and_skips_remaining_checks(self):
        self.runner.fail["usdcat"] = 31
        with self.assertRaisesRegex(RuntimeError, "usdcat failed .exit 31."):
            self.run_smoke()
        self.assertEqual(len(self.runner.calls), 1)

    def test_usdcat_unexpected_output_fails(self):
        self.runner.outputs["usdcat"] = "garbled"
        with self.assertRaisesRegex(RuntimeError, "did not contain expected"):
            self.run_smoke()
        self.assertEqual(len(self.runner.calls), 1)

    def test_usdchecker_failure_stops_before_python_import(self):
        self.runner.fail["usdchecker"] = 7
        with self.assertRaisesRegex(RuntimeError, "usdchecker failed .exit 7."):
            self.run_smoke()
        self.assertEqual(len(self.runner.calls), 2)

    def test_python_import_failure_reports_diagnostic(self):
        self.runner.fail["/usr/bin/python3"] = 1
        with self.assertRaisesRegex(RuntimeError, "Python ABI/import probe failed"):
            self.run_smoke()
        self.assertEqual(len(self.runner.calls), 3)

    def test_missing_or_duplicated_python_probe_output_rejected(self):
        with mock.patch.object(smoke, "PROBE_TAG", "DIFFERENT="):
            # The test runner uses the changed tag, so force malformed JSON instead.
            pass
        for output in ("", "junk\n", smoke.PROBE_TAG + "{}\n" + smoke.PROBE_TAG + "{}\n",
                       smoke.PROBE_TAG + "{bad json\n"):
            with self.subTest(output=output):
                self.runner.calls.clear()
                with mock.patch.object(smoke, "_command", side_effect=[
                        USD_WORLD, "Success", output]):
                    with self.assertRaisesRegex(ValueError, "probe output"):
                        self.run_smoke()

    def test_python_runtime_version_and_soabi_must_match(self):
        for change in ({"python_implementation": "pypy"},
                       {"python_version": "3.12"},
                       {"soabi": ""},
                       {"soabi": "cpython-311-x86_64-linux-gnu"}):
            with self.subTest(change=change):
                self.info.update(change)
                with self.assertRaisesRegex(ValueError, "Python|CPython"):
                    self.run_smoke()
                self.info.update({
                    "python_implementation": "cpython",
                    "python_version": [3, 12, 3],
                    "soabi": "cpython-312-x86_64-linux-gnu",
                })

    def test_binary_usd_version_must_match_requested_rez_version(self):
        self.info["usd_version"] = [0, 25, 11]
        with self.assertRaisesRegex(ValueError, "does not match usd-26.05"):
            self.run_smoke()

    def test_rez_stage_must_be_prod_never_dev(self):
        self.info["rez_packages_path"] = str(self.root / "dev")
        with self.assertRaisesRegex(ValueError, "outside the production"):
            self.run_smoke()

    def test_malformed_or_nonprod_rez_config_rejected(self):
        for filename in ("", "/opt/studio/studio-rez/config/rezconfig.py",
                         "relative/config/rezconfig.prod.py"):
            with self.subTest(config=filename):
                self.info["rez_config_file"] = filename
                with self.assertRaisesRegex(ValueError, "production configuration"):
                    self.run_smoke()

    def test_shadowed_pxr_module_outside_selected_package_rejected(self):
        self.info["pxr_usd_file"] = "/usr/local/lib/python3.12/site-packages/pxr/Usd/__init__.py"
        with self.assertRaisesRegex(ValueError, "outside requested USD package"):
            self.run_smoke()

    def test_shadowed_or_non_native_usd_extension_rejected(self):
        for native in ("/tmp/pxr/_usd.so",
                       str(self.usd / "_usd.py"), ""):
            with self.subTest(native=native):
                self.info["pxr_native_file"] = native
                with self.assertRaisesRegex(ValueError, "module path|outside requested|not a shared library"):
                    self.run_smoke()

    def test_timeout_is_bounded_and_fails_closed(self):
        self.runner.timeout = "usdcat"
        with self.assertRaisesRegex(RuntimeError, "usdcat timed out after 60s"):
            self.run_smoke()
        self.assertEqual(len(self.runner.calls), 1)

    def test_missing_stdout_abi_record_rejected(self):
        with mock.patch.object(smoke, "_command", side_effect=[
                USD_WORLD, "Success", "import succeeded but no report\n"]):
            with self.assertRaisesRegex(ValueError, "output missing"):
                self.run_smoke()

    def test_real_example_fixture_has_valid_expected_tokens(self):
        content = smoke.FIXTURE.read_text(encoding="utf-8")
        self.assertIn("#usda 1.0", content)
        self.assertIn('def Xform "World"', content)

    def test_cli_error_returns_nonzero_without_commands(self):
        with mock.patch.object(smoke, "smoke", side_effect=RuntimeError("unavailable")):
            with mock.patch.object(smoke, "sys") as sys:
                # Use parser.exit rather than actual process exit.
                with self.assertRaises(SystemExit) as error:
                    smoke.main([])
        self.assertEqual(error.exception.code, 1)


if __name__ == "__main__":
    unittest.main()
