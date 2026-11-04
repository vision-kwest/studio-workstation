"""CPU-only RES-derived EFS adapter tests. All files/mount calls are mocked."""
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase, mock
import subprocess

from runtime import standalone_efs as efs

FS = "fs-0123456789abcdef0"
AP = "fsap-0123456789abcdef0"


class StandaloneEfsTests(TestCase):
    def setUp(self):
        t = TemporaryDirectory()
        self.addCleanup(t.cleanup)
        self.path = Path(t.name)
        self.mountpoint = self.path / "packages"
        patcher = mock.patch.object(efs, "MOUNTPOINT", self.mountpoint)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.fstab = self.path / "fstab"
        self.fstab.write_text("# original entry\n", encoding="utf-8")
        self.mountinfo = self.path / "mountinfo"
        self.mountinfo.write_text("", encoding="utf-8")
        self.called = []

    def active_mount(self, flags="ro,nosuid,nodev", fstype="nfs4"):
        self.mountinfo.write_text(
            f"33 25 0:50 / {self.mountpoint} {flags} - "
            f"{fstype} 127.0.0.1:/ rw,vers=4.1\n", encoding="utf-8")

    def do_mount(self, argv, *, check, timeout):
        self.called.append(tuple(argv))
        self.assertEqual(argv, ["mount", str(self.mountpoint)])
        self.assertTrue(check)
        self.assertEqual(timeout, 45)
        self.active_mount()
        return None

    def configure(self, **kwargs):
        return efs.configure(
            FS, AP, fstab=self.fstab, mountinfo=self.mountinfo,
            runner=kwargs.pop("runner", self.do_mount),
            sleeper=kwargs.pop("sleeper", lambda seconds: None),
            helper_available=kwargs.pop("helper_available", True), **kwargs)

    def test_plan_does_not_touch_files_or_mount(self):
        before = self.fstab.read_text(encoding="utf-8")
        p = efs.plan(FS, AP, fstab_text=before)
        self.assertEqual(p["fstab_action"], "add")
        self.assertFalse(p["aws_identity_verified"])
        self.assertIn("tls,iam,accesspoint=" + AP, p["fstab_entry"])
        self.assertIn(",ro,", p["fstab_entry"])
        self.assertIn("x-systemd.automount", p["fstab_entry"])
        self.assertEqual(self.fstab.read_text(encoding="utf-8"), before)
        self.assertEqual(self.called, [])

    def test_exact_cpu_mount_and_readback(self):
        result = self.configure()
        self.assertEqual(result, {"mounted": True, "changed": True})
        self.assertEqual(self.called, [("mount", str(self.mountpoint))])
        self.assertEqual(self.fstab.read_text().count("fs-" + FS[3:]), 1)
        self.assertNotIn("mount -a", self.fstab.read_text())

    def test_second_run_is_idempotent_without_new_mount(self):
        self.configure()
        self.called.clear()
        result = self.configure()
        self.assertEqual(result, {"mounted": True, "changed": False})
        self.assertEqual(self.called, [])
        self.assertEqual(self.fstab.read_text().count("fs-" + FS[3:]), 1)

    def test_matching_fstab_entry_can_mount_when_not_mounted(self):
        entry = efs.plan(FS, AP, fstab_text="")["fstab_entry"]
        self.fstab.write_text(entry + "\n")
        self.assertEqual(self.configure()["changed"], False)
        self.assertEqual(self.fstab.read_text(), entry + "\n")

    def test_conflicting_or_duplicate_fstab_rejected(self):
        entry = efs.plan(FS, AP, fstab_text="")["fstab_entry"]
        for contents in (entry.replace(AP, "fsap-aaaaaaaaaaaaaaaaa") + "\n",
                         entry + "\n" + entry + "\n"):
            with self.subTest(contents=contents):
                self.fstab.write_text(contents)
                with self.assertRaisesRegex(ValueError, "Conflicting or duplicate"):
                    self.configure()
                self.assertEqual(self.fstab.read_text(), contents)
                self.assertEqual(self.called, [])

    def test_cannot_treat_unmanaged_mount_as_ours(self):
        self.active_mount()
        with self.assertRaisesRegex(ValueError, "without matching"):
            self.configure()
        self.assertEqual(self.called, [])

    def test_writable_or_unexpected_mount_rejected(self):
        entry = efs.plan(FS, AP, fstab_text="")["fstab_entry"]
        self.fstab.write_text(entry + "\n")
        for flags, fs in (("rw,nosuid", "nfs4"), ("ro,nosuid", "ext4")):
            with self.subTest(flags=flags, fs=fs):
                self.active_mount(flags, fs)
                with self.assertRaisesRegex(ValueError, "Unexpected or writable"):
                    self.configure()
                self.assertEqual(self.called, [])

    def test_invalid_fs_accesspoint_and_gpu_are_refused(self):
        for fs, ap, profile in ((FS, AP, "gpu"), ("fs-invalid", AP, "cpu"),
                                (FS, "fsap-wrong", "cpu")):
            with self.subTest(fs=fs, ap=ap, profile=profile):
                with self.assertRaises(ValueError):
                    efs.plan(fs, ap, profile=profile)
        self.assertFalse(self.mountpoint.exists())

    def test_missing_helper_fails_before_fstab_write(self):
        with self.assertRaisesRegex(RuntimeError, "mount.efs"):
            self.configure(helper_available=False)
        self.assertEqual(self.fstab.read_text(), "# original entry\n")
        self.assertFalse(self.mountpoint.exists())

    def test_unmanaged_local_files_and_symlink_refused(self):
        self.mountpoint.mkdir()
        (self.mountpoint / "not-ours").write_text("keep")
        with self.assertRaisesRegex(ValueError, "Unmanaged files"):
            self.configure()
        (self.mountpoint / "not-ours").unlink()
        self.mountpoint.rmdir()
        self.mountpoint.symlink_to(self.path, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, "symlink"):
            self.configure()
        self.assertFalse(self.called)

    def test_transient_failure_retries_only_exact_mountpoint(self):
        attempts = []
        def run(argv, **kwargs):
            attempts.append(tuple(argv))
            if len(attempts) == 1:
                raise subprocess.CalledProcessError(32, argv)
            self.do_mount(argv, **kwargs)
        self.assertTrue(self.configure(runner=run)["mounted"])
        self.assertEqual(len(attempts), 2)
        self.assertEqual(self.called, [("mount", str(self.mountpoint))])

    def test_all_failed_retries_restore_only_new_fstab_line(self):
        def fail(argv, **kwargs):
            self.called.append(tuple(argv))
            raise subprocess.CalledProcessError(32, argv)
        with self.assertRaisesRegex(RuntimeError, "EFS mount failed"):
            self.configure(runner=fail)
        self.assertEqual(len(self.called), 3)
        self.assertEqual(self.fstab.read_text(), "# original entry\n")

    def test_success_without_read_only_mount_verification_rolls_back(self):
        with self.assertRaisesRegex(RuntimeError, "not verified"):
            self.configure(runner=lambda argv, **kwargs: None)
        self.assertEqual(self.fstab.read_text(), "# original entry\n")

    def test_existing_managed_fstab_is_not_removed_after_mount_failure(self):
        entry = efs.plan(FS, AP, fstab_text="")["fstab_entry"]
        self.fstab.write_text("# original\n" + entry + "\n")
        def fail(argv, **kwargs):
            raise subprocess.TimeoutExpired(argv, timeout=45)
        with self.assertRaisesRegex(RuntimeError, "EFS mount failed"):
            self.configure(runner=fail)
        self.assertEqual(self.fstab.read_text(), "# original\n" + entry + "\n")


if __name__ == "__main__":
    import unittest
    unittest.main()
