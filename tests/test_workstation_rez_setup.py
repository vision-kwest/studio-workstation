"""Offline CPU Rez installer tests; no network, AWS, EFS or real installation."""
import contextlib
import hashlib
import io
from pathlib import Path
import subprocess
import tarfile
import tempfile
import unittest
from unittest import mock

from scripts import install_workstation_rez as rez


class FakeRunner:
    def __init__(self):
        self.calls = []
        self.revision = rez.STUDIO_REZ_COMMIT
        self.dirty = False
        self.fail_install = False

    def __call__(self, args, **kwargs):
        self.calls.append((args, kwargs))
        if args[:1] == ["git"]:
            value = self.revision if args[-2:] == ["rev-parse", "HEAD"] else (
                " M env/studio-prod.sh" if self.dirty else "")
            return subprocess.CompletedProcess(args, 0, stdout=value + "\n")
        if self.fail_install:
            raise subprocess.CalledProcessError(1, args)
        dest = Path(args[-1])
        bin_dir = dest / "bin/rez"
        bin_dir.mkdir(parents=True)
        (bin_dir / ".rez_production_install").write_text(rez.VERSION)
        cli = bin_dir / "rez-env"
        cli.write_text(
            "#!/bin/bash\n"
            'printf "%s\n" "$REZ_PACKAGES_PATH" "$REZ_CONFIG_FILE" "$PATH" "$1"\n')
        cli.chmod(0o755)
        return subprocess.CompletedProcess(args, 0)


class WorkstationRezSetupTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.archive = self.root / "rez-3.4.0.tar.gz"
        self._archive()
        self.wheels = self.root / "wheels"
        self.wheels.mkdir()
        self.wheel = self.wheels / "example-1.0-py3-none-any.whl"
        self.wheel.write_bytes(b"synthetic approved wheel")
        self.manifest = self.root / "wheelhouse.sha256"
        self._manifest()
        self.source = self.root / "studio-rez"
        (self.source / "config").mkdir(parents=True)
        (self.source / "env").mkdir()
        (self.source / "config/rezconfig.prod.py").write_text(
            'packages_path=["/opt/studio/packages/prod"]\n'
            'implicit_packages=["platform","arch","os"]\n')
        (self.source / "env/studio-prod.sh").write_text(
            f'export STUDIO_REZ_ROOT="{self.source}"\n'
            'export STUDIO_PACKAGE_ROOT="/opt/studio/packages"\n'
            'export STUDIO_PACKAGE_PROD="$STUDIO_PACKAGE_ROOT/prod"\n'
            'export REZ_CONFIG_FILE="$STUDIO_REZ_ROOT/config/rezconfig.prod.py"\n'
            'export REZ_PACKAGES_PATH="$STUDIO_PACKAGE_PROD"\n'
        )
        self.target = self.root / "opt/studio/rez/3.4.0"
        self.wrapper = self.root / "usr/local/bin/studio-rez-env"
        self.run = FakeRunner()
        self.sha = mock.patch.object(rez, "SOURCE_SHA256", hashlib.sha256(
            self.archive.read_bytes()).hexdigest())
        self.sha.start()
        self.addCleanup(self.sha.stop)

    def _archive(self, *, member="rez-3.4.0/install.py", kind="file"):
        with tarfile.open(self.archive, "w:gz") as tar:
            info = tarfile.TarInfo(member)
            if kind == "symlink":
                info.type = tarfile.SYMTYPE
                info.linkname = "/etc/passwd"
                tar.addfile(info)
            else:
                data = b"# offline upstream installer fixture\n"
                info.size = len(data)
                tar.addfile(info, io.BytesIO(data))

    def _manifest(self):
        digest = hashlib.sha256(self.wheel.read_bytes()).hexdigest()
        self.manifest.write_text(f"{digest}  {self.wheel.name}\n")

    def config(self, action=rez.plan):
        return action(self.archive, self.wheels, self.manifest, self.source,
                      target=self.target, wrapper=self.wrapper, run=self.run)

    def test_plan_checks_prod_config_and_pinned_version_without_writes(self):
        result = self.config()
        self.assertEqual(result["wheel_count"], 1)
        self.assertFalse(result["already_installed"])
        self.assertEqual(result["target"], self.target)
        self.assertFalse(self.target.exists())
        self.assertFalse(self.wrapper.exists())
        self.assertEqual(len(self.run.calls), 2)
        self.assertTrue(all(args[0][0] == "git" for args in self.run.calls))

    def test_reject_wrong_rez_source_archive_checksum(self):
        self.archive.write_bytes(b"mutated")
        with self.assertRaisesRegex(ValueError, "SHA-256"):
            self.config()
        self.assertEqual(self.run.calls, [])

    def test_reject_unlisted_bad_hash_or_symlink_wheels(self):
        self.wheel.write_bytes(b"wrong")
        with self.assertRaisesRegex(ValueError, "Wheelhouse SHA-256"):
            self.config()
        self._manifest()
        (self.wheels / "extra.whl").write_bytes(b"extra")
        with self.assertRaisesRegex(ValueError, "exactly"):
            self.config()
        (self.wheels / "extra.whl").unlink()
        self.wheel.unlink()
        self.wheel.symlink_to(self.manifest)
        with self.assertRaisesRegex(ValueError, "exactly"):
            self.config()

    def test_reject_changed_checkout_dirty_tree_and_dev_resolve(self):
        self.run.revision = "0" * 40
        with self.assertRaisesRegex(ValueError, "revision"):
            self.config()
        self.run.revision = rez.STUDIO_REZ_COMMIT
        self.run.dirty = True
        with self.assertRaisesRegex(ValueError, "worktree"):
            self.config()
        self.run.dirty = False
        (self.source / "config/rezconfig.prod.py").write_text(
            'packages_path=["/opt/studio/packages/dev"]\n'
            'implicit_packages=["platform","arch","os"]\n')
        with self.assertRaisesRegex(ValueError, "production config"):
            self.config()

    def test_upstream_install_is_offline_and_checks_production_marker(self):
        result = self.config(rez.install)
        self.assertTrue(result["changed"])
        install_calls = [x for x in self.run.calls if x[0][0] != "git"]
        self.assertEqual(len(install_calls), 1)
        argv, opts = install_calls[0]
        self.assertEqual(argv[:2], [rez.sys.executable, "-E"])
        self.assertTrue(argv[2].endswith("/rez-3.4.0/install.py"))
        self.assertEqual(opts["env"]["PIP_NO_INDEX"], "1")
        self.assertEqual(opts["env"]["PIP_FIND_LINKS"], str(self.wheels))
        self.assertTrue((self.target / "bin/rez/.rez_production_install").is_file())
        self.assertEqual(self.wrapper.stat().st_mode & 0o777, 0o755)

    def test_second_run_is_idempotent_and_does_not_reinstall(self):
        self.config(rez.install)
        self.run.calls.clear()
        result = self.config(rez.install)
        self.assertFalse(result["changed"])
        self.assertEqual(len(self.run.calls), 2)
        self.assertEqual(self.wrapper.stat().st_mode & 0o777, 0o755)

    def test_new_shell_forwards_arguments_and_uses_only_prod_rez_paths(self):
        self.config(rez.install)
        output = subprocess.run(["bash", str(self.wrapper), "usd-26.05"], check=True,
                                text=True, capture_output=True).stdout.splitlines()
        self.assertEqual(output[0], "/opt/studio/packages/prod")
        self.assertEqual(output[1], str(self.source / "config/rezconfig.prod.py"))
        self.assertEqual(output[3], "usd-26.05")
        self.assertNotIn(str(self.target / "bin"), output[2])

    def test_existing_foreign_launcher_or_install_refuses_overwrite(self):
        self.wrapper.parent.mkdir(parents=True)
        self.wrapper.write_text("#!/bin/sh\nmalicious\n")
        with self.assertRaisesRegex(ValueError, "launcher differs"):
            self.config(rez.install)
        self.assertFalse(self.target.exists())
        self.wrapper.unlink()
        self.target.mkdir(parents=True)
        with self.assertRaisesRegex(ValueError, "not approved"):
            self.config(rez.install)

    def test_unsafe_archive_symlink_is_rejected_without_install(self):
        self._archive(kind="symlink")
        with self.assertRaisesRegex(ValueError, "Unsafe"):
            self.config(rez.install)
        self.assertFalse(self.target.exists())
        self.assertFalse(self.wrapper.exists())

    def test_tar_path_traversal_is_rejected(self):
        self._archive(member="rez-3.4.0/../../escape")
        with self.assertRaisesRegex(ValueError, "Unsafe"):
            self.config(rez.install)
        self.assertFalse((self.root / "escape").exists())

    def test_install_error_never_publishes_partial_version_or_launcher(self):
        self.run.fail_install = True
        with self.assertRaises(subprocess.CalledProcessError):
            self.config(rez.install)
        self.assertFalse(self.target.exists())
        self.assertFalse(self.wrapper.exists())

    def test_missing_production_marker_cannot_claim_success(self):
        class BrokenRunner(FakeRunner):
            def __call__(self, args, **kwargs):
                result = super().__call__(args, **kwargs)
                if args[0] != "git":
                    (Path(args[-1]) / "bin/rez/.rez_production_install").unlink()
                return result
        self.run = BrokenRunner()
        with self.assertRaisesRegex(RuntimeError, "verification failed"):
            self.config(rez.install)
        self.assertFalse(self.target.exists())

    def test_no_installation_is_triggered_by_module_import_or_plan(self):
        self.config()
        self.assertFalse(self.target.exists())
        self.assertFalse(self.wrapper.exists())

    def test_cli_has_no_implicit_execution_without_all_artifacts(self):
        with mock.patch.object(rez, "install") as installer:
            with contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit):
                    rez.main([])
            installer.assert_not_called()


if __name__ == "__main__":
    unittest.main()
