"""Run the Studio shell and wrappers against temporary homes and app payloads."""
import json
from pathlib import Path
import re
import shlex
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts/studio-shell"
CAPTURE = """import json, os, sys
keys = ('PATH', 'STUDIO_ENV', 'STUDIO_ROOT', 'STUDIO_PROJECTS', 'OLLAMA_MODELS',
        'SHELL_PREFERENCE', 'OBSERVED_PROMPT', 'SHELL_FLAGS', 'REZ_CONFIG_FILE',
        'XDG_CONFIG_HOME', 'XDG_DATA_HOME', 'XDG_STATE_HOME', 'XDG_CACHE_HOME')
print(json.dumps({'args': sys.argv[1:], 'cwd': os.getcwd(),
                  'env': {key: os.environ.get(key) for key in keys}}))
"""


class StudioEnvironmentTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="studio-env-")
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.shared, self.opt = self.root / "shared", self.root / "opt"
        self.bin, self.localbin = self.shared / "bin", self.root / "local-bin"
        self.home, self.cwd = self.root / "home", self.root / "project with spaces"
        for path in (self.bin, self.localbin, self.home, self.cwd):
            path.mkdir(parents=True)
        self.rez = self.localbin / "studio-rez-env"
        self.scripts = self.root / "repo/scripts/studio-shell"
        for source in SCRIPTS.iterdir():
            self.write(self.scripts / source.name, self.relocate(source.read_text()))
            if source.suffix != ".json":
                (self.bin / source.name).symlink_to(self.scripts / source.name)
        self.launch = self.scripts / "studio"
        self.write(self.root / "repo/studio", (ROOT / "studio").read_text())
        self.bashrc = "export SHELL_PREFERENCE=loaded\nPS1='personal> '\n"
        self.write(self.home / ".bashrc", self.bashrc)
        self.capture = self.payload(self.root / "capture")
        self.env = {"HOME": str(self.home), "PATH": "/usr/bin:/bin", "TERM": "dumb"}
        self.env.update({f"XDG_{kind}_HOME": str(self.home / kind.lower())
                         for kind in ("CONFIG", "DATA", "STATE", "CACHE")})

    def write(self, path, text):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
        path.chmod(0o755)
        return path

    def relocate(self, text):
        paths = [("/usr/local/bin/studio-rez-env", self.rez),
                 ("/usr/local/bin/studio", self.localbin / "studio"),
                 ("/opt/studio", self.opt), ("/studio", self.shared)]
        for index, (source, _) in enumerate(paths):
            text = re.sub(r"(?<![\w/@.-])" + re.escape(source) + r"(?![\w.-])",
                          f"@PATH{index}@", text)
        for index, (_, target) in enumerate(paths):
            text = text.replace(f"@PATH{index}@", str(target))
        return text

    def payload(self, path):
        return self.write(path, f"#!{sys.executable}\n" + CAPTURE)

    def run_command(self, argv, text=None):
        return subprocess.run([str(arg) for arg in argv], input=text, env=self.env,
                              cwd=self.cwd, capture_output=True, text=True, timeout=15)

    def rows(self, result):
        return [json.loads(line) for line in result.stdout.splitlines()]

    def test_interactive_shell_preferences_cwd_exit_and_parent_isolation(self):
        effects = self.root / "service-calls"
        for name in ("systemctl", "ollama", "opencode"):
            self.write(self.bin / name, f"#!/bin/sh\nprintf called >> {effects}\nexit 99\n")
        commands = ('export OBSERVED_PROMPT="$PS1" SHELL_FLAGS="$-"\n'
                    f"{shlex.quote(str(self.capture))}\nexit 7\n")
        parent = '"$1" env; status=$?; "$2"; exit "$status"'
        result = self.run_command(["/bin/bash", "--noprofile", "--norc", "-c", parent,
                                   "parent", self.launch, self.capture], commands)
        self.assertEqual(result.returncode, 7, result.stderr)
        child, outer = self.rows(result)
        self.assertEqual(child["cwd"], str(self.cwd))
        self.assertIn("i", child["env"]["SHELL_FLAGS"])
        self.assertEqual(child["env"]["OBSERVED_PROMPT"], "(studio) personal> ")
        self.assertEqual(child["env"]["SHELL_PREFERENCE"], "loaded")
        for key, value in {"STUDIO_ENV": "1", "STUDIO_ROOT": self.shared,
                           "STUDIO_PROJECTS": self.shared / "projects",
                           "OLLAMA_MODELS": self.shared / "models/ollama"}.items():
            self.assertEqual(child["env"][key], str(value))
            self.assertIsNone(outer["env"][key])
        self.assertEqual(outer["env"]["PATH"], self.env["PATH"])
        self.assertEqual(outer["cwd"], str(self.cwd))
        self.assertIsNone(child["env"]["REZ_CONFIG_FILE"])
        self.assertEqual(self.home.joinpath(".bashrc").read_text(), self.bashrc)
        self.assertFalse(effects.exists())

    def test_nested_shell_sources_optional_rez_without_duplicate_paths(self):
        rezbin = self.opt / "rez/3.4.0/bin/rez"
        rezbin.mkdir(parents=True)
        self.write(self.opt / "studio-rez/env/studio-prod.sh",
                   "export REZ_CONFIG_FILE=/verified/production.py\n")
        capture, launch = map(shlex.quote, map(str, (self.capture, self.launch)))
        commands = f"{launch} env <<'INNER'\n{capture}\nexit\nINNER\n{capture}\nexit\n"
        result = self.run_command([self.launch, "env"], commands)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(self.rows(result)), 2)
        for row in self.rows(result):
            self.assertEqual(row["env"]["REZ_CONFIG_FILE"], "/verified/production.py")
            for path in (self.bin, rezbin):
                self.assertEqual(row["env"]["PATH"].split(":").count(str(path)), 1)

    def test_dispatcher_usage_and_missing_shared_directory(self):
        result = self.run_command([sys.executable, self.root / "repo/studio", "env", "--help"])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Usage: studio env", result.stdout)
        for arguments in ([], ["env", "extra"], ["workstation"]):
            result = self.run_command([self.launch, *arguments])
            self.assertEqual(result.returncode, 2)
        self.bin.rename(self.shared / "unavailable-bin")
        result = self.run_command([self.launch, "env"])
        self.assertEqual(result.returncode, 1)
        self.assertIn("unavailable", result.stderr)

    def test_native_payload_arguments_and_comfy_configuration_delegation(self):
        cases = [("CODE", "apps/vscode/current/bin/code", ["--wait", "file with spaces.py"],
                  ["--extensions-dir", str(self.shared / "apps/vscode/extensions")]),
                 ("BLENDER", "apps/blender/current/blender", ["--background", "scene file.blend"], []),
                 ("COMFY", "apps/comfy-desktop/launcher/studio-comfy", ["--update"], []),
                 ("COMFY", "apps/comfy-desktop/launcher/studio-comfy",
                  ["--set-default", "/studio/apps/comfyui/Studio ComfyUI (1)"], [])]
        for name, target, arguments, prefix in cases:
            with self.subTest(wrapper=name, arguments=arguments):
                native = self.payload(self.shared / target)
                original = native.read_bytes()
                result = self.run_command([self.bin / name, *arguments])
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(self.rows(result)[0]["args"], prefix + arguments)
                self.assertEqual(native.read_bytes(), original)
        self.assertTrue((self.shared / "apps/vscode/extensions").is_dir())

    def test_ollama_uses_production_resolver_and_shared_models(self):
        self.payload(self.rez)
        self.env["OLLAMA_MODELS"] = "/unwanted/local/models"
        result = self.run_command([self.bin / "OLLAMA", "run", "model:tag", "two words"])
        self.assertEqual(result.returncode, 0, result.stderr)
        row, = self.rows(result)
        self.assertEqual(row["args"], ["ollama", "--", "ollama", "run", "model:tag", "two words"])
        self.assertEqual(row["env"]["OLLAMA_MODELS"], str(self.shared / "models/ollama"))
        self.assertTrue((self.shared / "models/ollama").is_dir())

    def test_opencode_missing_shared_settings_then_first_launch_and_retry(self):
        self.payload(self.shared / "apps/opencode/current/opencode")
        config = Path(self.env["XDG_CONFIG_HOME"]) / "opencode/opencode.json"
        result = self.run_command([self.bin / "OPENCODE"])
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(config.parent.exists())
        shared = self.write(self.shared / "apps/opencode/config/opencode.json", '{"update":"disable"}\n')
        for _ in range(2):
            result = self.run_command([self.bin / "OPENCODE", "project with spaces"])
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(config.readlink(), shared)
            self.assertEqual(self.rows(result)[0]["args"], ["project with spaces"])

    def test_opencode_preserves_existing_json_jsonc_and_local_state(self):
        self.payload(self.shared / "apps/opencode/current/opencode")
        folder = Path(self.env["XDG_CONFIG_HOME"]) / "opencode"
        state = self.write(Path(self.env["XDG_DATA_HOME"]) / "opencode/opencode.db", "local database")
        service = self.write(folder / "service.json", '{"port":12345}')
        for filename, symlink in (("opencode.json", False), ("opencode.jsonc", False),
                                  ("opencode.json", True)):
            with self.subTest(filename=filename, symlink=symlink):
                config = folder / filename
                if symlink:
                    config.symlink_to(self.root / "missing-user-config")
                else:
                    self.write(config, "// existing preferences\n{}\n")
                before = config.readlink() if symlink else config.read_bytes()
                result = self.run_command([self.bin / "OPENCODE", "--version"])
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(config.readlink() if symlink else config.read_bytes(), before)
                row, = self.rows(result)
                for key in ("XDG_DATA_HOME", "XDG_STATE_HOME", "XDG_CACHE_HOME"):
                    self.assertEqual(row["env"][key], self.env[key])
                self.assertEqual(state.read_text(), "local database")
                self.assertEqual(service.read_text(), '{"port":12345}')
                self.assertEqual(set(folder.iterdir()), {config, service})
                config.unlink()

    def test_installer_link_retries_and_preserves_collisions(self):
        source = (ROOT / "scripts/setup_workstation_software.sh").read_text()
        block = self.relocate(source[source.index("# The shell and app wrappers"):
                                     source.index("if ! command -v menulibre")])
        command, target = self.localbin / "studio", self.bin / "studio"
        for _ in range(2):
            result = self.run_command(["/bin/bash", "-eu", "-c", block])
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(command.readlink(), target)
        target.unlink()
        self.assertEqual(self.run_command(["/bin/bash", "-eu", "-c", block]).returncode, 0)
        for symlink in (False, True):
            command.unlink()
            if symlink:
                command.symlink_to(self.root / "other-command")
            else:
                self.write(command, "existing command")
            before = command.readlink() if symlink else command.read_bytes()
            result = self.run_command(["/bin/bash", "-eu", "-c", block])
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("Conflicting studio command", result.stdout)
            self.assertEqual(command.readlink() if symlink else command.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
