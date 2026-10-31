"""Offline unit tests for the RES 2026.06 standalone runtime coordinator.

Run: python3 -m unittest discover -s tests -p 'test_studio_res_runtime.py' -v
No AWS credentials, GPU, or RES Python package installation required.
"""
import importlib.util
import os
from pathlib import Path
import tempfile
import types
import unittest
from unittest.mock import MagicMock, patch

MODULE_PATH = Path(__file__).resolve().parents[1] / "runtime" / "studio_res_runtime.py"
spec = importlib.util.spec_from_file_location("studio_res_runtime", MODULE_PATH)
runtime = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runtime)

ENV = {
    "RES_BASE_OS": "ubuntu2404",
    "MACHINE": "x86_64",
    "INSTANCE_FAMILY": "g4dn",
    "SESSION_TYPE": "CONSOLE",
    "IDEA_SESSION_OWNER": "workstation",
    "IDEA_SESSION_ID": "studio-local-console",
}


def modules():
    m = {key: MagicMock() for key in ("wayland", "gl", "xorg", "host", "service", "dcv")}
    m["xorg"]._x_server_validated.return_value = True
    m["service"].is_dcvserver_ready.return_value = True
    m["dcv"].poll_dcv_session_ready.return_value = "READY"
    return m


class RuntimeTests(unittest.TestCase):
    def test_rejects_virtual_session_and_wrong_family(self):
        with patch.object(runtime.pwd, "getpwnam", return_value=object()):
            for name, value in (("SESSION_TYPE", "VIRTUAL"), ("INSTANCE_FAMILY", "g5")):
                env = dict(ENV, **{name: value})
                with self.assertRaises(runtime.ReadinessError):
                    runtime.validate_environment(env)

    def test_system_auth_only(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "dcv.conf"
            p.write_text('[security]\nauthentication="system"\n')
            runtime.assert_system_auth(p)
            p.write_text('[security]\nauthentication="system"\nauth-token-verifier="https://example"\n')
            with self.assertRaises(runtime.ReadinessError):
                runtime.assert_system_auth(p)
            p.write_text('[security]\nauthentication="none"\n')
            with self.assertRaises(runtime.ReadinessError):
                runtime.assert_system_auth(p)

    def test_permissions_owner_only_and_idempotent(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "console" / "idea.perm"
            runtime.ensure_permissions(p)
            self.assertEqual(p.read_text(), "[permissions]\n%owner% allow builtin\n")
            runtime.ensure_permissions(p)
            p.write_text("[permissions]\n%owner% allow builtin\nuser:anyone allow builtin\n")
            with self.assertRaises(runtime.ReadinessError):
                runtime.ensure_permissions(p)

    def test_xorg_timeout_is_fatal(self):
        m = modules()
        m["xorg"]._x_server_validated.return_value = False
        with self.assertRaises(runtime.ReadinessError):
            runtime.assert_xorg_ready(m["xorg"])

    def test_happy_path_reuses_upstream_order(self):
        m = modules()
        calls = []
        m["wayland"].configure.side_effect = lambda: calls.append("wayland")
        m["gl"]._configure_gl.side_effect = lambda _: calls.append("gl")
        m["xorg"].configure.side_effect = lambda: calls.append("xorg")
        m["host"].apply_automatic_console_session_config.side_effect = lambda *a: calls.append("console")
        m["service"].is_dcvserver_ready.side_effect = lambda *a: calls.append("service") or True
        m["dcv"].poll_dcv_session_ready.side_effect = lambda *a: calls.append("ready") or "READY"
        fakeuser = types.SimpleNamespace(pw_uid=1000, pw_gid=1000)
        with tempfile.TemporaryDirectory() as d, \
             patch.dict(os.environ, ENV), \
             patch.object(runtime.pwd, "getpwnam", return_value=fakeuser), \
             patch.object(runtime, "run_command", return_value=types.SimpleNamespace(stdout="/usr/bin/dcvgladmin\n")), \
             patch.object(runtime, "assert_system_auth"), \
             patch.object(runtime, "ensure_permissions", return_value="/tmp/idea.perm"), \
             patch.object(runtime, "assert_gpu_gl"), \
             patch.object(runtime.Path, "mkdir"), \
             patch.object(runtime.Path, "chown"):
            # The storage path is fixed, but filesystem mutations are mocked.
            runtime.initialize(m)
        self.assertEqual(calls, ["wayland", "gl", "xorg", "console", "service", "ready"])

    def test_failed_console_does_not_report_ready(self):
        m = modules()
        m["dcv"].poll_dcv_session_ready.return_value = "ERROR"
        fakeuser = types.SimpleNamespace(pw_uid=1000, pw_gid=1000)
        with patch.dict(os.environ, ENV), \
             patch.object(runtime.pwd, "getpwnam", return_value=fakeuser), \
             patch.object(runtime, "run_command", return_value=types.SimpleNamespace(stdout="/usr/bin/dcvgladmin\n")), \
             patch.object(runtime, "assert_system_auth"), \
             patch.object(runtime, "ensure_permissions", return_value="/tmp/idea.perm"), \
             patch.object(runtime, "assert_gpu_gl"), \
             patch.object(runtime.Path, "mkdir"), \
             patch.object(runtime.Path, "chown"):
            with self.assertRaises(runtime.ReadinessError):
                runtime.initialize(m)


if __name__ == "__main__":
    unittest.main()
