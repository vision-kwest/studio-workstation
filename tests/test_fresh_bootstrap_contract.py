"""Guard fresh-boot download failure handling and packaged logging source."""
from pathlib import Path
import unittest

ROOT=Path(__file__).resolve().parents[1]
class FreshBootstrapContract(unittest.TestCase):
    def test_archive_failure_does_not_write_preinstall_lock(self):
        text=(ROOT/"runtime/upstream/bootstrap_userdata_linux_base_non_substitution.sh.jinja2").read_text()
        self.assertIn('echo "#!/bin/bash\n  set -e\n  PACKAGE_DOWNLOAD_URI=',text)
        download='bash /root/bootstrap/download_bootstrap.sh "{{ bootstrap_package_uri }}" || exit 1'
        self.assertIn(download,text)
        self.assertLess(text.index(download),text.index('echo $(date +%s) > /root/bootstrap/semaphore/pre_install_finished.lock'))
    def test_logging_fix_is_in_release_overlay(self):
        text=(ROOT/"runtime/overrides/ideavirtualdesktop/app/standalone_bootstrap.py").read_text()
        self.assertIn('logging_utils.res_logging()',text)
        self.assertLess(text.index('logging_utils.res_logging()'),text.index('from ideavirtualdesktop.app.linux import bootstrap'))
if __name__=="__main__":unittest.main()
