"""Issue #43 Task 2: offline ACM Workload Credentials Provider -> DCV contracts.

Never invokes AWS, DCV, certificate export or real systemd. Mocks restart,
privileged ownership, and OpenSSL while exercising private files and links.
"""
import json
import os
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest import mock

from scripts import studio_dcv_acm as tls


class DcvAcmTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.src = root / "stage"
        self.src.mkdir()
        (self.src / "fullchain.pem").write_text("FULLCHAIN CERT\n")
        (self.src / "privkey.pem").write_text("PRIVATE KEY\n")
        self.dcv = root / "dcv"
        self.dcv.mkdir()
        self.store = self.dcv / tls.STORE_NAME
        self.store.mkdir()
        original = self.store / "gen-original"
        original.mkdir()
        (original / "dcv.pem").write_text("OLD CERT")
        (original / "dcv.key").write_text("OLD KEY")
        (self.dcv / tls.ACTIVE_NAME).symlink_to(f"{tls.STORE_NAME}/gen-original")
        for name, link in tls.LINKS.items():
            (self.dcv / name).symlink_to(link)

    def perms(self):
        return mock.patch.object(tls.pwd, "getpwnam", return_value=SimpleNamespace(
            pw_uid=os.getuid(), pw_gid=os.getgid()))

    def activate(self):
        with mock.patch.object(tls.os, "geteuid", return_value=0), self.perms(), \
             mock.patch.object(tls.os, "fchown"), mock.patch.object(tls.os, "chown"), \
             mock.patch.object(tls, "verify_material"), \
             mock.patch.object(tls, "restart_and_check") as ready:
            result = tls.activate(self.src, self.dcv)
            return result, ready.call_count

    def test_success_single_active_generation_switch(self):
        result, restarts = self.activate()
        self.assertIn("console ready", result)
        self.assertEqual(restarts, 1)
        link = os.readlink(self.dcv / tls.ACTIVE_NAME)
        self.assertNotEqual(link, f"{tls.STORE_NAME}/gen-original")
        self.assertTrue(link.startswith(tls.STORE_NAME + "/gen-"))
        self.assertEqual((self.dcv / "dcv.pem").read_text(), "FULLCHAIN CERT\n")
        self.assertEqual((self.dcv / "dcv.key").read_text(), "PRIVATE KEY\n")
        for name in tls.LINKS:
            self.assertEqual((self.dcv / name).stat().st_mode & 0o777, 0o600)
        self.assertEqual((self.dcv / tls.STORE_NAME / link.split("/")[-1]).stat().st_mode & 0o777, 0o700)

    def test_failed_dcv_restart_rolls_back_to_previous_generation(self):
        with mock.patch.object(tls.os, "geteuid", return_value=0), self.perms(), \
             mock.patch.object(tls.os, "fchown"), mock.patch.object(tls.os, "chown"), \
             mock.patch.object(tls, "verify_material"), \
             mock.patch.object(tls, "restart_and_check",
                               side_effect=[RuntimeError("new cert failed"), None]) as ready:
            with self.assertRaisesRegex(RuntimeError, "new cert failed"):
                tls.activate(self.src, self.dcv)
        self.assertEqual(ready.call_count, 2)
        self.assertEqual(os.readlink(self.dcv / tls.ACTIVE_NAME), f"{tls.STORE_NAME}/gen-original")
        self.assertEqual((self.dcv / "dcv.pem").read_text(), "OLD CERT")
        self.assertEqual((self.dcv / "dcv.key").read_text(), "OLD KEY")

    def test_invalid_staged_material_does_not_touch_active(self):
        with mock.patch.object(tls.os, "geteuid", return_value=0), \
             mock.patch.object(tls, "verify_material", side_effect=ValueError("bad pair")), \
             mock.patch.object(tls, "restart_and_check") as ready:
            with self.assertRaisesRegex(ValueError, "bad pair"):
                tls.activate(self.src, self.dcv)
            ready.assert_not_called()
        self.assertEqual(os.readlink(self.dcv / tls.ACTIVE_NAME), f"{tls.STORE_NAME}/gen-original")

    def test_staged_material_corrupted_after_copy_fails_closed(self):
        with mock.patch.object(tls.os, "geteuid", return_value=0), self.perms(), \
             mock.patch.object(tls.os, "fchown"), mock.patch.object(tls.os, "chown"), \
             mock.patch.object(tls, "verify_material", side_effect=[None, ValueError("corrupted")]), \
             mock.patch.object(tls, "restart_and_check") as ready:
            with self.assertRaisesRegex(ValueError, "corrupted"):
                tls.activate(self.src, self.dcv)
            ready.assert_not_called()
        self.assertEqual(os.readlink(self.dcv / tls.ACTIVE_NAME), f"{tls.STORE_NAME}/gen-original")

    def test_no_enrollment_refuses_original_res_files(self):
        (self.dcv / "dcv.pem").unlink()
        (self.dcv / "dcv.pem").write_text("RES ORIGINAL")
        with mock.patch.object(tls.os, "geteuid", return_value=0):
            with self.assertRaisesRegex(RuntimeError, "not enrolled"):
                tls.activate(self.src, self.dcv)
        self.assertEqual((self.dcv / "dcv.pem").read_text(), "RES ORIGINAL")

    def test_wrong_active_link_refused(self):
        (self.dcv / tls.ACTIVE_NAME).unlink()
        (self.dcv / tls.ACTIVE_NAME).symlink_to("/tmp/foreign")
        with self.assertRaisesRegex(RuntimeError, "Invalid active"):
            tls.check_enrolled(self.dcv)

    def test_source_symlink_refused(self):
        p = self.src / "fullchain.pem"
        p.unlink()
        p.symlink_to(self.src / "privkey.pem")
        with mock.patch.object(tls.os, "geteuid", return_value=0):
            with self.assertRaisesRegex(RuntimeError, "non-symlink"):
                tls.activate(self.src, self.dcv)

    def test_unprivileged_execution_refused(self):
        with mock.patch.object(tls.os, "geteuid", return_value=1000):
            with self.assertRaises(PermissionError):
                tls.activate(self.src, self.dcv)

    def test_mismatched_key_rejected_before_switch(self):
        with mock.patch.object(tls, "command",
                               side_effect=["", f"Hostname {tls.HOST} does match certificate",
                                            "CERT PUB", "KEY PUB"]):
            with self.assertRaisesRegex(ValueError, "Certificate/key mismatch"):
                tls.verify_material(self.src / "fullchain.pem", self.src / "privkey.pem")

    def test_wrong_host_rejected(self):
        with mock.patch.object(tls, "command", side_effect=["", "Hostname does NOT match certificate"]):
            with self.assertRaisesRegex(ValueError, "hostname mismatch"):
                tls.verify_material(self.src / "fullchain.pem", self.src / "privkey.pem")

    def test_expired_cert_rejected(self):
        import subprocess
        with mock.patch.object(tls, "command", side_effect=subprocess.CalledProcessError(1, "openssl")):
            with self.assertRaises(subprocess.CalledProcessError):
                tls.verify_material(self.src / "fullchain.pem", self.src / "privkey.pem")

    def test_approved_config_stages_fullchain_without_chain_separate(self):
        root = Path(__file__).resolve().parents[1]
        cfg = (root / "infra/issue43-awcp.review-only.toml").read_text()
        self.assertIn('certificate_path = "/var/lib/studio/dcv-acm/fullchain.pem"', cfg)
        self.assertIn('private_key_path = "/var/lib/studio/dcv-acm/privkey.pem"', cfg)
        self.assertIn('refresh_command = "/usr/local/sbin/studio-dcv-acm-activate"', cfg)
        self.assertNotIn("\nchain_path =", cfg)
        self.assertNotIn("AWS_ACCESS_KEY_ID=", cfg)
        self.assertNotIn("route53:", cfg)

    def test_iam_is_export_only_and_cert_scoped(self):
        root = Path(__file__).resolve().parents[1]
        p = json.loads((root / "infra/issue43-acm-iam.review-only.json").read_text())
        statement = p["ExportRolePolicy"]["Statement"][0]
        self.assertEqual(statement["Action"], "acm:ExportCertificate")
        self.assertEqual(statement["Resource"], "REPLACE_EXACT_ACM_CERTIFICATE_ARN")
        self.assertNotIn("route53:", json.dumps([p["AssumeExportRoleStatement"], p["ExportRolePolicy"]]))
        self.assertIn("shared", p["Description"])


if __name__ == "__main__":
    unittest.main()
