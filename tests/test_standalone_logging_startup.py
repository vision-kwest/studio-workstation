"""Verify standalone uses RES's cached default logger before bootstrap import."""
import importlib.util
import os
from pathlib import Path
import sys
import types
import unittest
from unittest import mock

SOURCE=Path(__file__).resolve().parents[1]/"runtime/overrides/ideavirtualdesktop/app/standalone_bootstrap.py"

class LoggingBootstrapTest(unittest.TestCase):
    def test_default_logger_initialized_before_bootstrap_import(self):
        events=[]
        logging_utils=types.ModuleType("res.utils.logging_utils")
        def res_logging():
            self.assertNotIn("IDEA_MODULE_ID",os.environ)
            events.append("res-default-logging")
        logging_utils.res_logging=res_logging
        res=types.ModuleType("res")
        res.__path__=[]
        utils=types.ModuleType("res.utils")
        utils.__path__=[]
        utils.logging_utils=logging_utils
        res.utils=utils
        desktop=types.ModuleType("ideavirtualdesktop")
        desktop.__path__=[]
        app=types.ModuleType("ideavirtualdesktop.app")
        app.__path__=[]
        linux=types.ModuleType("ideavirtualdesktop.app.linux")
        linux.__path__=[]
        bootstrap=types.ModuleType("ideavirtualdesktop.app.linux.bootstrap")
        def run():
            self.assertEqual(os.environ["IDEA_MODULE_ID"],"vdi-app")
            events.append("bootstrap")
        bootstrap.run=run
        linux.bootstrap=bootstrap
        modules={"res":res,"res.utils":utils,"res.utils.logging_utils":logging_utils,
                 "ideavirtualdesktop":desktop,"ideavirtualdesktop.app":app,
                 "ideavirtualdesktop.app.linux":linux,
                 "ideavirtualdesktop.app.linux.bootstrap":bootstrap}
        spec=importlib.util.spec_from_file_location("test_standalone_entrypoint",SOURCE)
        mod=importlib.util.module_from_spec(spec)
        env={"STUDIO_STANDALONE":"true","IDEA_SESSION_OWNER":"workstation",
             "IDEA_SESSION_ID":"standalone-console","RES_BASE_OS":"ubuntu2404",
             "STUDIO_GPU_FAMILIES":"g4dn,g5,g6","STUDIO_DCV_IDLE_TIMEOUT":"0",
             "STUDIO_DCV_IDLE_WARNING":"0","IDEA_MODULE_ID":"vdi-app"}
        with mock.patch.dict(sys.modules,modules),mock.patch.dict(os.environ,env,clear=True),mock.patch("pwd.getpwnam",return_value=types.SimpleNamespace(pw_uid=1000)):
            spec.loader.exec_module(mod)
            mod.main()
            self.assertEqual(os.environ["IDEA_MODULE_ID"],"vdi-app")
        self.assertEqual(events,["res-default-logging","bootstrap"])

if __name__=="__main__":
    unittest.main()
