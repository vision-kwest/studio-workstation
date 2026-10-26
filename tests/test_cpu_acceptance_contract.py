import json
from pathlib import Path
import unittest

ROOT=Path(__file__).resolve().parents[1]

class DeploymentAcceptanceContracts(unittest.TestCase):
    def test_release_logging_hotfix_in_source(self):
        source=(ROOT/"runtime/overrides/ideavirtualdesktop/app/standalone_bootstrap.py").read_text()
        self.assertIn("logging_utils.res_logging()",source)
        self.assertLess(source.index("logging_utils.res_logging()"),source.index("from ideavirtualdesktop.app.linux import bootstrap"))
    def test_bootstrap_does_not_mark_failed_download_complete(self):
        source=(ROOT/"runtime/upstream/bootstrap_userdata_linux_base_non_substitution.sh.jinja2").read_text()
        self.assertIn('if ! bash /root/bootstrap/download_bootstrap.sh',source)
        self.assertIn('if [[ ! -s /root/bootstrap/latest/scripts/virtual-desktop-host/linux/install.sh ]]',source)
        self.assertLess(source.index('if ! bash /root/bootstrap/download_bootstrap.sh'),source.index('pre_install_finished.lock\nfi'))
    def test_instance_remains_single_machine(self):
        template=json.loads((ROOT/"runtime/workstation-template.json").read_text())
        self.assertEqual(template["Resources"]["Workstation"]["Type"],"AWS::EC2::Instance")
        self.assertEqual(template["Resources"]["Workstation"]["Properties"]["IamInstanceProfile"],{"Ref":"InstanceProfileName"})

if __name__=="__main__":unittest.main()
