# Shared application launchers

[Issue #121](https://github.com/vision-kwest/studio-workstation/issues/121) connects native Ubuntu application menus to writable `/studio` storage. This change installs the menu editor and directory integration; Blender, VS Code, ComfyUI and their launchers are created during later application installations.

**Verification (November 18, 2026):** All 138 local tests passed. New tests exercise real shell migration and native GIO discovery, execution, edits and deletion across two temporary homes, retaining system launchers. GIO ran on local Debian 13; this does not establish live Ubuntu GNOME/DCV behavior.

CPU/GPU templates with and without Rez rendered and passed `bash -n` (largest user data: 12,519 bytes, below 16 KiB). This implementation/review pass made no AWS changes or authenticated GUI checks.

## Locations and setup

CPU/GPU setup uses the existing asynchronous post-DCV service. It finds the desktop account by UID 2000, creates the shared directories as that user, and installs Ubuntu's `menulibre`, `desktop-file-utils` and `ripgrep` packages. It links `/usr/local/bin/studio` to the shared shell entry point. `--no-studio-software` still skips only Rez. Menu setup may finish after the desktop becomes available.

| Location | Purpose |
|---|---|
| `/studio/share/applications/` | Shared `.desktop` launcher files |
| `~/.local/share/applications` | Symlink to `/studio/share/applications` on each workstation |
| `/studio/share/icons/` | Shared custom images referenced by absolute path |
| `~/.local/share/applications.before-studio` | Original local directory, when one existed |
| `~/.config/menus/`, `~/.local/share/desktop-directories/` | Local MenuLibre layout/category data |

Existing local entries are copied into the shared directory without replacing names already present there; the original directory is retained as the backup above. A correct applications symlink is reused. A conflicting file or symlink, or an occupied backup path when migration is needed, causes setup to report an error. Resolve the conflict with the original data intact.

All launchers and overrides saved in the linked applications directory are shared. Desktop preferences, favorites, menu ordering and custom folder layouts remain local. System launchers in `/usr/share/applications` remain available; a shared entry with the same desktop ID can override one.

## Studio shell and application commands

[Issue #140](https://github.com/vision-kwest/studio-workstation/issues/140) adds a child Bash shell and uppercase application commands. Uppercase names identify Studio wrappers that apply shared configuration; third-party binary names stay unchanged. Install their executable files **once on EFS**, from the repository root as the ordinary desktop user:

```bash
mkdir -p /studio/bin /studio/projects /studio/apps/opencode/config
install -m 755 scripts/studio-shell/{studio,BLENDER,CODE,COMFY,OPENCODE,OLLAMA} /studio/bin/
cp -n scripts/studio-shell/opencode.json /studio/apps/opencode/config/opencode.json
```

This installs launch commands; application binaries remain in their existing directories. The no-clobber configuration copy preserves shared OpenCode settings. COMFY uses the existing helper and adjacent templates; its separate installation/update instructions appear below.

From a desktop terminal, enter the environment in the project directory you want to use:

```bash
studio env
CODE /studio/projects
exit
```

The `(studio)` prompt identifies the child shell. It reads your normal `.bashrc`, adds `/studio/bin` to its PATH, and loads the installed Studio production Rez environment when available. It exports `STUDIO_ROOT=/studio`, `STUDIO_PROJECTS=/studio/projects`, and `OLLAMA_MODELS=/studio/models/ollama`. Your current directory is retained; `exit` returns to the original terminal environment. Entering the shell does not start an application or model server.

New workstations receive the local `studio` link during setup, before optional Rez. On an existing workstation, use `/studio/bin/studio env`, or `python3 studio env` from the repository, until an administrator creates that link through the established SSM access. The normal desktop account needs no sudo for the shared installation above. CloudShell lifecycle commands continue to use `python3 studio workstation ...`.

| Command | Studio behavior |
|---|---|
| `BLENDER` | Launches `/studio/apps/blender/current/blender` |
| `CODE` | Launches shared VS Code with `/studio/apps/vscode/extensions` |
| `COMFY` | Delegates to the existing helper, including `--set-default PATH` and `--update` |
| `OPENCODE` | Launches `/studio/apps/opencode/current/opencode`; seeds the shared global settings link only when local JSON/JSONC configuration is absent |
| `OLLAMA` | Uses the installed `studio-rez-env ollama -- ollama` command and shared model directory; requires the existing Rez/Ollama installation |

Each wrapper forwards its arguments. Outside the Studio shell, use its absolute path, such as `/studio/bin/CODE`. Use the same absolute paths for Menu Editor commands; `CODE` entries may append `%F`, while terminal commands omit it. Set **Run in terminal** for a terminal application such as OPENCODE. Existing legacy launchers remain usable.

### OpenCode settings and local state

On a fresh local configuration, OPENCODE links only `~/.config/opencode/opencode.json` to `/studio/apps/opencode/config/opencode.json`, honoring `XDG_CONFIG_HOME`. Existing `opencode.json` or `opencode.jsonc` files and links are preserved. The supplied shared setting disables OpenCode's automatic update checks so the shared version is selected deliberately.

Keep OpenCode's database, credentials, caches and service state on local storage; keep XDG directories local, as with ComfyUI. Provider login and local history remain workstation-specific. After changing the shared executable, restart any existing OpenCode background server before checking the running version. Start Ollama explicitly with `OLLAMA serve` when needed; entering `studio env` does not start a model server. Existing model files stay in place, and the wrappers do not download models automatically.

The model directory is chosen by the Ollama server when it starts. Running `OLLAMA list` or `OLLAMA run` does not change the model directory of an already-running server; verify its configuration separately.

**Verification:** The command paths and configuration behavior are source verified. Real Ubuntu/DCV launches and replacement-workstation acceptance for Issue #140 remain separate from offline checks.

## Add an application through the GUI

1. Install or extract the application into a stable path under `/studio`, and verify its executable runs.
2. Open **Menu Editor** (MenuLibre) from GNOME's application search as the normal desktop user.
3. Choose **Add Launcher**; enter a name and the absolute executable path in **Command**, with any required arguments. Set **Working Directory** if needed.
4. Choose an icon from the application directory or `/studio/share/icons/`. **Browse Files** records the selected path; it does not copy the image. Avoid icons in a workstation's temporary Downloads directory.
5. Leave **Hide from menus** off, save, and launch the application from GNOME search. No per-entry sudo or publisher step is required.

For an installed shared Blender, use `/studio/bin/BLENDER` as its command. MenuLibre saves new entries as `menulibre-<name>.desktop` in the linked directory, choosing a free filename. Its edits to an existing writable entry keep that filename.

An application may already supply a `.desktop` file: copy it into `/studio/share/applications/` using Files, choosing a new filename if one already exists, then edit it in Menu Editor. Check `Exec`, `TryExec`, `Path` and `Icon` for obsolete installation paths; use absolute shared paths for shared executables and images. Copying a launcher does not install its program. `Exec` takes a program and arguments, not shell activation commands; use the application's launch script when one is needed.

Create entries with their working applications. If a launcher must remain installed while its executable is absent, **Advanced → Try Exec** can name that executable; missing/nonexecutable targets may hide the entry. `Hidden` and **Hide from menus** are explicit visibility settings, not installation checks.

## VS Code with shared extensions

[Issue #123](https://github.com/vision-kwest/studio-workstation/issues/123) covers the GUI-installed shared VS Code workflow. Download the official Linux x64 `.tar.gz`, extract it under `/studio/apps/vscode`, and make `/studio/apps/vscode/current` a symlink to the directory containing `code`, `bin` and `resources`. Keep the extracted version directory so an upgrade can select a new target for `current`.

The CODE wrapper creates `/studio/apps/vscode/extensions` as the ordinary desktop user. After installing the wrappers above, the shared Menu Editor entry uses:

```text
/studio/bin/CODE %F
```

Use `/studio/apps/vscode/current/resources/app/resources/linux/code.png` for its icon. Omit `%F` for a terminal launch. Keep VS Code's **Default** profile: its extension catalog is stored in the selected extensions directory. Personal preferences and session state stay local. Do not create `current/data`, which activates portable mode and overrides `--extensions-dir`.

Ubuntu 24.04 restricts unprivileged user namespaces. Its packaged VS Code AppArmor rule covers `/usr/share/code`, so an archive extracted on EFS can crash while creating Chromium's sandbox. Workstation setup installs and loads `/etc/apparmor.d/studio-vscode`, granting `userns` to executables named `code` below the resolved `/opt/studio/packages/shared/apps/vscode/` path. This follows Ubuntu's `flags=(unconfined)` compatibility approach; it is not a complete confinement policy. The application keeps its own sandbox, and the system-wide namespace setting remains enabled.

The profile is installed before VS Code needs to exist, independently of optional Rez. AppArmor reloads it at boot; replacement CPU/GPU workstations receive it from the same software-setup script. Existing workstations need the updated setup before retrying a failed launch. The actual GUI launch, extension use and replacement-workstation checks remain the acceptance criteria in #123.

## ComfyUI on a replacement GPU workstation

[Issue #131](https://github.com/vision-kwest/studio-workstation/issues/131) adds `scripts/comfy-desktop/studio-comfy` and two small configuration templates. Use the shared **ComfyUI** launcher for the first Desktop launch on a fresh workstation. The helper registers the existing **Studio ComfyUI** installation, selects shared storage, and uses Desktop's native startup preference to open that instance. It does not install or download ComfyUI, Python, models or custom nodes.

| Shared location | Purpose |
|---|---|
| `/studio/apps/comfy-desktop/current/Comfy-Desktop.AppImage` | Existing Desktop executable; the `current` symlink pattern is retained |
| `/studio/apps/comfyui/main/Studio ComfyUI (1)` | Existing standalone installation, including its environment and native user/workflow directory; preserve this exact path |
| `/studio/models/comfyui` | Model library and default download destination |
| `/studio/projects/comfyui/input`, `/studio/projects/comfyui/output` | Shared inputs and generated outputs |
| `/studio/projects/comfyui/workflows` | Existing exported workflow files |

An ordinary launch seeds only missing `~/.config/comfyui-desktop-2/settings.json` and `~/.local/share/comfyui-desktop-2/installations.json`. It carries forward the operator's disabled telemetry/beta preferences, omits the old workstation's local model fallback, and puts SQLite at `~/.local/share/comfyui-studio/comfyui.db`. It honors `XDG_CONFIG_HOME`, `XDG_DATA_HOME` and `XDG_STATE_HOME` when set; keep those on local storage. Electron caches, credentials and runtime state remain local. Without `--update`, existing files are left byte-for-byte unchanged by the helper; Desktop can subsequently update its own files normally.

### Select the default for fresh workstations

After installing another standalone instance on EFS, run this as the ordinary desktop user, supplying its full installation path:

```bash
/studio/bin/COMFY --set-default \
  "/studio/apps/comfyui/main/Studio ComfyUI (1)"
```

The command checks that the target is under `/studio` and has the standalone environment, `ComfyUI/main.py`, and virtual-environment Python. It uses matching local Desktop registration metadata when available, otherwise the target's `manifest.json`. It atomically replaces only the shared `installations.json` beside the helper, then exits. It does not launch Desktop or modify local configuration. A missing/incomplete target or unusable metadata leaves the previous shared selection intact.

The selected record is the single source of its startup ID: the launcher derives `autoLaunchOnStartup` when creating fresh local settings. Existing workstations keep their local configuration. The ordinary menu command is `/studio/bin/COMFY`; the AppImage still uses `current`. Multiple installed directories can remain on EFS, with this command selecting the one seeded on the next fresh workstation.

Only the selected identity/version fields and launch arguments are exported. Existing launch arguments are retained except that `--database-url` is replaced with the local-database placeholder. A manifest-only selection uses `--enable-manager` and that placeholder. Environment variables, credentials, runtime state, and cached Git-version metadata are not copied. Other explicit paths in custom launch arguments retain their original values; the tool does not reconstruct GUI preferences from an installation folder.

For a fresh setup test on an existing workstation, fully quit Desktop, create a temporary directory with `mktemp -d /tmp/studio-comfy-test.XXXXXX`, and set `XDG_CONFIG_HOME`, `XDG_DATA_HOME`, `XDG_STATE_HOME`, and `XDG_CACHE_HOME` to separate subdirectories there when running the normal launcher. A new temporary directory tests the newly selected default; reusing one tests preservation. This does not reset the workstation's normal profile.

### Apply the default to an existing workstation

[Issue #135](https://github.com/vision-kwest/studio-workstation/issues/135) adds an explicit update. **Fully quit Comfy Desktop first**, then run as the ordinary desktop user:

```bash
/studio/bin/COMFY --update
```

If the local startup ID and its resolved installation path already match the shared default, the helper leaves both local JSON files unchanged. Otherwise it replaces the local registered-instance list with the single shared default and updates `autoLaunchOnStartup`, preserving other local preferences. It writes the registry before the startup setting. Missing files receive the usual initial configuration. It then launches Desktop normally; this option does not update application software or models.

This changes local registration, so other entries disappear from this workstation's instance list; their installation directories and data stay intact. `--set-default PATH` selects the shared default, while `--update` applies it locally. Run these as separate commands. The menu launcher uses `/studio/bin/COMFY`.

### Install or update the shared helper

After installing the uppercase wrappers above, **install or update the helper on EFS**, as the ordinary desktop user, from the repository root:

```bash
mkdir -p /studio/bin /studio/apps/comfy-desktop/launcher
install -m 755 scripts/comfy-desktop/studio-comfy /studio/apps/comfy-desktop/launcher/studio-comfy
cp -n scripts/comfy-desktop/{settings,installations}.json /studio/apps/comfy-desktop/launcher/
ln -sfnT /studio/apps/comfy-desktop/launcher/studio-comfy /studio/bin/studio-comfy
cp -an /studio/share/applications/menulibre-comfyui.desktop /studio/share/applications/menulibre-comfyui.desktop.before-studio-comfy
sed -i 's|^Exec=.*|Exec=/studio/bin/COMFY|' /studio/share/applications/menulibre-comfyui.desktop
desktop-file-validate /studio/share/applications/menulibre-comfyui.desktop
```

The no-clobber template copy preserves a previously selected shared default when updating the helper. Keep the helper beside its templates: COMFY delegates to it without copying its registration logic. The legacy `/studio/bin/studio-comfy` link remains available to existing launchers.

Replacement workstations reuse those shared files automatically through the existing application-menu integration. The helper checks the shared app, environment and storage paths before creating local configuration; an unavailable path stops launch instead of allowing Desktop to select local fallback storage. It serializes initialization with a local lock, releases it before launching Desktop, and retains the already-required `--no-sandbox` argument. This disables Electron's sandbox; it does not alter the system's AppArmor policy. Errors go to stderr and, when available, a native Zenity dialog.

**Scope:** Ordinary launches seed missing files; `--update` explicitly applies the current shared instance selection to an existing workstation while Desktop is closed. Other existing preferences, including storage paths and onboarding state, remain local; use Desktop's GUI to change those. If an installation moves, select its new path with `--set-default` and run `--update` on workstations that should adopt it. The helper does not synchronize live Desktop sessions or coordinate concurrent installation updates.

**Acceptance:** On a fresh GPU workstation, launch **ComfyUI** from GNOME search, confirm that Studio ComfyUI opens without onboarding or Add Existing Instance, load an existing shared workflow, and generate an image into the shared output directory. Quit Desktop fully, launch again, and confirm local preferences remain. The operator has already reported successful manual reuse of the shared installation/models/workflows/output on a replacement; automatic first-launch acceptance is a separate check. Do not run a second backend against an installation currently being used for this test: native launch preparation can modify that shared environment before checking its port.

**Rollback:** Restore `menulibre-comfyui.desktop.before-studio-comfy` over the shared launcher (or set its Command back to `/studio/apps/comfy-desktop/current/Comfy-Desktop.AppImage --no-sandbox`). Keep the shared installation and data. Seeded local profiles remain ordinary Desktop configuration and continue to work without the helper.

**Source contract:** Comfy Desktop **v1.1.10**, commit [`ccccb94ff41348f02e96a01eaa1a8895ffc2bf19`](https://github.com/Comfy-Org/Comfy-Desktop/commit/ccccb94ff41348f02e96a01eaa1a8895ffc2bf19): [`settings.ts`](https://github.com/Comfy-Org/Comfy-Desktop/blob/ccccb94ff41348f02e96a01eaa1a8895ffc2bf19/src/main/settings.ts), [`installations.ts`](https://github.com/Comfy-Org/Comfy-Desktop/blob/ccccb94ff41348f02e96a01eaa1a8895ffc2bf19/src/main/installations.ts), [`lib/paths.ts`](https://github.com/Comfy-Org/Comfy-Desktop/blob/ccccb94ff41348f02e96a01eaa1a8895ffc2bf19/src/main/lib/paths.ts), and startup in [`index.ts`](https://github.com/Comfy-Org/Comfy-Desktop/blob/ccccb94ff41348f02e96a01eaa1a8895ffc2bf19/src/main/index.ts). Upstream code is unchanged. The bundled OEM mechanism is Windows-only; no supported Linux unattended registration interface was identified. Recheck these file formats when changing the shared Desktop version.

## Readiness and refresh

Wait for **Menu Editor** to appear before managing launchers. From a desktop terminal, these optional checks show the integration and service state:

```bash
readlink ~/.local/share/applications
command -v menulibre desktop-file-validate
systemctl status studio-software-setup.service --no-pager
```

The link should print `/studio/share/applications`. Setup diagnostics are in `/var/log/studio-software-setup.log`; the service can continue with optional Rez after menu setup. An imported file can be checked with `desktop-file-validate /studio/share/applications/your-app.desktop`.

If an early desktop session or another workstation's EFS changes leave missing/stale entries, **save your work, use GNOME Log Out, then Log In**. Reconnecting the DCV viewer does not restart the desktop session. Network filesystem changes made on another client do not generate local inotify events, so immediate refresh is not guaranteed. `update-desktop-database` updates MIME associations; it is not a general GNOME menu-refresh command.

## Rollback

For a running workstation, first stop and disable `studio-software-setup.service` so it cannot restore the link. Remove only the local `~/.local/share/applications` symlink, restore `applications.before-studio` if present (otherwise create a local applications directory), and log out/in. Keep `/studio/share/applications` and its contents. Revert the integration in provisioning for future workstations; existing EFS storage remains independent.

## Primary references

- [Ubuntu 24.04 MenuLibre package](https://packages.ubuntu.com/en/noble/menulibre); [MenuLibre 2.4.0 save paths](https://github.com/bluesabre/menulibre/blob/menulibre-2.4.0/menulibre/util.py).
- [XDG data directories](https://specifications.freedesktop.org/basedir/latest/); [desktop-entry keys](https://specifications.freedesktop.org/desktop-entry/latest/recognized-keys.html) and [command syntax](https://specifications.freedesktop.org/desktop-entry/latest/exec-variables.html).
- [Linux inotify limitations](https://man7.org/linux/man-pages/man7/inotify.7.html); [Ubuntu MIME-cache utility](https://manpages.ubuntu.com/manpages/noble/man1/update-desktop-database.1.html).
- [VS Code downloads](https://code.visualstudio.com/download), [extension directory](https://code.visualstudio.com/docs/configure/extensions/extension-marketplace), and [portable-mode behavior](https://code.visualstudio.com/docs/setup/portable).
- [Ubuntu 24.04 user-namespace compatibility profiles](https://documentation.ubuntu.com/release-notes/24.04/#unprivileged-user-namespace-restrictions); [AppArmor attachment patterns](https://manpages.ubuntu.com/manpages/noble/man5/apparmor.d.5.html).
