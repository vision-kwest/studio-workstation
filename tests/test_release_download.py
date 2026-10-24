"""Offline tests for release source acquisition; no AWS calls."""
import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest import mock

SCRIPT=Path(__file__).resolve().parents[1]/"scripts/release.py"
spec=importlib.util.spec_from_file_location("release",SCRIPT)
release=importlib.util.module_from_spec(spec)
spec.loader.exec_module(release)

class SourceAcquisitionTests(unittest.TestCase):
    def test_missing_offline_fails_without_aws(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(release,"run",side_effect=AssertionError("AWS called")):
            with self.assertRaisesRegex(ValueError,"offline mode"):
                release.ensure_originals(Path(tmp),offline=True)

    def test_existing_invalid_archive_is_never_overwritten(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(release,"run",side_effect=AssertionError("AWS called")):
            path=Path(tmp)/"installation-scripts.tar.gz"
            path.write_bytes(b"bad")
            with self.assertRaisesRegex(ValueError,"wrong SHA-256"):
                release.ensure_originals(Path(tmp))
            self.assertEqual(path.read_bytes(),b"bad")

    def test_download_failure_does_not_leave_partial_original(self):
        with tempfile.TemporaryDirectory() as tmp:
            def fail(*args):
                Path(args[4]).write_bytes(b"partial")
                raise RuntimeError("simulated failure")
            with mock.patch.object(release,"run",side_effect=fail):
                with self.assertRaisesRegex(RuntimeError,"simulated failure"):
                    release.ensure_originals(Path(tmp))
            self.assertFalse((Path(tmp)/"installation-scripts.tar.gz").exists())
            self.assertFalse((Path(tmp)/"installation-scripts.tar.gz.download").exists())

    def test_verified_download_promoted_and_cached(self):
        with tempfile.TemporaryDirectory() as tmp:
            originals={name:(name+" contents").encode() for name in release.EXPECTED}
            hashes={name:__import__("hashlib").sha256(data).hexdigest() for name,data in originals.items()}
            with mock.patch.dict(release.EXPECTED,hashes,clear=True):
                def fake(*args):
                    name=Path(args[4]).name.removesuffix(".download")
                    Path(args[4]).write_bytes(originals[name])
                with mock.patch.object(release,"run",side_effect=fake) as downloader:
                    release.ensure_originals(Path(tmp))
                    self.assertEqual(downloader.call_count,2)
                    release.ensure_originals(Path(tmp))
                    self.assertEqual(downloader.call_count,2)
            for name,data in originals.items():
                self.assertEqual((Path(tmp)/name).read_bytes(),data)

if __name__=="__main__":
    unittest.main()
