"""Exercise local Comfy first-launch setup with a real executable; no AWS or GUI."""
import fcntl
import json
import os
from pathlib import Path
import select
import shlex
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
sys.argv = [script, *arguments]
helper.main()
"""

class ComfyFirstLaunch(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.home, self.studio = self.root / "home with spaces", self.root / "studio"
        self.install = self.studio / "apps/comfyui/main/Studio ComfyUI (1)"
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
        self.image.write_text(f"#!{sys.executable}\nimport json, sys\n"
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
        result = self.launch("--example", "argument with spaces")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), ["--no-sandbox", "--example", "argument with spaces"])
        self.assertEqual(json.loads(self.settings.read_text()),
                         json.loads((self.templates / "settings.json").read_text()))
        entry, = json.loads(self.catalog.read_text())
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

    def test_initialization_lock_is_released_while_app_remains_running(self):
        app = subprocess.Popen([*self.command, "--hold"], env=self.env, text=True,
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
