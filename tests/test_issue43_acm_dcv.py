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

    def test_restart_verifies_console_json_not_unrelated_status_text(self):
        with mock.patch.object(tls, "command", side_effect=[
            "", "active", '{"id":"other","message":"status: running","status":"initializing"}',
            "active", '{"id":"console","status":"running"}',
        ]) as cmds, mock.patch.object(tls.time, "sleep") as sleep:
            tls.restart_and_check()
        self.assertEqual(cmds.call_args_list[0].args[0], ["systemctl", "restart", "dcvserver"])
        sleep.assert_called_once_with(2)

    def test_restart_rejects_console_not_ready(self):
        with mock.patch.object(tls, "command", side_effect=lambda argv:
                "active" if argv[0] == "systemctl" and "is-active" in argv
                else '{"id":"console","status":"initializing"}'), \
             mock.patch.object(tls.time, "sleep") as sleep:
            with self.assertRaisesRegex(RuntimeError, "did not become ready"):
                tls.restart_and_check()
        self.assertEqual(sleep.call_count, 12)

    def test_enroll_builtin_default_without_preexisting_files(self):
        (self.dcv / "dcv.pem").unlink()
        (self.dcv / "dcv.key").unlink()
        (self.dcv / tls.ACTIVE_NAME).unlink()
        import shutil
        shutil.rmtree(self.store)
        with mock.patch.object(tls.os, "geteuid", return_value=0), self.perms(), \
             mock.patch.object(tls.os, "fchown"), mock.patch.object(tls.os, "chown"), \
             mock.patch.object(tls, "verify_material"), \
             mock.patch.object(tls, "restart_and_check") as ready:
            text = tls.enroll_default(self.src, self.dcv)
        self.assertIn("First custom certificate installed", text)
        ready.assert_called_once()
        self.assertEqual((self.dcv / "dcv.pem").read_text(), "FULLCHAIN CERT\n")
        self.assertEqual((self.dcv / "dcv.key").read_text(), "PRIVATE KEY\n")

    def test_enroll_failure_restores_builtin_default(self):
        (self.dcv / "dcv.pem").unlink()
        (self.dcv / "dcv.key").unlink()
        (self.dcv / tls.ACTIVE_NAME).unlink()
        import shutil
        shutil.rmtree(self.store)
        with mock.patch.object(tls.os, "geteuid", return_value=0), self.perms(), \
             mock.patch.object(tls.os, "fchown"), mock.patch.object(tls.os, "chown"), \
             mock.patch.object(tls, "verify_material"), \
             mock.patch.object(tls, "restart_and_check",
                               side_effect=[RuntimeError("failed new TLS"), None]) as ready:
            with self.assertRaisesRegex(RuntimeError, "failed new TLS"):
                tls.enroll_default(self.src, self.dcv)
        self.assertEqual(ready.call_count, 2)
        for name in ("dcv.pem", "dcv.key", tls.ACTIVE_NAME):
            self.assertFalse((self.dcv / name).exists())
            self.assertFalse((self.dcv / name).is_symlink())

    def test_enroll_refuses_to_replace_existing_custom_certificate(self):
        (self.dcv / "dcv.pem").unlink()
        (self.dcv / "dcv.pem").write_text("existing")
        with mock.patch.object(tls.os, "geteuid", return_value=0):
            with self.assertRaisesRegex(RuntimeError, "preexisting"):
                tls.enroll_default(self.src, self.dcv)
        self.assertEqual((self.dcv / "dcv.pem").read_text(), "existing")

    def test_awcp_refresh_sandbox_exception_minimal(self):
        # Regression: AWCP's ProtectSystem=full and capability bounding set
        # blocked certificate generation/chown after boot until both exceptions.
        file = Path(__file__).resolve().parents[1] / "infra/issue43-awcp-dcv-refresh.conf"
        content = file.read_text()
        lines = [line.strip() for line in content.splitlines()
                 if line.strip() and not line.lstrip().startswith("#")]
        self.assertEqual(lines, ["[Service]",
                                 "ReadWritePaths=/etc/dcv",
                                 "CapabilityBoundingSet=CAP_CHOWN"])
        self.assertNotIn("ProtectSystem=false", content)
        self.assertNotIn("NoNewPrivileges=false", content)

    def test_unchanged_cert_pair_does_not_restart_or_allocate_generation(self):
        (self.store / "gen-original" / "dcv.pem").write_bytes(
            (self.src / "fullchain.pem").read_bytes())
        (self.store / "gen-original" / "dcv.key").write_bytes(
            (self.src / "privkey.pem").read_bytes())
        before = set(p.name for p in self.store.iterdir())
        with mock.patch.object(tls.os, "geteuid", return_value=0), self.perms(), \
             mock.patch.object(tls, "verify_material"), \
             mock.patch.object(tls, "restart_and_check") as restart, \
             mock.patch.object(tls, "prune_managed_generations") as prune, \
             mock.patch.object(tls, "command", side_effect=[
                 "active", '{"id":"console","owner":"workstation","status":"running"}']):
            message = tls.activate(self.src, self.dcv)
        self.assertIn("unchanged", message)
        restart.assert_not_called()
        prune.assert_not_called()
        self.assertEqual(before, set(p.name for p in self.store.iterdir()))
        self.assertEqual(os.readlink(self.dcv / tls.ACTIVE_NAME),
                         f"{tls.STORE_NAME}/gen-original")

    def test_unchanged_material_cannot_mask_failed_console(self):
        (self.store / "gen-original" / "dcv.pem").write_bytes(
            (self.src / "fullchain.pem").read_bytes())
        (self.store / "gen-original" / "dcv.key").write_bytes(
            (self.src / "privkey.pem").read_bytes())
        with mock.patch.object(tls.os, "geteuid", return_value=0), self.perms(), \
             mock.patch.object(tls, "verify_material"), \
             mock.patch.object(tls, "command", return_value="inactive"), \
             mock.patch.object(tls, "restart_and_check") as restart:
            with self.assertRaisesRegex(RuntimeError, "not active"):
                tls.activate(self.src, self.dcv)
            restart.assert_not_called()
        self.assertEqual(os.readlink(self.dcv / tls.ACTIVE_NAME),
                         f"{tls.STORE_NAME}/gen-original")

    def test_invalid_source_pair_is_rejected_even_when_current_matches(self):
        for filename, current in (("fullchain.pem", "dcv.pem"),
                                  ("privkey.pem", "dcv.key")):
            (self.store / "gen-original" / current).write_bytes(
                (self.src / filename).read_bytes())
        with mock.patch.object(tls.os, "geteuid", return_value=0), \
             mock.patch.object(tls, "verify_material",
                               side_effect=ValueError("invalid certificate")), \
             mock.patch.object(tls, "restart_and_check") as restart:
            with self.assertRaisesRegex(ValueError, "invalid certificate"):
                tls.activate(self.src, self.dcv)
            restart.assert_not_called()

    def prepare_safe_retention(self):
        os.chmod(self.store, 0o700)
        orig = self.store / "gen-original"
        os.chmod(orig, 0o700)
        for name in tls.LINKS:
            os.chmod(orig / name, 0o600)

    def add_managed_pair(self, name):
        d = self.store / name
        d.mkdir(mode=0o700)
        for filename in tls.LINKS:
            (d / filename).write_text("OLD SENSITIVE CONTENT")
            os.chmod(d / filename, 0o600)
        return d

    def test_prune_preserves_active_and_immediate_previous(self):
        self.prepare_safe_retention()
        old = self.add_managed_pair("gen-oldervalid")
        unknown = self.store / "user-owned-backup"
        unknown.mkdir()
        with mock.patch.object(tls.os, "geteuid", return_value=0), self.perms(), \
             mock.patch.object(tls.os, "fchown"), mock.patch.object(tls.os, "chown"), \
             mock.patch.object(tls, "verify_material"), \
             mock.patch.object(tls, "restart_and_check") as restarted:
            message = tls.activate(self.src, self.dcv)
        self.assertIn("Activated", message)
        self.assertEqual(restarted.call_count, 1)
        self.assertFalse(old.exists(), "old validated generation should be pruned")
        self.assertTrue(unknown.exists(), "unfamiliar directory must be preserved")
        self.assertTrue((self.store / "gen-original").exists(), "rollback generation retained")
        target = os.readlink(self.dcv / tls.ACTIVE_NAME).split("/")[-1]
        self.assertTrue((self.store / target).exists(), "active generation retained")
        self.assertEqual(
            len([x for x in self.store.iterdir()
                 if x.name.startswith("gen-")]), tls.RETAIN_GENERATIONS)

    def test_prune_refuses_symlinks_unexpected_files_and_hardlinks(self):
        self.prepare_safe_retention()
        untouched = self.add_managed_pair("gen-symlinkbad")
        (untouched / "dcv.key").unlink()
        (untouched / "dcv.key").symlink_to(self.src / "privkey.pem")
        extra = self.add_managed_pair("gen-extrafiles")
        (extra / "foreign.txt").write_text("not owned by our lifecycle")
        hardlink = self.add_managed_pair("gen-hardlinks")
        (hardlink / "dcv.key").unlink()
        os.link(self.src / "privkey.pem", hardlink / "dcv.key")
        removed, skipped = tls.prune_managed_generations(
            self.store, "gen-original", "gen-currentnow", os.getuid(), os.getgid())
        self.assertEqual(removed, 0)
        self.assertEqual(skipped, 3)
        self.assertTrue(untouched.is_dir())
        self.assertTrue(extra.is_dir())
        self.assertTrue(hardlink.is_dir())
        self.assertTrue((self.src / "privkey.pem").exists())

    def test_failed_retention_cleanup_does_not_roll_back_good_certificate(self):
        self.prepare_safe_retention()
        with mock.patch.object(tls.os, "geteuid", return_value=0), self.perms(), \
             mock.patch.object(tls.os, "fchown"), mock.patch.object(tls.os, "chown"), \
             mock.patch.object(tls, "verify_material"), \
             mock.patch.object(tls, "restart_and_check") as ready, \
             mock.patch.object(tls, "prune_managed_generations",
                               side_effect=OSError("cleanup blocked")):
            text = tls.activate(self.src, self.dcv)
        self.assertIn("Activated", text)
        ready.assert_called_once()
        self.assertNotEqual(os.readlink(self.dcv / tls.ACTIVE_NAME),
                            f"{tls.STORE_NAME}/gen-original")

    def test_rollback_on_failed_restart_does_not_prune(self):
        self.prepare_safe_retention()
        old = self.add_managed_pair("gen-recoverold")
        with mock.patch.object(tls.os, "geteuid", return_value=0), self.perms(), \
             mock.patch.object(tls.os, "fchown"), mock.patch.object(tls.os, "chown"), \
             mock.patch.object(tls, "verify_material"), \
             mock.patch.object(tls, "prune_managed_generations") as prune, \
             mock.patch.object(tls, "restart_and_check",
                               side_effect=[RuntimeError("new cert failed"), None]):
            with self.assertRaisesRegex(RuntimeError, "new cert failed"):
                tls.activate(self.src, self.dcv)
        prune.assert_not_called()
        self.assertTrue(old.exists())
        self.assertEqual(os.readlink(self.dcv / tls.ACTIVE_NAME),
                         f"{tls.STORE_NAME}/gen-original")

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
        self.assertEqual(statement["Resource"], "arn:aws:acm:us-east-1:401233741942:certificate/b753beee-5659-4311-a52f-39326b7b3243")
        self.assertNotIn("route53:", json.dumps([p["AssumeExportRoleStatement"], p["ExportRolePolicy"]]))
        self.assertEqual(p["CpuInstanceId"], "i-0bbfbdc878d3a2320")
        self.assertEqual(p["AssumeExportRoleStatement"]["Condition"]["StringEquals"]["aws:userid"], "AROAV223FBB3JOQHYPKDX:i-0bbfbdc878d3a2320")


if __name__ == "__main__":
    unittest.main()
