# ComfyUI Desktop and standalone instances

Install the official Linux Desktop AppImage in a versioned directory under /studio/apps/comfy-desktop, name the executable Comfy-Desktop.AppImage, make it executable and select the version using current. Use Desktop's native installation flow to create a standalone instance under /studio/apps/comfyui/main/StudioComfyUI. Create shared models, input, output and workflow directories before configuring the helper. Do not copy an entire Electron profile to EFS.

## ComfyUI on a replacement GPU workstation

[Issue #131](https://github.com/vision-kwest/studio-workstation/issues/131) adds `scripts/comfy-desktop/studio-comfy` and two small configuration templates. Use the shared **ComfyUI** launcher for the first Desktop launch on a fresh workstation. The helper registers the selected shared instance, selects shared storage, and uses Desktop's native startup preference to open that instance. It does not install or download ComfyUI, Python, models or custom nodes.

| Shared location | Purpose |
|---|---|
| `/studio/apps/comfy-desktop/current/Comfy-Desktop.AppImage` | Existing Desktop executable; the `current` symlink pattern is retained |
| `/studio/apps/comfyui/main/StudioComfyUI` | Default standalone installation, including its environment and native user/workflow directory; select another valid path with `--set-default` |
| `/studio/models/comfyui` | Model library and default download destination |
| `/studio/projects/comfyui/input`, `/studio/projects/comfyui/output` | Shared inputs and generated outputs |
| `/studio/projects/comfyui/workflows` | Existing exported workflow files |

An ordinary launch seeds only missing `~/.config/comfyui-desktop-2/settings.json` and `~/.local/share/comfyui-desktop-2/installations.json`. It carries forward the operator's disabled telemetry/beta preferences, omits the old workstation's local model fallback, and puts SQLite at `~/.local/share/comfyui-studio/comfyui.db`. It honors `XDG_CONFIG_HOME`, `XDG_DATA_HOME` and `XDG_STATE_HOME` when set; keep those on local storage. Electron caches, credentials and runtime state remain local. Without `--update`, existing files are left byte-for-byte unchanged by the helper; Desktop can subsequently update its own files normally.

Helper options must match exactly: `--set-default PATH`, `--update`, or `--help`. Unknown options (including `-set-default`) and stray arguments produce usage help and exit with status 2 before any configuration changes or app launch. Native AppImage arguments must follow an explicit `--` separator and are forwarded unchanged; they cannot be combined with `--set-default`.

### Select the default for fresh workstations

After installing another standalone instance on EFS, run this as the ordinary desktop user, supplying its full installation path:

```bash
/studio/bin/studio-comfy --set-default \
  "/studio/apps/comfyui/main/StudioComfyUI"
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

When adopting `StudioComfyUI`, `cp -n` preserves the deployed selection, so you must run `--set-default` with the new path shown above to capture its actual registry or manifest metadata. With Desktop fully closed, run `--update` on each existing workstation, then verify the retained instance and its data before deleting the old installation. The helper changes registrations; it does not move or copy installation files.

Replacement workstations reuse those shared files automatically through the existing application-menu integration. The helper checks the shared app, environment and storage paths before creating local configuration; an unavailable path stops launch instead of allowing Desktop to select local fallback storage. It serializes initialization with a local lock, releases it before launching Desktop, and retains the already-required `--no-sandbox` argument. This disables Electron's sandbox; it does not alter the system's AppArmor policy. Errors go to stderr and, when available, a native Zenity dialog.

**Scope:** Ordinary launches seed missing files; `--update` explicitly applies the current shared instance selection to an existing workstation while Desktop is closed. Other existing preferences, including storage paths and onboarding state, remain local; use Desktop's GUI to change those. If an installation moves, select its new path with `--set-default` and run `--update` on workstations that should adopt it. The helper does not synchronize live Desktop sessions or coordinate concurrent installation updates.

**Acceptance:** On a fresh GPU workstation, launch **ComfyUI** from GNOME search, confirm that the selected shared instance opens without onboarding or Add Existing Instance, load an existing shared workflow, and generate an image into the shared output directory. Quit Desktop fully, launch again, and confirm local preferences remain. The operator has already reported successful manual reuse of the shared installation/models/workflows/output on a replacement; automatic first-launch acceptance is a separate check. Do not run a second backend against an installation currently being used for this test: native launch preparation can modify that shared environment before checking its port.

**Rollback:** Restore `menulibre-comfyui.desktop.before-studio-comfy` over the shared launcher (or set its Command back to `/studio/apps/comfy-desktop/current/Comfy-Desktop.AppImage --no-sandbox`). Keep the shared installation and data. Seeded local profiles remain ordinary Desktop configuration and continue to work without the helper.

**Source contract:** Comfy Desktop **v1.1.10**, commit [`ccccb94ff41348f02e96a01eaa1a8895ffc2bf19`](https://github.com/Comfy-Org/Comfy-Desktop/commit/ccccb94ff41348f02e96a01eaa1a8895ffc2bf19): [`settings.ts`](https://github.com/Comfy-Org/Comfy-Desktop/blob/ccccb94ff41348f02e96a01eaa1a8895ffc2bf19/src/main/settings.ts), [`installations.ts`](https://github.com/Comfy-Org/Comfy-Desktop/blob/ccccb94ff41348f02e96a01eaa1a8895ffc2bf19/src/main/installations.ts), [`lib/paths.ts`](https://github.com/Comfy-Org/Comfy-Desktop/blob/ccccb94ff41348f02e96a01eaa1a8895ffc2bf19/src/main/lib/paths.ts), and startup in [`index.ts`](https://github.com/Comfy-Org/Comfy-Desktop/blob/ccccb94ff41348f02e96a01eaa1a8895ffc2bf19/src/main/index.ts). Upstream code is unchanged. The bundled OEM mechanism is Windows-only; no supported Linux unattended registration interface was identified. Recheck these file formats when changing the shared Desktop version.

Verify the shared model directory in Desktop's storage settings, including the model download destination. Earlier GUI testing showed that a download-destination badge did not always change immediately; verify actual saved paths and downloaded files rather than relying on the badge. Keep SQLite and XDG state local. Use unique output names and avoid concurrent changes to the shared Python environment.


[Installation index](README.md) · [Shared menu and shell setup](../shared-applications.md)
