#!/usr/bin/env python3
"""Offline behavioral tests for original RES-derived standalone bootstrap.

Imports modules from the derived application archive in an isolated CI
container, with all host effects patched before invoking functions.
"""
import importlib
import io
import json
import os
from pathlib import Path
import sys
from unittest import TestCase, TestLoader, TextTestRunner, mock

class StandaloneBehavior(TestCase):
    def test_first_boot_preserves_gnome_then_dcv_and_no_res_services(self):
        m=importlib.import_module("ideavirtualdesktop.app.linux.bootstrap")
        events=[]
        with mock.patch.dict(os.environ, {"STUDIO_STANDALONE":"true","IDEA_SESSION_ID":"test","IDEA_CLUSTER_HOME":"/no-res"},clear=False), \
             mock.patch.object(m.os.path,"isfile",return_value=False), \
             mock.patch.object(m.os,"makedirs"), \
             mock.patch("builtins.open",mock.mock_open()), \
             mock.patch.object(m.bootstrap_common,"set_reboot_required"), \
             mock.patch.object(m.bootstrap_common,"check_reboot_required",return_value=False), \
             mock.patch.object(m.bootstrap_common,"source_launch_env_file"), \
             mock.patch.object(m.post_reboot,"run",side_effect=lambda:events.append("post")), \
             mock.patch.object(m.gnome_utils,"configure",side_effect=lambda:events.append("gnome")), \
             mock.patch.object(m.dcv,"configure",side_effect=lambda:events.append("dcv")), \
             mock.patch.object(m.dcv,"is_dcvserver_ready",return_value=True):
            preserved=[(m.amazon_ssm_agent,"configure"),(m.cloudwatch_agent,"setup"),
                       (m.idea_service_account,"setup_account"),(m.restrict_ssh,"configure"),
                       (m.disable_ssh,"configure"),(m.sudoer_secure_path,"configure"),
                       (m.chronyd,"configure"),(m.bootstrap_common,"disable_ulimit"),
                       (m.bootstrap_common,"disable_strict_host_check"),
                       (m.bootstrap_common,"disable_se_linux"),(m.motd,"disable_update"),
                       (m.motd,"update")]
            forbidden=[(m.idea_proxy,"set_proxy"),(m.shared_storage,"configure"),
                       (m.ebs_volume_tags,"setup"),(m.network_interface_tags,"setup"),
                       (m.active_directory,"configure"),(m.cognito_modules,"configure"),
                       (m.set_sudoers,"configure"),(m.ssh_keygen,"configure"),
                       (m.hibernation,"resume_logger")]
            with __import__("contextlib").ExitStack() as stack:
                for obj,name in preserved:
                    stack.enter_context(mock.patch.object(obj,name))
                blocked=[stack.enter_context(mock.patch.object(obj,name,side_effect=AssertionError("RES-only "+name))) for obj,name in forbidden]
                stack.enter_context(mock.patch.object(m,"on_vdi_configured",side_effect=AssertionError("RES customization")))
                m.run()
            self.assertEqual(events,["gnome","dcv","post"])

    def test_initial_dcv_readiness_failure_propagates(self):
        m=importlib.import_module("ideavirtualdesktop.app.linux.bootstrap")
        with mock.patch.dict(os.environ,{"STUDIO_STANDALONE":"true","IDEA_SESSION_ID":"test"},clear=False), \
             mock.patch.object(m.os.path,"isfile",return_value=False), \
             mock.patch.object(m.bootstrap_common,"set_reboot_required"), \
             mock.patch.object(m.gnome_utils,"configure"), \
             mock.patch.object(m.dcv,"configure"), \
             mock.patch.object(m.dcv,"is_dcvserver_ready",return_value=False):
            from contextlib import ExitStack
            with ExitStack() as stack:
                for obj,name in ((m.amazon_ssm_agent,"configure"),(m.cloudwatch_agent,"setup"),
                                 (m.idea_service_account,"setup_account"),(m.restrict_ssh,"configure"),
                                 (m.disable_ssh,"configure"),(m.sudoer_secure_path,"configure"),
                                 (m.chronyd,"configure"),(m.bootstrap_common,"disable_ulimit"),
                                 (m.bootstrap_common,"disable_strict_host_check"),
                                 (m.bootstrap_common,"disable_se_linux"),(m.motd,"disable_update"),
                                 (m.motd,"update")):
                    stack.enter_context(mock.patch.object(obj,name))
                with self.assertRaisesRegex(RuntimeError,"DCV server failed initial readiness"):
                    m.run()

    def test_existing_config_lock_calls_post_reboot(self):
        m=importlib.import_module("ideavirtualdesktop.app.linux.bootstrap")
        with mock.patch.dict(os.environ,{"STUDIO_STANDALONE":"true","RERUN_ON_REBOOT":"True"},clear=False), \
             mock.patch.object(m.os.path,"isfile",return_value=True), \
             mock.patch.object(m.bootstrap_common,"source_launch_env_file"), \
             mock.patch.object(m.post_reboot,"run") as post, \
             mock.patch.object(m,"on_vdi_configured",side_effect=AssertionError("RES customization")):
            m.run()
            post.assert_called_once()

    def test_post_reboot_skips_res_state_and_cron(self):
        m=importlib.import_module("ideavirtualdesktop.app.linux.post_reboot")
        with mock.patch.dict(os.environ,{"STUDIO_STANDALONE":"true"},clear=False), \
             mock.patch.object(m.os.path,"isfile",return_value=True), \
             mock.patch.object(m,"update_session_host_info",side_effect=AssertionError("RES host")), \
             mock.patch.object(m,"update_session_state",side_effect=AssertionError("RES state")), \
             mock.patch.object(m.subprocess,"run",side_effect=AssertionError("cron")), \
             mock.patch.object(m.dcv,"configure_automatic_console_session",return_value=True) as configure, \
             mock.patch.object(m.dcv,"poll_dcv_session_ready",return_value="READY") as poll:
            m.run()
            configure.assert_called_once()
            poll.assert_called_once()

    def test_original_res_branch_remains_available(self):
        m=importlib.import_module("ideavirtualdesktop.app.linux.post_reboot")
        with mock.patch.dict(os.environ,{"STUDIO_STANDALONE":"false"},clear=False), \
             mock.patch.object(m.os.path,"isfile",return_value=True), \
             mock.patch.object(m,"update_session_host_info") as host, \
             mock.patch.object(m,"update_session_state") as state, \
             mock.patch.object(m.dcv,"configure_automatic_console_session",return_value=True), \
             mock.patch.object(m.dcv,"poll_dcv_session_ready",return_value="READY"):
            m.run()
            host.assert_called_once()
            state.assert_called_once_with("READY")

if __name__=="__main__":
    stream=io.StringIO()
    result=TextTestRunner(stream=stream,verbosity=2).run(TestLoader().loadTestsFromTestCase(StandaloneBehavior))
    report={"tests":result.testsRun,"success":result.wasSuccessful(),"output":stream.getvalue(),
            "limitations":["Host operations mocked; no real reboot or cloud-init","No PAM, DCV login or GPU runtime test"]}
    Path(os.environ.get("STUDIO_TEST_REPORT","/tmp/studio-behavior.json")).write_text(json.dumps(report,indent=2))
    print(json.dumps(report,indent=2))
    sys.exit(0 if result.wasSuccessful() else 1)
