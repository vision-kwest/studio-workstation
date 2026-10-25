"""Regression checks for minimal standalone SSM and DCV failure boundaries."""
from pathlib import Path
import unittest

ROOT=Path(__file__).resolve().parents[1]
def source(name):
    return (ROOT/"runtime/overrides"/name).read_text()

class CompatibilityChecks(unittest.TestCase):
    def test_ssm_disable_is_res_only(self):
        s=source("scripts/virtual-desktop-host/linux/install.sh")
        self.assertIn('[[ $PREBAKING_AMI == "false" && "${STUDIO_STANDALONE}" != "true" ]]',s)
        self.assertIn("disable_amazon_ssm_agent.sh",s)
        self.assertIn("install_post_reboot.sh",s)
    def test_supervisor_reports_unexpected_exits(self):
        s=source("scripts/common/linux/install_app.sh")
        self.assertIn("autorestart=unexpected",s)
        self.assertIn("startsecs=10",s)
        self.assertIn("startretries=3",s)
    def test_bootstrap_requires_dcv_ready(self):
        s=source("ideavirtualdesktop/app/linux/bootstrap.py")
        self.assertIn("not dcv.is_dcvserver_ready()",s)
        self.assertIn('raise RuntimeError("Standalone DCV server failed initial readiness check")',s)
    def test_console_requires_ready(self):
        s=source("ideavirtualdesktop/app/linux/post_reboot.py")
        self.assertEqual(s.count('raise RuntimeError("Standalone DCV console failed readiness check")'),2)

if __name__=="__main__":unittest.main()
