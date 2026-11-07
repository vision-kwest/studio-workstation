import json
import subprocess
import unittest
from pathlib import Path
from runtime import render_workstation

ROOT = Path(__file__).resolve().parents[1]
BUCKET = "studio-workstation-releases-401233741942-us-east-1"

class StudioSoftwareContract(unittest.TestCase):
    def test_original_cpu_gpu_rendering_unchanged_without_software_pin(self):
        for profile in ("cpu", "gpu"):
            user_data = render_workstation.render(BUCKET, "releases/v0.1.3/res-2026.06", "workstation", profile)
            self.assertNotIn("studio-software-setup", user_data)
            self.assertIn("STUDIO_STANDALONE=true", user_data)

    def test_opt_in_is_same_for_cpu_and_gpu_and_does_not_block_dcv(self):
        for profile in ("cpu", "gpu"):
            text = render_workstation.render(BUCKET, "releases/v0.1.3/res-2026.06", "workstation", profile,
                                              "Version_Id.1", "a" * 64)
            self.assertIn("systemctl enable studio-software-setup.service", text)
            self.assertIn("systemctl start --no-block studio-software-setup.service", text)
            self.assertIn("RemainAfterExit=yes", text)
            self.assertIn("Restart=on-failure", text)
            self.assertIn("app_installed.lock", text)
            self.assertIn("linux_vdi_config_host_ready.lock", text)
            self.assertIn("x-systemd.automount,ro,tls,iam", text)
            self.assertIn("software/rez/3.4.0/studio-rez-ubuntu2404.tar.gz", text)
            self.assertLess(text.index("scripts/virtual-desktop-host/linux/install.sh -p false"),
                            text.index("systemctl start --no-block studio-software-setup.service"))
            self.assertNotIn("mount -a", text)

    def test_release_pin_validation(self):
        for version, digest in [(None, "a"*64), ("bad;id", "a"*64), ("v1", "bad"), ("v1", None)]:
            with self.assertRaises(ValueError):
                render_workstation.render(BUCKET, "releases/v0.1.3/res-2026.06",
                                          "workstation", "cpu", version, digest)

    def test_bash_syntax_and_no_mount_engine(self):
        script = ROOT / "scripts/setup_workstation_software.sh"
        subprocess.run(["bash", "-n", str(script)], check=True)
        text = script.read_text()
        self.assertNotIn("mount -a", text)
        self.assertNotIn("mount -t ", text)
        self.assertIn("systemctl start opt-studio-packages.automount", text)
        self.assertIn("PIP_NO_INDEX=1", text)

    def test_review_only_security_and_exact_s3(self):
        policy = json.loads((ROOT / "infra/efs-publisher-consumer.review-only.json").read_text())
        by_sid = {s["Sid"]: s for s in policy["FileSystemPolicy"]["Statement"]}
        self.assertEqual(by_sid["ConsumerMount"]["Action"], "elasticfilesystem:ClientMount")
        self.assertIn("elasticfilesystem:ClientWrite", by_sid["ConsumerNoWrite"]["Action"])
        self.assertEqual(by_sid["RequireTLS"]["Condition"]["Bool"]["aws:SecureTransport"], "false")
        s3 = json.loads((ROOT / "infra/workstation-release-read-policy.json").read_text())
        exact = [s for s in s3["Statement"] if s["Sid"] == "ReadExactRezBundleVersion"]
        self.assertEqual(len(exact), 1)
        self.assertEqual(exact[0]["Action"], "s3:GetObjectVersion")

class SoftwareRetryCases(unittest.TestCase):
    def setUp(self):
        import tempfile
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = (ROOT / "scripts/setup_workstation_software.sh").read_text()

    def execute(self, code, **env):
        import os
        return subprocess.run(["bash", "-c", "set -euo pipefail\n" + code],
                              env={**os.environ, **env}, capture_output=True, text=True)

    def test_fstab_rejects_duplicate_and_conflict_even_with_expected(self):
        import shlex
        mount = self.root / "packages"
        fstab = self.root / "fstab"
        code = self.source.split("mountpoint=/opt/studio/packages\n", 1)[1].split("systemctl daemon-reload", 1)[0]
        code = "mountpoint=" + shlex.quote(str(mount)) + "\n" + code
        code = code.replace("fstab=/etc/fstab", "fstab=" + shlex.quote(str(fstab)))
        expected = f"fs-054fd0b72429f4569:/ {mount} efs _netdev,nofail,x-systemd.automount,ro,tls,iam 0 0"
        conflict = f"fs-other:/ {mount} nfs4 defaults 0 0"
        for entries, ok in [([], True), ([expected], True), ([expected, expected], False),
                            ([expected, conflict], False), ([conflict, expected], False),
                            ([conflict], False)]:
            with self.subTest(entries=entries):
                fstab.write_text("\n".join(entries) + ("\n" if entries else ""))
                r = self.execute(code)
                self.assertEqual(r.returncode == 0, ok, r.stderr)
                self.assertEqual(fstab.read_text().splitlines(),
                                 entries or [expected])

    def test_interrupted_rez_is_retried_only_when_owned(self):
        directory = self.root / "bundle"
        directory.mkdir()
        path = self.root / "rez" / "3.4.0"
        stamp = self.root / "rez" / ".studio-rez-3.4.0-installing"
        block = self.source.split("rez=/opt/studio/rez/3.4.0\n", 1)[1].split(
            'test -f "$dir/studio-rez/env/studio-prod.sh"', 1)[0]
        block = "rez=/opt/studio/rez/3.4.0\n" + block
        block = block.replace("/opt/studio/rez", str(self.root / "rez"))
        block = "\n".join("  :" if "sha256sum -c" in line or "tar -xzf" in line
                          else '  bash "$dir/fake-install" "$rez"' if "PIP_NO_INDEX=1" in line
                          else line for line in block.splitlines()) + "\n"
        installer = directory / "fake-install"
        installer.write_text('''mkdir -p "$1/bin/rez"
if test -e "$FAIL"; then exit 42; fi
printf '3.4.0' > "$1/bin/rez/.rez_production_install"
printf '#!/bin/sh\\nexit 0\\n' > "$1/bin/rez/rez-env"
chmod +x "$1/bin/rez/rez-env"
''')
        failure = self.root / "fail"
        failure.touch()
        self.assertNotEqual(self.execute('dir="' + str(directory) + '"\n' + block, FAIL=str(failure)).returncode, 0)
        self.assertTrue(stamp.exists())
        self.assertTrue(path.exists())
        failure.unlink()
        result = self.execute('dir="' + str(directory) + '"\n' + block, FAIL=str(failure))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(stamp.exists())
        self.assertTrue((path / "bin/rez/rez-env").is_file())
        (path / "preserve").write_text("valid")
        failure.touch()
        self.assertEqual(self.execute('dir="' + str(directory) + '"\n' + block, FAIL=str(failure)).returncode, 0)
        self.assertEqual((path / "preserve").read_text(), "valid")
        import shutil
        shutil.rmtree(path)
        (path / "foreign").parent.mkdir()
        (path / "foreign").write_text("do not remove")
        self.assertNotEqual(self.execute('dir="' + str(directory) + '"\n' + block, FAIL=str(failure)).returncode, 0)
        self.assertEqual((path / "foreign").read_text(), "do not remove")

    def test_interrupted_studio_rez_staging_retries_without_touching_foreign_tree(self):
        bundle = self.root / "bundle"
        source = bundle / "studio-rez"
        for item in ("config/rezconfig.prod.py", "env/studio-prod.sh"):
            target = source / item
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text("prod-config")
        destination = self.root / "studio-rez"
        stale = self.root / ".studio-rez.stale"
        stale.mkdir()
        (stale / "partial").write_text("preserve unrelated")
        block = self.source.split("if test ! -e /opt/studio/studio-rez; then\n", 1)[1].split(
            "if test -e /usr/local/bin/studio-rez-env;", 1)[0]
        block = "if test ! -e /opt/studio/studio-rez; then\n" + block
        block = block.replace("/opt/studio/studio-rez", str(destination))
        block = block.replace("/opt/studio/.studio-rez.XXXXXXXX", str(self.root / ".studio-rez.XXXXXXXX"))
        result = self.execute('dir="' + str(bundle) + '"\nstage=""\n' + block)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((destination / "config/rezconfig.prod.py").read_text(), "prod-config")
        self.assertTrue((stale / "partial").exists())
        (destination / "valid").write_text("keep")
        self.assertEqual(self.execute('dir="' + str(bundle) + '"\nstage=""\n' + block).returncode, 0)
        self.assertEqual((destination / "valid").read_text(), "keep")

if __name__ == "__main__":
    unittest.main()
