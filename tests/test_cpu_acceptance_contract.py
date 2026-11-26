import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]

class DeploymentAcceptanceContracts(unittest.TestCase):
    def test_versioned_transfer_retries_without_bypassing_verification(self):
        from runtime import render_workstation as renderer
        data = renderer.render(renderer.PINNED_GPU_BUCKET, renderer.PINNED_GPU_PREFIX,
                               'workstation', 'gpu').replace('${!', '${')
        start, end = data.index('  BOOTSTRAP_DOWNLOAD_URI='), data.index('  if ! bash /root/bootstrap/download_bootstrap.sh')
        script = data[start:end]
        stubs = r'''
aws() { count=$((count+1)); echo attempt >> "$TEST_ROOT/calls"; test "$count" -gt "$FAILURES"; }
sleep() { echo wait >> "$TEST_ROOT/calls"; }
sha256sum() { echo verify >> "$TEST_ROOT/calls"; test "$BAD_HASH" = 0; }
count=0
'''
        for failures, bad_hash, attempts, verified, success in (
                (0, 0, 1, True, True), (2, 0, 3, True, True),
                (13, 0, 13, False, False), (0, 1, 1, True, False)):
            with self.subTest(failures=failures, bad_hash=bad_hash), tempfile.TemporaryDirectory() as directory:
                command = stubs + script.replace('/root/bootstrap', directory) + '\necho continued\n'
                result = subprocess.run(['bash', '-c', command], capture_output=True, text=True,
                                        env={**os.environ, 'TEST_ROOT': directory,
                                             'AWS_REGION': 'us-east-1', 'FAILURES': str(failures),
                                             'BAD_HASH': str(bad_hash)})
                calls = (Path(directory) / 'calls').read_text().splitlines()
                self.assertEqual(calls.count('attempt'), attempts)
                self.assertEqual(calls.count('wait'), attempts - 1)
                self.assertEqual('verify' in calls, verified)
                self.assertEqual(result.returncode == 0, success)
                self.assertEqual('continued' in result.stdout, success)

    def test_release_logging_hotfix_in_source(self):
        source=(ROOT/"runtime/overrides/ideavirtualdesktop/app/standalone_bootstrap.py").read_text()
        self.assertIn("logging_utils.res_logging()",source)
        self.assertLess(source.index("logging_utils.res_logging()"),source.index("from ideavirtualdesktop.app.linux import bootstrap"))
    def test_bootstrap_does_not_mark_failed_download_complete(self):
        source=(ROOT/"runtime/upstream/bootstrap_userdata_linux_base_non_substitution.sh.jinja2").read_text()
        self.assertIn('echo "#!/bin/bash\n  set -e\n  PACKAGE_DOWNLOAD_URI=',source)
        self.assertIn('if ! bash /root/bootstrap/download_bootstrap.sh',source)
        self.assertIn('if [[ ! -s /root/bootstrap/latest/scripts/virtual-desktop-host/linux/install.sh ]]',source)
        self.assertLess(source.index('if ! bash /root/bootstrap/download_bootstrap.sh'),source.index('pre_install_finished.lock\nfi'))
    def test_public_dcv_ingress_is_default_and_restrictable(self):
        template=json.loads((ROOT/"runtime/workstation-template.json").read_text())
        cidr=template["Parameters"]["ClientCidr"]
        self.assertEqual(cidr["Default"],"0.0.0.0/0")
        import re
        for valid in ("0.0.0.0/0","76.166.163.7/32","192.168.1.0/24"):
            self.assertIsNotNone(re.fullmatch(cidr["AllowedPattern"],valid))
        for invalid in ("999.999.999.999/32","1.2.3.4/33","not-a-cidr"):
            self.assertIsNone(re.fullmatch(cidr["AllowedPattern"],invalid))
        ingress=template["Resources"]["WorkstationSG"]["Properties"]["SecurityGroupIngress"]
        self.assertEqual(ingress,[
            {"IpProtocol":"tcp","FromPort":8443,"ToPort":8443,"CidrIp":{"Ref":"ClientCidr"}},
            {"IpProtocol":"tcp","FromPort":22,"ToPort":22,"CidrIp":"0.0.0.0/0"},
        ])
    def test_instance_remains_single_machine(self):
        template=json.loads((ROOT/"runtime/workstation-template.json").read_text())
        self.assertEqual(template["Resources"]["Workstation"]["Type"],"AWS::EC2::Instance")
        self.assertEqual(template["Resources"]["Workstation"]["Properties"]["IamInstanceProfile"],{"Ref":"InstanceProfileName"})

if __name__=="__main__":unittest.main()
