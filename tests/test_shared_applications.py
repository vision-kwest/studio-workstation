"""Real shell linking and native GIO launcher discovery; no AWS or desktop login."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class SharedApplications(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.shared = self.root / "studio/share/applications"
        source = (ROOT / "scripts/setup_workstation_software.sh").read_text()
        self.code = source.split("<<'STUDIO_APPLICATIONS'\n", 1)[1].split("\nSTUDIO_APPLICATIONS", 1)[0]
        self.code = self.code.replace("/studio", str(self.root / "studio"))

    def setup_menu(self, home):
        return subprocess.run(["bash", "-se"], input=self.code, text=True, capture_output=True,
                              env={**os.environ, "HOME": str(home)})

    def test_migration_preserves_originals_and_shared_collisions_then_retries(self):
        home = self.root / "home"
        applications = home / ".local/share/applications"
        applications.mkdir(parents=True)
        (applications / "local.desktop").write_text("local entry")
        (applications / "same.desktop").write_text("local version")
        self.shared.mkdir(parents=True)
        (self.shared / "same.desktop").write_text("shared version")
        for _ in range(2):
            result = self.setup_menu(home)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(applications.resolve(), self.shared)
            self.assertEqual((self.shared / "local.desktop").read_text(), "local entry")
            self.assertEqual((self.shared / "same.desktop").read_text(), "shared version")
            backup = applications.with_name("applications.before-studio")
            self.assertEqual((backup / "same.desktop").read_text(), "local version")
        applications.unlink()  # Also recover an interruption between backup and linking.
        self.assertEqual(self.setup_menu(home).returncode, 0)
        self.assertEqual(applications.resolve(), self.shared)

    def test_conflicts_are_preserved(self):
        for conflict in ("file", "foreign-link", "backup"):
            with self.subTest(conflict=conflict):
                home = self.root / conflict
                applications = home / ".local/share/applications"
                applications.parent.mkdir(parents=True)
                if conflict == "file":
                    applications.write_text("keep")
                elif conflict == "foreign-link":
                    applications.symlink_to(self.root / "foreign")
                else:
                    applications.mkdir()
                    (applications / "keep").touch()
                    applications.with_name("applications.before-studio").write_text("keep")
                self.assertNotEqual(self.setup_menu(home).returncode, 0)
                if conflict == "foreign-link":
                    self.assertEqual(applications.readlink(), self.root / "foreign")
                elif conflict == "file":
                    self.assertEqual(applications.read_text(), "keep")
                else:
                    self.assertTrue((applications / "keep").exists())
                    self.assertEqual(applications.with_name("applications.before-studio").read_text(), "keep")

    def inspect_app(self, home, desktop_id, launch=False):
        probe = """import json, sys
from gi.repository import Gio
app = next((item for item in Gio.AppInfo.get_all() if item.get_id() == sys.argv[1]), None)
if app and sys.argv[2] == 'True':
    assert app.launch([], None)
print(json.dumps([app.get_name(), app.should_show()] if app else None))
"""
        result = subprocess.run(["/usr/bin/python3", "-c", probe, desktop_id, str(launch)],
                                text=True, capture_output=True, check=True,
                                env={**os.environ, "HOME": str(home),
                                     "XDG_DATA_HOME": str(home / ".local/share"),
                                     "XDG_DATA_DIRS": str(self.root / "system")})
        if launch:
            self.assertIn("shared launcher ran", result.stderr)
        return json.loads(result.stdout)

    def test_native_discovery_launch_edit_and_delete_from_two_homes(self):
        first, second = self.root / "first", self.root / "second"
        for home in (first, second):
            self.assertEqual(self.setup_menu(home).returncode, 0)
        executable = self.root / "studio/probe"
        executable.write_text("#!/bin/sh\nprintf 'shared launcher ran' >&2\n")
        executable.chmod(0o755)
        entry = (first / ".local/share/applications/studio-test.desktop")
        entry.write_text(f"[Desktop Entry]\nType=Application\nName=Shared Probe\nExec={executable}\n"
                         f"TryExec={executable}\nIcon=applications-other\nTerminal=false\nCategories=Utility;\n")
        subprocess.run(["desktop-file-validate", str(entry)], check=True)
        system = self.root / "system/applications"
        system.mkdir(parents=True)
        (system / "system-test.desktop").write_text(entry.read_text().replace("Shared Probe", "System Probe"))
        for home in (first, second):
            self.assertEqual(self.inspect_app(home, "studio-test.desktop", launch=True), ["Shared Probe", True])
            self.assertEqual(self.inspect_app(home, "system-test.desktop"), ["System Probe", True])
        entry.write_text(entry.read_text().replace("Shared Probe", "Edited on first"))
        self.assertEqual(self.inspect_app(second, "studio-test.desktop"), ["Edited on first", True])
        (second / ".local/share/applications/studio-test.desktop").unlink()
        self.assertIsNone(self.inspect_app(first, "studio-test.desktop"))


if __name__ == "__main__":
    unittest.main()
