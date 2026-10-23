#!/usr/bin/env python3
"""T4: negative-path characterization of unmodified RES 2026.06 modules.

These tests deliberately assert observed upstream behavior, including swallowed
errors. They do NOT assert that the behavior is safe for standalone use.
"""
import importlib
import io
import json
import os
from pathlib import Path
import sys
from unittest import TestCase, TestLoader, TextTestRunner, mock

class NegativeCases(TestCase):
    def test_missing_session_id_dcv_host_rejected_but_configure_logs(self):
        mod = importlib.import_module("ideabootstrap.dcv.linux.dcv_host")
        with mock.patch.dict(os.environ, {}, clear=True), \
             mock.patch.object(mod, "_configure_storage_root"), \
             mock.patch.object(mod, "logger") as log:
            mod.configure()
            self.assertTrue(log.error.called, "Missing session ID must be logged")
            # Original configure() returns despite failure in _configure_dcv_conf().
            self.assertTrue(log.info.called)

    def test_missing_linux_account_storage_root_error_logged(self):
        mod = importlib.import_module("ideabootstrap.dcv.linux.dcv_host")
        with mock.patch.dict(os.environ, {"IDEA_SESSION_OWNER":"missing-local-user"}, clear=True), \
             mock.patch.object(mod.os.path, "islink", return_value=False), \
             mock.patch.object(mod.os.path, "exists", return_value=False), \
             mock.patch.object(mod.os, "makedirs"), \
             mock.patch.object(mod.pwd, "getpwnam", side_effect=KeyError("missing-local-user")), \
             mock.patch.object(mod, "logger") as log:
            mod._configure_storage_root()
            log.error.assert_called()

    def test_unavailable_cluster_settings_gpu_raises(self):
        mod = importlib.import_module("ideabootstrap.dcv.linux.gl")
        with mock.patch.dict(os.environ, {"RES_BASE_OS":"ubuntu2404", "INSTANCE_FAMILY":"g4dn"}, clear=False), \
             mock.patch.object(mod.cluster_settings, "get_setting", side_effect=RuntimeError("no DynamoDB")):
            with self.assertRaisesRegex(RuntimeError, "no DynamoDB"):
                mod.configure()

    def test_missing_dcv_server_returns_false(self):
        mod = importlib.import_module("ideabootstrap.dcv.dcv")
        with mock.patch.object(mod.dcv.start_dcv_service, "is_dcvserver_ready", side_effect=OSError("no dcv")), \
             mock.patch.object(mod, "logger") as log:
            self.assertFalse(mod.is_dcvserver_ready(timeout_seconds=1, retry_interval=0))
            log.error.assert_called()

    def test_console_configuration_failure_reports_error(self):
        mod = importlib.import_module("ideabootstrap.dcv.dcv")
        with mock.patch.object(mod, "BASE_OS", "ubuntu2404"), \
             mock.patch.object(mod, "update_session_state") as state, \
             mock.patch.object(mod.cluster_settings, "get_setting", side_effect=RuntimeError("no settings")), \
             mock.patch.dict(os.environ, {"IDEA_SESSION_OWNER":"workstation", "IDEA_SESSION_ID":"test"}, clear=False):
            self.assertFalse(mod.configure_automatic_console_session())
            self.assertEqual([x.args[0] for x in state.call_args_list], ["CREATING", "ERROR"])

    def test_dcv_configuration_failure_does_not_propagate(self):
        mod = importlib.import_module("ideabootstrap.dcv.dcv")
        with mock.patch.object(mod, "BASE_OS", "ubuntu2404"), \
             mock.patch.object(mod, "dcv") as platform, \
             mock.patch.object(mod, "logger") as log:
            platform.disable_wayland_protocol.configure.side_effect = RuntimeError("gdm unavailable")
            self.assertIsNone(mod.configure())
            log.error.assert_called()
            platform.dcv_host.configure.assert_not_called()

    def test_xorg_virtual_session_skips_systemd(self):
        mod = importlib.import_module("ideabootstrap.dcv.linux.x_server")
        with mock.patch.dict(os.environ, {"SESSION_TYPE":"VIRTUAL"}, clear=False), \
             mock.patch.object(mod.subprocess, "run") as run:
            mod.configure()
            run.assert_not_called()

    def test_xorg_systemd_failure_is_logged(self):
        mod = importlib.import_module("ideabootstrap.dcv.linux.x_server")
        with mock.patch.dict(os.environ, {"SESSION_TYPE":"CONSOLE"}, clear=False), \
             mock.patch.object(mod.subprocess, "run", side_effect=OSError("systemctl unavailable")), \
             mock.patch.object(mod, "logger") as log:
            mod.configure()
            log.error.assert_called()

    def test_existing_ready_lock_failed_console_does_not_poll(self):
        mod = importlib.import_module("ideavirtualdesktop.app.linux.post_reboot")
        with mock.patch.object(mod.os.path, "isfile", return_value=True), \
             mock.patch.object(mod, "update_session_host_info"), \
             mock.patch.object(mod.dcv, "configure_automatic_console_session", return_value=False), \
             mock.patch.object(mod.dcv, "poll_dcv_session_ready") as poll, \
             mock.patch.object(mod, "update_session_state") as state:
            mod.run()
            poll.assert_not_called()
            state.assert_not_called()

if __name__ == "__main__":
    suite = TestLoader().loadTestsFromTestCase(NegativeCases)
    output = io.StringIO()
    result = TextTestRunner(stream=output, verbosity=2).run(suite)
    report = {
        "test":"Gate 2 T4 negative cases on original RES code",
        "status":"PASS" if result.wasSuccessful() else "FAIL",
        "tests_run":result.testsRun,
        "failures":[{"test":str(t),"detail":detail[-1200:]} for t,detail in result.failures],
        "errors":[{"test":str(t),"detail":detail[-1200:]} for t,detail in result.errors],
        "details":output.getvalue(),
        "interpretation":"PASS means expected upstream failure behaviors were reproduced, NOT that standalone compatibility is approved.",
        "limitations":["All host/AWS effects mocked", "No DCV browser login or GPU tests", "Original modules unmodified"]
    }
    Path(os.environ.get("T3_REPORT","/tmp/gate2-t4-report.json")).write_text(json.dumps(report,indent=2)+"\n")
    print(json.dumps(report,indent=2))
    sys.exit(0 if result.wasSuccessful() else 1)
