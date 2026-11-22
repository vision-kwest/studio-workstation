"""Exercise local Comfy first-launch setup with a real executable; no AWS or GUI."""
import fcntl
import json
import os
from pathlib import Path
import select
import shlex
import shutil
import subprocess
import sys
import tempfile
import unittest

SOURCE = Path(__file__).resolve().parents[1] / "scripts/comfy-desktop"
HARNESS = """from importlib.machinery import SourceFileLoader
from importlib.util import module_from_spec, spec_from_loader
from pathlib import Path
import sys
script, image, templates, *arguments = sys.argv[1:]
loader = SourceFileLoader('comfy', script)
helper = module_from_spec(spec_from_loader(loader.name, loader))
loader.exec_module(helper)
helper.APPIMAGE, helper.TEMPLATES = Path(image), Path(templates)
helper.STUDIO = Path(image).parent
sys.argv = [script, *arguments]
helper.main()
"""

class ComfyFirstLaunch(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.home, self.studio = self.root / "home with spaces", self.root / "studio"
        self.install = self.studio / "apps/comfyui/main/StudioComfyUI"
        for path in (self.install / "standalone-env", self.install / "ComfyUI/.venv/bin",
                     self.studio / "models/comfyui", self.studio / "projects/comfyui/input",
                     self.studio / "projects/comfyui/output"):
            path.mkdir(parents=True)
        (self.install / "ComfyUI/main.py").touch()
        (self.install / "ComfyUI/.venv/bin/python3").touch()
        self.templates = self.root / "templates"
        self.templates.mkdir()
        for name in ("settings.json", "installations.json"):
            (self.templates / name).write_text((SOURCE / name).read_text().replace("/studio", str(self.studio)))
        self.image = self.studio / "Comfy Desktop.AppImage"
        self.marker = self.root / "app-launched"
        self.image.write_text(f"#!{sys.executable}\nimport json, sys\nfrom pathlib import Path\n"
                              f"Path({str(self.marker)!r}).touch()\n"
                              "print(json.dumps(sys.argv[1:]), flush=True)\n"
                              "if '--hold' in sys.argv: sys.stdin.readline()\n")
        self.image.chmod(0o755)
        self.env = {k: v for k, v in os.environ.items()
                    if k not in ("XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_STATE_HOME")}
        self.env["HOME"] = str(self.home)
        self.settings = self.home / ".config/comfyui-desktop-2/settings.json"
        self.catalog = self.home / ".local/share/comfyui-desktop-2/installations.json"
        self.command = [sys.executable, "-c", HARNESS, str(SOURCE / "studio-comfy"),
                        str(self.image), str(self.templates)]

    def launch(self, *arguments):
        return subprocess.run([*self.command, *arguments], env=self.env, text=True,
                              capture_output=True, timeout=5)

    def test_fresh_profile_preserves_paths_and_passes_arguments(self):
        result = self.launch("--", "--example", "argument with spaces")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), ["--no-sandbox", "--example", "argument with spaces"])
        entry, = json.loads(self.catalog.read_text())
        expected = json.loads((self.templates / "settings.json").read_text())
        self.assertEqual(json.loads(self.settings.read_text()), {**expected, "autoLaunchOnStartup": entry["id"]})
        self.assertEqual(entry["installPath"], str(self.install))
        database = self.home / ".local/share/comfyui-studio/comfyui.db"
        self.assertEqual(shlex.split(entry["launchArgs"]),
                         ["--enable-manager", "--database-url", f"sqlite:///{database}"])
        self.assertTrue(database.parent.is_dir())

    def test_existing_files_and_preferences_survive_repeated_launches(self):
        originals = {self.settings: b'{ "theme": "light", "firstUseCompleted": false }\n',
                     self.catalog: b'[ { "id": "personal", "installPath": "/other/install" } ]\n'}
        for path, contents in originals.items():
            path.parent.mkdir(parents=True)
            path.write_bytes(contents)
        for _ in range(2):
            result = self.launch()
            self.assertEqual(result.returncode, 0, result.stderr)
            for path, contents in originals.items():
                self.assertEqual(path.read_bytes(), contents)

    def test_partial_setup_seeds_only_missing_file_and_honors_xdg_directories(self):
        for variable, directory in (("XDG_CONFIG_HOME", "config"), ("XDG_DATA_HOME", "data"),
                                    ("XDG_STATE_HOME", "state")):
            self.env[variable] = str(self.root / directory)
        settings = self.root / "config/comfyui-desktop-2/settings.json"
        settings.parent.mkdir(parents=True)
        settings.write_text('{"keep": true}\n')
        result = self.launch()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(settings.read_text(), '{"keep": true}\n')
        self.assertTrue((self.root / "data/comfyui-desktop-2/installations.json").is_file())
        self.assertTrue((self.root / "state/comfyui-studio/setup.lock").is_file())
        self.assertFalse(self.home.exists())

    def test_missing_shared_installation_stops_before_local_writes(self):
        (self.install / "ComfyUI/main.py").unlink()
        result = self.launch()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Shared ComfyUI path unavailable", result.stderr)
        self.assertEqual(result.stdout, "")
        self.assertFalse(self.home.exists())

    def test_update_replaces_different_or_missing_selection_preserving_preferences(self):
        shared, = json.loads((self.templates / "installations.json").read_text())
        for case in ("different-id", "wrong-path", "fresh", "settings-only", "catalog-only"):
            with self.subTest(case=case):
                for path in (self.settings, self.catalog):
                    path.unlink(missing_ok=True)
                    path.parent.mkdir(parents=True, exist_ok=True)
                selected_id = shared["id"] if case == "wrong-path" else "previous"
                if case not in ("fresh", "catalog-only"):
                    self.settings.write_text(json.dumps({"autoLaunchOnStartup": selected_id,
                                                        "theme": "light", "outputDir": "/my/output"}))
                if case not in ("fresh", "settings-only"):
                    old_path = self.install if case == "different-id" else self.studio / "old"
                    old = {**shared, "id": selected_id, "installPath": str(old_path)}
                    self.catalog.write_text(json.dumps([old, {**old, "id": "extra"}]))
                result = self.launch("--update", "--", "--example")
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(json.loads(result.stdout), ["--no-sandbox", "--example"])
                settings = json.loads(self.settings.read_text())
                chosen, = json.loads(self.catalog.read_text())
                self.assertEqual(settings["autoLaunchOnStartup"], shared["id"])
                self.assertEqual((chosen["id"], chosen["installPath"]), (shared["id"], str(self.install)))
                if case not in ("fresh", "catalog-only"):
                    self.assertEqual(settings, {"autoLaunchOnStartup": shared["id"],
                                                "theme": "light", "outputDir": "/my/output"})

    def test_update_matching_resolved_path_preserves_bytes_mtime_and_extra_entries(self):
        self.assertEqual(self.launch().returncode, 0)
        selected, = json.loads(self.catalog.read_text())
        alias = self.studio / "same installation"
        alias.symlink_to(self.install, target_is_directory=True)
        entries = [{**selected, "id": "extra"}, {**selected, "installPath": str(alias)}]
        self.catalog.write_text(json.dumps(entries, indent=1))
        before = {path: (path.read_bytes(), path.stat().st_mtime_ns)
                  for path in (self.settings, self.catalog)}
        result = self.launch("--update")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), ["--no-sandbox"])
        for path, original in before.items():
            self.assertEqual((path.read_bytes(), path.stat().st_mtime_ns), original)

    def test_update_rejects_conflicting_actions_and_invalid_shared_target_without_clobber(self):
        self.assertEqual(self.launch().returncode, 0)
        self.settings.write_text('{"autoLaunchOnStartup": "previous", "theme": "light"}\n')
        self.marker.unlink()
        before = {path: path.read_bytes() for path in
                  (self.settings, self.catalog, self.templates / "installations.json")}
        for arguments in (("--update", "--set-default", str(self.install)), ("--update",)):
            with self.subTest(arguments=arguments):
                if arguments == ("--update",):
                    (self.install / "ComfyUI/main.py").unlink()
                result = self.launch(*arguments)
                self.assertNotEqual(result.returncode, 0)
                self.assertFalse(self.marker.exists())
                for path, original in before.items():
                    self.assertEqual(path.read_bytes(), original)

    def test_invalid_helper_options_fail_before_any_setup(self):
        cases = [("-set-default", "/studio/apps/comfyui/main/StudioComfyUI"),
                 ("--unknown",), ("--set", str(self.install)), ("--upd",),
                 ("--set-default",), ("--update", "unexpected argument"),
                 ("--update", "--set-default", str(self.install)),
                 ("--set-default", str(self.install), "--", "--example")]

        def snapshot():
            return {path: (path.read_bytes(), path.stat().st_mtime_ns) if path.is_file() else None
                    for base in (self.templates, self.home) for path in base.rglob("*")}

        for existing in (False, True):
            for arguments in cases:
                with self.subTest(existing=existing, arguments=arguments):
                    shutil.rmtree(self.home, ignore_errors=True)
                    self.marker.unlink(missing_ok=True)
                    if existing:
                        for path, contents in ((self.settings, '{"theme":"light"}\n'),
                                               (self.catalog, '[{"id":"personal"}]\n')):
                            path.parent.mkdir(parents=True, exist_ok=True)
                            path.write_text(contents)
                    before = snapshot()
                    result = self.launch(*arguments)
                    observed = (result.returncode, "usage:" in result.stderr,
                                "error:" in result.stderr, self.marker.exists(),
                                self.home.exists(), snapshot() == before)
                    self.assertEqual(observed, (2, True, True, False, existing, True),
                                     f"stdout={result.stdout!r}\nstderr={result.stderr!r}")

    def test_native_suffix_preserves_helper_looking_flags_and_spaces(self):
        arguments = ["--set-default", "native path with spaces", "--update", "--help",
                     "--example", "--", "last value"]
        shared = {path: path.read_bytes() for path in self.templates.iterdir()}
        for prefix in ((), ("--update",)):
            with self.subTest(prefix=prefix):
                self.marker.unlink(missing_ok=True)
                result = self.launch(*prefix, "--", *arguments)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(json.loads(result.stdout), ["--no-sandbox", *arguments])
                self.assertTrue(self.marker.exists())
                for path, contents in shared.items():
                    self.assertEqual(path.read_bytes(), contents)

    def second_install(self):
        target = self.install.with_name("Alternate instance with spaces")
        shutil.copytree(self.install, target)
        return target

    def test_set_default_selects_registry_metadata_for_fresh_profiles_only(self):
        self.assertEqual(self.launch().returncode, 0)
        first, = json.loads(self.catalog.read_text())
        target = self.second_install()
        arguments = r'''--enable-manager --output-directory "C:\renders\shots" --label "artist's render" --literal '"quoted"' --database-url="sqlite:////old path/db" --database-url sqlite:////old.db'''
        chosen = {**first, "id": "inst-second", "name": "Second install", "installPath": str(target),
                  "version": "v0.43.0", "releaseTag": "v0.43.0-env7", "pythonVersion": "3.13.9",
                  "launchArgs": arguments, "envVars": {"TOKEN": "test-only-sentinel"}, "comfyVersion": "ignore"}
        self.catalog.write_text(json.dumps([first, chosen], indent=1))
        originals = {path: path.read_bytes() for path in (self.settings, self.catalog)}
        shared_settings = (self.templates / "settings.json").read_bytes()
        self.marker.unlink()
        result = self.launch("--set-default", str(target))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(self.marker.exists(), "Setter launched Desktop")
        selected, = json.loads((self.templates / "installations.json").read_text())
        for key in ("id", "name", "createdAt", "version", "releaseTag", "variant", "pythonVersion", "installPath"):
            self.assertEqual(selected[key], chosen[key])
        expected = ["--enable-manager", "--output-directory", r"C:\renders\shots", "--label", "artist's render",
                    "--literal", '"quoted"', "--database-url", "@DATABASE_URL@"]
        self.assertEqual(shlex.split(selected["launchArgs"]), expected)
        self.assertNotIn("envVars", selected)
        self.assertNotIn("comfyVersion", selected)
        self.assertEqual((self.templates / "settings.json").read_bytes(), shared_settings)
        for path, contents in originals.items():
            self.assertEqual(path.read_bytes(), contents)
        self.assertEqual(self.launch().returncode, 0)
        for path, contents in originals.items():
            self.assertEqual(path.read_bytes(), contents)
        fresh = self.root / "fresh workstation"
        self.env["HOME"] = str(fresh)
        result = self.launch()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads((fresh / ".config/comfyui-desktop-2/settings.json").read_text())
                         ["autoLaunchOnStartup"], chosen["id"])
        seeded, = json.loads((fresh / ".local/share/comfyui-desktop-2/installations.json").read_text())
        self.assertEqual(seeded["installPath"], str(target))
        expected[-1] = f"sqlite:///{fresh}/.local/share/comfyui-studio/comfyui.db"
        self.assertEqual(shlex.split(seeded["launchArgs"]), expected)

    def test_set_default_uses_target_manifest_without_creating_local_profile(self):
        target = self.second_install()
        manifest = {"comfyui_ref": "v0.44.0", "version": "v0.44.0-env3", "id": "linux-nvidia",
                    "python_version": "3.13.11"}
        (target / "manifest.json").write_text(json.dumps(manifest))
        result = self.launch("--set-default", str(target))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(self.marker.exists())
        self.assertFalse(self.home.exists())
        selected, = json.loads((self.templates / "installations.json").read_text())
        self.assertEqual([selected[key] for key in ("version", "releaseTag", "variant", "pythonVersion")],
                         ["v0.44.0", "v0.44.0-env3", "linux-nvidia", "3.13.11"])
        self.assertEqual(selected["installPath"], str(target))
        self.assertTrue(selected["id"])

    def test_invalid_selection_preserves_shared_default(self):
        target = self.second_install()
        (target / "manifest.json").write_text("{}")
        outside = self.root / "outside"
        shutil.copytree(self.install, outside)
        escape = self.studio / "escape"
        escape.symlink_to(outside, target_is_directory=True)
        original = (self.templates / "installations.json").read_bytes()
        cases = [("relative", target.name), ("outside", str(outside)), ("symlink escape", str(escape)),
                 ("missing", str(self.studio / "missing")), ("incomplete", str(target / "ComfyUI")),
                 ("manifest metadata", str(target)), ("registry metadata", str(target))]
        for name, path in cases:
            with self.subTest(case=name):
                if name == "registry metadata":
                    self.catalog.parent.mkdir(parents=True)
                    self.catalog.write_text(json.dumps([{"sourceId": "standalone", "installPath": str(target)}]))
                result = self.launch("--set-default", path)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual((self.templates / "installations.json").read_bytes(), original)
                self.assertFalse(self.marker.exists())
                self.assertFalse(self.settings.exists())

    def test_initialization_lock_is_released_while_app_remains_running(self):
        app = subprocess.Popen([*self.command, "--", "--hold"], env=self.env, text=True,
                               stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        try:
            self.assertTrue(select.select([app.stdout], [], [], 5)[0], "AppImage did not start")
            self.assertEqual(json.loads(app.stdout.readline()), ["--no-sandbox", "--hold"])
            self.assertIsNone(app.poll())
            with (self.home / ".local/state/comfyui-studio/setup.lock").open("a") as lock:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        finally:
            try:
                app.communicate("\n", timeout=5)
            except subprocess.TimeoutExpired:
                app.kill()
                app.communicate()

if __name__ == "__main__":
    unittest.main()
