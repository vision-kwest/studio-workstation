"""Read-only fixture tests for Rez/OpenUSD EFS preflight; no AWS or EFS."""
import json
from pathlib import Path
import tempfile
import unittest

from scripts import rez_openusd_preflight as preflight


class RezOpenUsdPreflightTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "packages"
        self.pkg = self.root / "dev" / "usd" / "26.05"
        (self.pkg / "lib" / "python").mkdir(parents=True)
        (self.pkg / "package.py").write_text(
            'name="usd"\nversion="26.05"\n'
            'requires=["platform-linux","arch-x86_64"]\n'
            'def commands():\n    env.PATH.prepend("{root}/bin")\n',
            encoding="utf-8")
        (self.pkg / "manifest.json").write_text(json.dumps({
            "package_name": "usd", "package_version": "26.05",
            "python_version": "3.12.3", "build_image": "offline-fixture"
        }), encoding="utf-8")
        self.mountinfo = (
            f"31 25 0:44 / {self.root} ro,nosuid,nodev - "
            "nfs4 fs-abc123.efs.us-east-1.amazonaws.com:/ rw,vers=4.1\n"
        )

    def run_preflight(self, **kwargs):
        return preflight.inspect(self.root, "dev",
                                 mountinfo=self.mountinfo, **kwargs)

    def test_valid_dev_package_detects_abi_and_efs_identity_as_unverified(self):
        result = self.run_preflight()
        self.assertEqual(result["stage"], "dev")
        self.assertTrue(result["read_only"])
        self.assertEqual(result["rez_requires"], ["platform-linux", "arch-x86_64"])
        self.assertIn("UNVERIFIED", result["python_abi"])
        self.assertIn("UNVERIFIED", result["efs_identity"])

    def test_missing_mount_is_not_mistaken_for_efs(self):
        self.mountinfo = ""
        with self.assertRaisesRegex(ValueError, "mountpoint"):
            self.run_preflight()

    def test_writable_mount_is_rejected(self):
        self.mountinfo = self.mountinfo.replace(" ro,nosuid", " rw,nosuid")
        with self.assertRaisesRegex(ValueError, "read-only"):
            self.run_preflight()

    def test_non_nfs_mount_is_rejected(self):
        self.mountinfo = self.mountinfo.replace("nfs4 fs-", "ext4 fs-")
        with self.assertRaisesRegex(ValueError, "NFS4"):
            self.run_preflight()

    def test_prod_is_not_assumed_promoted(self):
        with self.assertRaises(FileNotFoundError):
            preflight.inspect(self.root, "prod", mountinfo=self.mountinfo)

    def test_unknown_stage_version_or_relative_root_is_rejected(self):
        for stage in ("archive", "", "../dev"):
            with self.subTest(stage=stage), self.assertRaises(ValueError):
                preflight.inspect(self.root, stage, mountinfo=self.mountinfo)
        with self.assertRaisesRegex(ValueError, "Unsupported"):
            preflight.inspect(self.root, "dev", "99.99", mountinfo=self.mountinfo)
        with self.assertRaisesRegex(ValueError, "relative"):
            preflight.inspect(Path("packages"), "dev", mountinfo=self.mountinfo)

    def test_manifest_identity_mismatch_is_rejected(self):
        (self.pkg / "manifest.json").write_text(
            '{"package_name":"other","package_version":"26.05"}', encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "manifest"):
            self.run_preflight()

    def test_bad_package_metadata_and_requirements_fail_closed(self):
        p = self.pkg / "package.py"
        original = p.read_text(encoding="utf-8")
        for content in (original.replace('name="usd"', 'name="wrong"'),
                        original.replace('"platform-linux"', '"platform-windows"')):
            with self.subTest(content=content):
                p.write_text(content, encoding="utf-8")
                with self.assertRaises(ValueError):
                    self.run_preflight()

    def test_missing_python_tree_is_rejected(self):
        (self.pkg / "lib" / "python").rmdir()
        with self.assertRaisesRegex(ValueError, "Python module"):
            self.run_preflight()

    def test_package_metadata_is_parsed_not_executed(self):
        sentinel = Path(self.temp.name) / "should-not-exist"
        with (self.pkg / "package.py").open("a", encoding="utf-8") as stream:
            stream.write(f"__import__('pathlib').Path({str(sentinel)!r}).touch()\n")
        self.run_preflight()
        self.assertFalse(sentinel.exists())


if __name__ == "__main__":
    unittest.main()
