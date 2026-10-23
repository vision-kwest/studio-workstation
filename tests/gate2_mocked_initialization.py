#!/usr/bin/env python3
"""T3: mocked RES 2026.06 call-order and dependency tests. Never deploys."""
import importlib
import json
import os
from pathlib import Path
from unittest import TestCase, TestLoader, TextTestRunner, mock
import io
import sys
import unittest

class RESMockedTests(TestCase):
    def test_dcv_configuration_order(self):
        mod = importlib.import_module("ideabootstrap.dcv.dcv")
        calls = []
        names = ["disable_wayland_protocol", "dcv_host", "gl", "x_server", "start_dcv_service"]
        with mock.patch.object(mod, "BASE_OS", "ubuntu2404"), mock.patch.object(mod, "dcv") as platform:
            for name in names:
                getattr(platform, name).configure.side_effect = lambda n=name: calls.append(n)
            mod.configure()
        self.assertEqual(calls, names)

    def test_dcv_configuration_error_is_logged_not_raised(self):
        mod = importlib.import_module("ideabootstrap.dcv.dcv")
        with mock.patch.object(mod, "BASE_OS", "ubuntu2404"), mock.patch.object(mod, "dcv") as platform, mock.patch.object(mod, "logger") as logger:
            platform.disable_wayland_protocol.configure.side_effect = RuntimeError("sentinel")
            mod.configure()
            logger.error.assert_called_once()
            platform.dcv_host.configure.assert_not_called()

    def test_bootstrap_first_run_local_order_and_semaphore(self):
        mod = importlib.import_module("ideavirtualdesktop.app.linux.bootstrap")
        events = []
        # Mock all external calls before invoking the original function.
        patchers = []
        def replace(obj, attr, val):
            p = mock.patch.object(obj, attr, val)
            patchers.append(p)
            p.start()
        try:
            replace(mod.os.path, "isfile", lambda path: False)
            replace(mod.os, "makedirs", lambda *a, **kw: None)
            replace(mod, "open", mock.mock_open(),) if hasattr(mod, "open") else None
            # builtins.open used by original function
            p = mock.patch("builtins.open", mock.mock_open())
            patchers.append(p); p.start()
            replace(mod.bootstrap_common, "set_reboot_required", lambda value: events.append("reboot_flag"))
            replace(mod.bootstrap_common, "disable_ulimit", lambda: None)
            replace(mod.bootstrap_common, "disable_strict_host_check", lambda: None)
            replace(mod.bootstrap_common, "disable_se_linux", lambda: None)
            replace(mod.bootstrap_common, "source_launch_env_file", lambda path: events.append("launch_env"))
            for obj, method in [
                (mod.amazon_ssm_agent,"configure"),(mod.cloudwatch_agent,"setup"),
                (mod.idea_proxy,"set_proxy"),(mod.idea_service_account,"setup_account"),
                (mod.shared_storage,"configure"),(mod.restrict_ssh,"configure"),
                (mod.disable_ssh,"configure"),(mod.sudoer_secure_path,"configure"),
                (mod.ebs_volume_tags,"setup"),(mod.network_interface_tags,"setup"),
                (mod.chronyd,"configure"),(mod.motd,"disable_update"),
                (mod.motd,"update"),(mod.active_directory,"configure"),
                (mod.cognito_modules,"configure"),(mod.set_sudoers,"configure"),
                (mod.ssh_keygen,"configure"),(mod.hibernation,"resume_logger")]:
                replace(obj, method, lambda *args, **kw: None)
            replace(mod.gnome_utils, "configure", lambda: events.append("gnome"))
            replace(mod.dcv, "configure", lambda: events.append("dcv"))
            replace(mod, "on_vdi_configured", lambda: events.append("customization"))
            replace(mod.bootstrap_common, "check_reboot_required", lambda: False)
            replace(mod.post_reboot, "run", lambda: events.append("post_reboot"))
            with mock.patch.dict(os.environ, {"IDEA_SESSION_ID":"local-session", "IDEA_CLUSTER_HOME":"/nonexistent-res-t3"}, clear=False):
                mod.run()
            self.assertLess(events.index("gnome"), events.index("dcv"))
            self.assertLess(events.index("dcv"), events.index("launch_env"))
            self.assertLess(events.index("launch_env"), events.index("post_reboot"))
        finally:
            for p in reversed(patchers): p.stop()

    def test_bootstrap_subsequent_run(self):
        mod = importlib.import_module("ideavirtualdesktop.app.linux.bootstrap")
        with mock.patch.object(mod.os.path, "isfile", return_value=True), \
             mock.patch.object(mod.bootstrap_common, "source_launch_env_file") as env, \
             mock.patch.object(mod.post_reboot, "run") as post, \
             mock.patch.object(mod, "on_vdi_configured") as configured, \
             mock.patch.dict(os.environ, {"RERUN_ON_REBOOT":"False"}, clear=False):
            mod.run()
            env.assert_called_once()
            post.assert_called_once()
            configured.assert_not_called()

    def test_post_reboot_existing_ready_lock(self):
        mod = importlib.import_module("ideavirtualdesktop.app.linux.post_reboot")
        events = []
        with mock.patch.object(mod.os.path, "isfile", return_value=True), \
             mock.patch.object(mod, "update_session_host_info", side_effect=lambda: events.append("RES_host")), \
             mock.patch.object(mod, "update_session_state", side_effect=lambda s: events.append("RES_state:"+s)), \
             mock.patch.object(mod.dcv, "configure_automatic_console_session", side_effect=lambda: events.append("console") or True), \
             mock.patch.object(mod.dcv, "poll_dcv_session_ready", side_effect=lambda: events.append("poll") or "READY"):
            mod.run()
        self.assertEqual(events, ["RES_host", "console", "poll", "RES_state:READY"])

    def test_gpu_family_queries_res_settings_even_cpu(self):
        mod = importlib.import_module("ideabootstrap.dcv.linux.gl")
        with mock.patch.dict(os.environ, {"RES_BASE_OS":"ubuntu2404", "INSTANCE_FAMILY":"m7i", "MACHINE":"x86_64"}, clear=False), \
             mock.patch.object(mod.cluster_settings, "get_setting", return_value=["g4dn"]) as setting, \
             mock.patch.object(mod, "_configure_gl") as configure:
            mod.configure()
            setting.assert_called_once_with("global-settings.gpu_settings.instance_families")
            configure.assert_not_called()

if __name__ == "__main__":
    suite = TestLoader().loadTestsFromTestCase(RESMockedTests)
    stream = io.StringIO()
    result = TextTestRunner(stream=stream, verbosity=2).run(suite)
    report = {"test":"Gate 2 T3 mocked behavior", "tests_run":result.testsRun,
              "failures":[str(t) for t,_ in result.failures],
              "errors":[str(t) for t,_ in result.errors],
              "success":result.wasSuccessful(),
              "details":stream.getvalue(),
              "limitations":["No real DCV/GNOME/Xorg startup", "No AWS calls", "No provisioning", "T4 negative scenarios still pending"]}
    Path(os.environ.get("T3_REPORT", "/tmp/gate2-t3-report.json")).write_text(json.dumps(report, indent=2))
    print(json.dumps(report, indent=2))
    sys.exit(0 if result.wasSuccessful() else 1)
