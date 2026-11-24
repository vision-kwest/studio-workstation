"""Run the Studio shell and wrappers against temporary homes and app payloads."""
import base64
import json
from pathlib import Path
import re
import shlex
import subprocess
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts/studio-shell"
CAPTURE = """import json, os, sys
keys = ('PATH', 'STUDIO_ENV', 'STUDIO_ROOT', 'STUDIO_PROJECTS', 'OLLAMA_MODELS',
        'SHELL_PREFERENCE', 'OBSERVED_PROMPT', 'SHELL_FLAGS', 'REZ_CONFIG_FILE',
        'XDG_CONFIG_HOME', 'XDG_DATA_HOME', 'XDG_STATE_HOME', 'XDG_CACHE_HOME', 'DOTNET_ROOT')
print(json.dumps({'args': sys.argv[1:], 'cwd': os.getcwd(),
                  'env': {key: os.environ.get(key) for key in keys}}))
"""
UNITY_CLI = """import json, os, sys
with open(os.environ['UNITY_CLI_CALLS'], 'a') as journal:
    journal.write(json.dumps(sys.argv[1:]) + '\\n')
operation = ' '.join(sys.argv[4:6])
if operation == os.environ.get('UNITY_CLI_FAILURE'):
    print(os.environ['UNITY_CLI_RESPONSE'])
    sys.exit(int(os.environ['UNITY_CLI_STATUS']))
print(json.dumps({'success': True, 'errors': [], 'data': {'partial': False, 'nextPage': None,
    'items': [{'path': p} for p in json.loads(os.environ.get('UNITY_CLI_REGISTERED', '[]'))]}}))
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
                 ("/opt/resolve", self.opt / "resolve"),
                 ("/var/lib/davinci-resolve", self.root / "var/lib/davinci-resolve"),
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
                  ["--set-default", "/studio/apps/comfyui/main/StudioComfyUI"], [])]
        for name, target, arguments, prefix in cases:
            with self.subTest(wrapper=name, arguments=arguments):
                native = self.payload(self.shared / target)
                original = native.read_bytes()
                result = self.run_command([self.bin / name, *arguments])
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(self.rows(result)[0]["args"], prefix + arguments)
                self.assertEqual(native.read_bytes(), original)
        self.assertTrue((self.shared / "apps/vscode/extensions").is_dir())

    def resolve_fixture(self):
        payload = self.shared / "apps/davinci-resolve/21.2-free"
        self.payload(payload / "bin/resolve")
        self.write(payload / ".shared-payload-ready", "ready\n")
        self.write(payload / "share/default-config.dat", "Default.Sentinel = preserve\n"
                   "Site.1.FS.Count = 1\nSite.1.FS.1.Type = IOFileSys\nSite.1.FS.1.Root = HOMEDIR\n")
        self.resolve_prepare = self.write(payload.parent / "launcher/prepare-user.py",
            self.relocate((ROOT / "scripts/resolve/prepare-user.py").read_text()))
        (payload.parent / "current").symlink_to(payload)
        self.opt.mkdir(exist_ok=True)
        (self.opt / "resolve").symlink_to(payload)
        local = self.root / "var/lib/davinci-resolve/21.2-free"
        for name in (".license", "LUT", "easyDCP", "Extras", "Fairlight", "Immersive", "Apple Immersive", "logs"):
            (local / name).mkdir(parents=True)
            (payload / name).symlink_to(local / name)
        data = self.home / ".local/share/DaVinciResolve"
        for name in ("configs", "Resolve Project Library", "logs"):
            (data / name).mkdir(parents=True)
        self.write(self.localbin / "findmnt", '#!/bin/sh\ncase "$*" in *network-storage*) echo nfs4;; *) echo ext4;; esac\n')
        self.env["PATH"] = str(self.localbin) + ":" + self.env["PATH"]
        return payload, local, data, self.home / ".cache/davinci-resolve"

    def test_resolve_and_legacy_launch_preserve_arguments_exit_and_user_data(self):
        payload, local, data, cache = self.resolve_fixture()
        self.write(payload / "bin/resolve", f"#!{sys.executable}\n" + CAPTURE + "sys.exit(7)\n")
        originals = [self.write(data / "configs/config.dat", "existing preferences"),
                     self.write(data / "Resolve Project Library/project.db", "existing project"),
                     self.write(local / "LUT/custom.cube", "custom LUT")]
        arguments = ["clip with spaces.mov", "", "$(echo literal)", "single'quote", "line one\nline two"]
        for name in ("RESOLVE", "studio-resolve"):
            result = self.run_command([self.bin / name, *arguments])
            self.assertEqual(result.returncode, 7, result.stderr)
            self.assertEqual(self.rows(result)[0]["args"], arguments)
            self.assertEqual(self.rows(result)[0]["cwd"], str(payload))
            self.assertTrue((cache / "CacheClip").is_dir())
            self.assertEqual([p.read_text() for p in originals], ["existing preferences", "existing project", "custom LUT"])
        self.assertEqual(ET.parse(data / "configs/config.user.xml").findtext("AutoSaveDuration"), "10")

    def test_resolve_seeds_native_preferences_and_repairs_only_missing_files(self):
        payload, local, data, cache = self.resolve_fixture()
        configs = data / "configs"
        configs.rmdir()
        result = self.run_command([self.bin / "RESOLVE", "clip with spaces.mov"])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.rows(result)[0]["args"], ["clip with spaces.mov"])
        system, user = configs / "config.dat", configs / "config.user.xml"
        values = dict(line.split(" = ", 1) for line in system.read_text().splitlines() if " = " in line)
        self.assertEqual(values["Default.Sentinel"], "preserve")
        self.assertEqual(values["Site.1.FS.Count"], "4")
        self.assertEqual([values[f"Site.1.FS.{i}.Root"] for i in range(1, 5)],
                         [str(cache), str(self.home / "Videos"), str(self.shared), "ResolveVirtual"])
        prefs = ET.parse(user).getroot()
        self.assertEqual(prefs.tag, "SM_UserPrefs")
        self.assertEqual({e.tag: e.text for e in prefs}, {
            "AutoSaveMode": "1", "AutoSaveDuration": "10", "IsProjectLiveSaveEnabled": "true",
            "IsTimelineBackupsEnabled": "true", "ProjectBackupLocation": str(self.shared / "projects/resolve/backups")})
        self.assertTrue((self.shared / "projects/resolve/backups").is_dir())
        for missing, retained in ((system, user), (user, system)):
            with self.subTest(missing=missing.name):
                self.write(retained, "existing customized settings\n")
                missing.unlink()
                result = self.run_command([self.bin / "RESOLVE"])
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertTrue(missing.is_file())
                self.assertEqual(retained.read_text(), "existing customized settings\n")
        before = {p: p.read_bytes() for p in (system, user)}
        (payload / "share/default-config.dat").unlink()
        self.assertEqual(self.run_command([self.bin / "RESOLVE"]).returncode, 0)
        self.assertEqual({p: p.read_bytes() for p in before}, before)

    def test_resolve_conflicting_preference_paths_fail_before_seeding(self):
        _, _, data, _ = self.resolve_fixture()
        configs = data / "configs"
        for name in ("config.dat", "config.user.xml"):
            for kind in ("directory", "dangling-link"):
                with self.subTest(name=name, kind=kind):
                    target = configs / name
                    target.mkdir() if kind == "directory" else target.symlink_to(self.root / "missing")
                    result = self.run_command([self.bin / "RESOLVE"])
                    self.assertNotEqual(result.returncode, 0)
                    self.assertEqual(result.stdout, "")
                    self.assertTrue(target.is_dir() if kind == "directory" else target.is_symlink())
                    self.assertFalse((configs / ("config.user.xml" if name == "config.dat" else "config.dat")).exists())
                    target.rmdir() if kind == "directory" else target.unlink()

    def test_resolve_running_native_process_blocks_seed_and_concurrent_retries_are_valid(self):
        import shutil
        payload, _, data, _ = self.resolve_fixture()
        binary = payload / "bin/resolve"
        shutil.copyfile("/bin/sleep", binary)
        with subprocess.Popen([str(binary), "30"], env=self.env) as running:
            try:
                result = self.run_command([sys.executable, self.resolve_prepare])
                self.assertNotEqual(result.returncode, 0)
                self.assertFalse((data / "configs/config.dat").exists())
                self.assertFalse((data / "configs/config.user.xml").exists())
            finally:
                running.terminate()
        children = [subprocess.Popen([sys.executable, str(self.resolve_prepare)], env=self.env,
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) for _ in range(2)]
        for child in children:
            _, error = child.communicate(timeout=15)
            self.assertEqual(child.returncode, 0, error)
        self.assertEqual(ET.parse(data / "configs/config.user.xml").findtext("AutoSaveDuration"), "10")
        self.assertIn("Site.1.FS.Count = 4", (data / "configs/config.dat").read_text())

    def test_resolve_missing_prerequisites_fail_before_launch_or_cache_creation(self):
        payload, local, data, cache = self.resolve_fixture()
        targets = [payload / ".shared-payload-ready", payload / "bin/resolve", self.opt / "resolve", *local.iterdir()]
        for path in targets:
            with self.subTest(path=path.name):
                backup = path.with_name(path.name + ".saved")
                path.rename(backup)
                result = self.run_command([self.bin / "RESOLVE"])
                backup.rename(path)
                self.assertNotEqual(result.returncode, 0)
                self.assertTrue(result.stderr)
                self.assertEqual(result.stdout, "")
                self.assertFalse(cache.exists())
                if path.parent == local:
                    self.assertIn("setup-local.sh --user", result.stderr)
        configs = data / "configs"
        configs.rmdir()
        for dangling in (False, True):
            configs.symlink_to(self.root / "missing-configs") if dangling else configs.write_text("keep this file")
            result = self.run_command([self.bin / "RESOLVE"])
            self.assertNotEqual(result.returncode, 0)
            self.assertFalse(cache.exists())
            self.assertTrue(configs.is_symlink() if dangling else configs.read_text() == "keep this file")
            configs.unlink()

    def test_resolve_rejects_resolved_network_children_without_modifying_them(self):
        payload, local, data, cache = self.resolve_fixture()
        network = self.shared / "network-storage"
        network.mkdir()
        sentinel = self.write(network / "keep", "shared data")
        for path in (local / "Apple Immersive", data / "configs", data / "Resolve Project Library", cache / "CacheClip"):
            with self.subTest(path=path.name):
                backup = path.with_name(path.name + ".saved")
                existed = path.exists()
                if existed:
                    path.rename(backup)
                path.parent.mkdir(parents=True, exist_ok=True)
                path.symlink_to(network)
                result = self.run_command([self.bin / "RESOLVE"])
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(result.stdout, "")
                self.assertIn("nfs4", result.stderr)
                self.assertIn(str(path), result.stderr)
                self.assertEqual(path.readlink(), network)
                self.assertEqual(sentinel.read_text(), "shared data")
                path.unlink()
                if existed:
                    backup.rename(path)

    def test_ollama_uses_production_resolver_and_shared_models(self):
        resolved_bin = self.root / "resolved-bin"
        # Rez 3.4.0 runs a shell command after resolution; direct argv exec
        # misses the expansion boundary. Exercise its --command semantics.
        self.write(self.rez, '#!/bin/sh\n'
                   '[ "$#" = 5 ] && [ "$1" = ollama ] || exit 90\n'
                   '[ "$2" = --shell ] && [ "$3" = bash ] || exit 90\n'
                   '[ "$4" = --command ] || exit 90\n'
                   f'export PATH={shlex.quote(str(resolved_bin))}\n'
                   'exec /bin/bash --noprofile --norc -c "$5"\n')
        self.write(resolved_bin / "ollama", f"#!{sys.executable}\n" + CAPTURE +
                   "import shutil\n"
                   "print(json.dumps({'opencode': shutil.which('opencode')}))\n")
        opencode = self.shared / "apps/opencode/current/opencode"
        self.env["OLLAMA_MODELS"] = "/unwanted/local/models"
        cases = [[], ["serve"], ["-h"], ["--help"], ["list"],
                 ["run", "model:tag", "two words", "$(echo literal)", "",
                  "$PATH", "`echo literal`", "single'quote", 'double"quote',
                  "line one\nline two"]]
        for installed in (False, True):
            if installed:
                self.payload(opencode)
            for arguments in cases:
                with self.subTest(opencode_installed=installed, arguments=arguments):
                    result = self.run_command([self.bin / "OLLAMA", *arguments])
                    self.assertEqual(result.returncode, 0, result.stderr)
                    row, resolution = self.rows(result)
                    self.assertEqual(row["args"], arguments)
                    self.assertEqual(row["env"]["OLLAMA_MODELS"], str(self.shared / "models/ollama"))
                    self.assertEqual(resolution["opencode"], str(opencode) if installed else None)
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

    def unity_payloads(self):
        self.write(self.localbin / "pgrep", '#!/bin/sh\nexit "${UNITY_PGREP_STATUS:-1}"\n')
        self.env["PATH"] = str(self.localbin) + ":/usr/bin:/bin"
        android = "unity/current/Editor/Data/PlaybackEngines/AndroidPlayer/"
        paths = ["unity-hub/current/UnityHub.AppImage", "unity/current/Editor/Unity",
                 "vscode/current/bin/code", "dotnet/dotnet"] + [android + name for name in
                 ("SDK/platform-tools/adb", "NDK/ndk-build", "OpenJDK/bin/java")]
        required = [self.payload(self.shared / "apps" / path) for path in paths]
        self.cli_calls = self.root / "unity-cli-calls.jsonl"
        self.env["UNITY_CLI_CALLS"] = str(self.cli_calls)
        required.append(self.write(self.shared / "apps/unity-cli/current/unity",
                                   f"#!{sys.executable}\n" + UNITY_CLI))
        required.append(self.write(self.shared / "apps" / android / "Tools/gradle/lib/gradle-launcher-test.jar", "Gradle"))
        sdk = self.shared / "apps/dotnet/sdk/10.0.100"
        sdk.mkdir(parents=True)
        (self.shared / "apps/vscode/extensions").mkdir()
        self.unity_prefs = self.home / ".local/share/unity3d/prefs"
        self.unity_extensions = self.home / ".vscode/extensions"
        return required + [sdk, self.shared / "apps/vscode/extensions"]

    def test_unity_library_migration_repeat_and_missing_local_cache(self):
        self.unity_payloads()
        project = self.shared / "projects/unity/Project with spaces"
        self.write(project / "ProjectSettings/ProjectVersion.txt", "6000.3.29f1")
        self.write(project / "Library/SourceAssetDB", "original EFS cache bytes")
        temp = self.write(project / "Temp/keep", "native project temp")
        library, backup = project / "Library", project / "Library.efs-backup"
        for attempt in range(3):
            result = self.run_command([self.bin / "UNITY"])
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue(library.is_symlink())
            cache = library.resolve()
            self.assertTrue(cache.is_relative_to(self.home / ".cache/studio-unity"))
            self.assertTrue(cache.is_dir())
            self.assertEqual((backup / "SourceAssetDB").read_bytes(), b"original EFS cache bytes")
            self.assertEqual(temp.read_text(), "native project temp")
            self.assertFalse(temp.parent.is_symlink())
            if attempt == 0:
                self.write(cache / "warm", "local cache survives repeat")
            elif attempt == 1:
                self.assertEqual((cache / "warm").read_text(), "local cache survives repeat")
                (cache / "warm").unlink()
                cache.rmdir()  # Same shared link, but no local cache on a fresh workstation.

    def test_unity_library_refuses_active_project_and_preserves_conflicts(self):
        import shutil
        self.unity_payloads()
        self.write(self.unity_prefs, '<unity_prefs version_major="1" version_minor="1">'
                   '<pref name="kScriptsDefaultApp" type="string" /></unity_prefs>')
        for kind in ("active", "probe-error", "lock", "foreign-link", "backup", "file"):
            with self.subTest(conflict=kind):
                project = self.shared / "projects/unity/Test"
                self.write(project / "ProjectSettings/ProjectVersion.txt", "6000.3.29f1")
                library = project / "Library"
                if kind == "foreign-link":
                    library.symlink_to(self.root / "foreign-cache")
                else:
                    self.write(library if kind == "file" else library / "keep", "preserved")
                if kind == "backup":
                    self.write(project / "Library.efs-backup/keep", "previous backup")
                if kind == "lock":
                    self.write(project / "Temp/UnityLockfile", "active on another workstation")
                self.env["UNITY_PGREP_STATUS"] = {"active": "0", "probe-error": "2"}.get(kind, "1")
                protected = library / "keep" if library.is_dir() else library
                before = library.readlink() if library.is_symlink() else protected.read_bytes()
                result = self.run_command([self.bin / "UNITY"])
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(result.stdout, "")
                self.assertFalse(self.cli_calls.exists())
                self.assertEqual(library.readlink() if library.is_symlink() else protected.read_bytes(), before)
                if kind == "backup":
                    self.assertEqual((project / "Library.efs-backup/keep").read_text(), "previous backup")
                shutil.rmtree(project)

    def test_unity_registers_resolved_editor_and_only_missing_immediate_projects(self):
        self.unity_payloads()
        editor = self.shared / "apps/unity/current/Editor/Unity"
        resolved = self.payload(self.root / "editor with spaces/Unity")
        editor.unlink()
        editor.symlink_to(resolved)
        project_root = self.shared / "projects/unity"
        projects = [project_root / name for name in ("Alpha", "Quest with spaces")]
        for project in projects + [project_root / "nested/Ignore"]:
            self.write(project / "ProjectSettings/ProjectVersion.txt", "6000.3.29f1")
        (project_root / "Invalid/ProjectSettings/ProjectVersion.txt").mkdir(parents=True)
        registered_alias = self.root / "registered project alias"
        registered_alias.symlink_to(projects[0], target_is_directory=True)
        flags = ["--non-interactive", "--format", "json"]
        expected = [flags + ["projects"], flags + ["editors", "add", str(resolved)],
                    flags + ["projects", "add", *map(str, projects)]]
        for attempt in range(2):
            result = self.run_command([self.bin / "UNITY"])
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(len(self.rows(result)), 1)
            self.assertEqual([json.loads(line) for line in self.cli_calls.read_text().splitlines()], expected)
            self.env.update(UNITY_CLI_FAILURE="editors add", UNITY_CLI_STATUS="6",
                            UNITY_CLI_RESPONSE='{"errors":[{"code":"EDITORS_ADD_ALREADY_IN_LIST"}]}',
                            UNITY_CLI_REGISTERED=json.dumps([str(registered_alias), str(projects[1])]))
            expected += expected[:2]

    def test_unity_registration_errors_prevent_launch(self):
        self.unity_payloads()
        self.write(self.shared / "projects/unity/Test/ProjectSettings/ProjectVersion.txt", "6000.3.29f1")
        duplicate = {"code": "EDITORS_ADD_ALREADY_IN_LIST"}
        cases = [("projects", 0, "not JSON"), ("projects", 1, '{"success":false}'),
                 ("projects", 0, '{"success":true,"data":{"items":[],"partial":true}}'),
                 ("projects", 0, '{"success":true,"data":{"items":[],"nextPage":1}}'),
                 ("editors add", 6, "not JSON"), ("editors add", 6, "[]"),
                 ("editors add", 6, '{"errors":[]}'),
                 ("editors add", 6, json.dumps({"errors": [duplicate, {"code": "OTHER"}]})),
                 ("editors add", 1, json.dumps({"errors": [duplicate]})),
                 ("projects add", 6, '{"errors":[{"code":"COMMAND_FAILED"}]}')]
        for operation, status, response in cases:
            with self.subTest(operation=operation, status=status, response=response):
                self.env.update(UNITY_CLI_FAILURE=operation, UNITY_CLI_STATUS=str(status),
                                UNITY_CLI_RESPONSE=response)
                result = self.run_command([self.bin / "UNITY"])
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(result.stdout, "", "Hub or Editor must not launch after registration failure")

    def test_unity_direct_editor_and_hub_preserve_literal_arguments(self):
        self.unity_payloads()
        editor = self.shared / "apps/unity/current/Editor/Unity"
        self.write(editor, f"#!{sys.executable}\n" + CAPTURE.replace(
            "'args': sys.argv[1:]", "'executable': sys.argv[0], 'args': sys.argv[1:]"))
        arguments = ["--projectPath", "project with spaces", "$(echo literal)", "--editor", ""]
        for prefix in ([], ["--editor"]):
            result = self.run_command([self.bin / "UNITY", *prefix, *arguments])
            self.assertEqual(result.returncode, 0, result.stderr)
            row, = self.rows(result)
            self.assertEqual(row["args"], arguments)
            self.assertEqual(row.get("executable"), str(editor) if prefix else None)
            self.assertEqual(row["env"]["DOTNET_ROOT"], str(self.shared / "apps/dotnet"))

    def test_unity_editor_desktop_entry(self):
        import shutil
        entry = ROOT / "share/applications/unity-editor.desktop"
        fields = dict(line.split("=", 1) for line in entry.read_text().splitlines() if "=" in line)
        for key, value in {"Type": "Application", "Exec": "/studio/bin/UNITY --editor",
                           "TryExec": "/studio/bin/UNITY", "Terminal": "false",
                           "Icon": "/studio/apps/unity/current/Editor/Data/Resources/LargeUnityIcon.png"}.items():
            self.assertEqual(fields[key], value)
        if shutil.which("desktop-file-validate"):
            subprocess.run(["desktop-file-validate", str(entry)], check=True)

    def test_unity_first_launch_retry_environment_and_arguments(self):
        self.unity_payloads()
        arguments = ["project with spaces", "$(echo literal)", ""]
        for attempt in range(2):
            result = self.run_command([self.bin / "UNITY", *arguments])
            self.assertEqual(result.returncode, 0, result.stderr)
            row, = self.rows(result)
            self.assertEqual(row["args"], arguments)
            self.assertEqual(row["env"]["DOTNET_ROOT"], str(self.shared / "apps/dotnet"))
            self.assertEqual(row["env"]["PATH"], row["env"]["DOTNET_ROOT"] + ":" + self.env["PATH"])
            self.assertEqual(self.unity_extensions.resolve(), self.shared / "apps/vscode/extensions")
            if attempt == 0:
                original = self.unity_prefs.read_bytes()
            self.assertEqual(self.unity_prefs.read_bytes(), original)
        prefs = ET.fromstring(original)
        self.assertEqual((prefs.tag, prefs.attrib), ("unity_prefs", {"version_major": "1", "version_minor": "1"}))
        editor = prefs.find("pref[@name='kScriptsDefaultApp']")
        self.assertEqual(editor.get("type"), "string")
        self.assertEqual(base64.b64decode(editor.text).decode(), str(self.shared / "apps/vscode/current/bin/code"))

    def test_unity_missing_prerequisites_do_not_change_local_configuration(self):
        for required in self.unity_payloads():
            with self.subTest(missing=required):
                hidden = required.with_name("unavailable-" + required.name)
                required.rename(hidden)
                result = self.run_command([self.bin / "UNITY"])
                self.assertNotEqual(result.returncode, 0)
                self.assertFalse(self.unity_prefs.parent.exists())
                self.assertFalse(self.unity_extensions.parent.exists())
                hidden.rename(required)

    def test_unity_preserves_conflicting_extensions_and_invalid_preferences(self):
        self.unity_payloads()
        for kind in ("file", "directory", "link", "xml", "prefs-link"):
            with self.subTest(conflict=kind):
                target = self.unity_prefs if kind in ("xml", "prefs-link") else self.unity_extensions
                target.parent.mkdir(parents=True, exist_ok=True)
                if "link" in kind:
                    target.symlink_to(self.root / "missing-user-target")
                else:
                    self.write(target / "keep" if kind == "directory" else target, "existing data")
                content = target / "keep" if target.is_dir() else target
                before = target.readlink() if target.is_symlink() else content.read_bytes()
                result = self.run_command([self.bin / "UNITY"])
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(target.readlink() if target.is_symlink() else content.read_bytes(), before)
                if target.is_dir():
                    (target / "keep").unlink()
                    target.rmdir()
                else:
                    target.unlink()

    def test_unity_seeds_only_missing_preference_and_preserves_custom_or_empty_choice(self):
        self.unity_payloads()
        for value in (None, "", base64.b64encode(b"/custom/editor").decode()):
            with self.subTest(editor=value):
                entry = "" if value is None else f'<pref name="kScriptsDefaultApp" type="string">{value}</pref>'
                original = '<unity_prefs version_major="1" version_minor="1"><pref name="theme" type="int">7</pref>' + entry + '</unity_prefs>'
                self.write(self.unity_prefs, original)
                result = self.run_command([self.bin / "UNITY"])
                self.assertEqual(result.returncode, 0, result.stderr)
                prefs = ET.parse(self.unity_prefs).getroot()
                self.assertEqual(prefs.find("pref[@name='theme']").text, "7")
                self.assertIsNotNone(prefs.find("pref[@name='kScriptsDefaultApp']"))
                if value is not None:
                    self.assertEqual(self.unity_prefs.read_text(), original)

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
