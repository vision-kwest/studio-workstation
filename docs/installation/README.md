# Application installation

This is the operator entry point for shared GUI and developer applications on Ubuntu 24.04 Studio workstations. See the [CLI](../studio-cli.md) for provisioning and [EFS setup](../efs-helper-install.md) for storage. The separate Rez publishing workflow remains appropriate for packaged dependencies; GUI archives need not be Rez packages.

| Application | Guide |
|---|---|
| Blender | [Installation and launcher](blender.md) |
| VS Code | [Shared extensions and sandbox](vscode.md) |
| Unity | [Hub, Editor, Android, SSH and local cache](unity.md) |
| ComfyUI | [Desktop, standalone instances and models](comfyui.md) |
| Ollama | [Server, shared models and Modelfiles](ollama.md) |
| OpenCode | [Shared configuration and Ollama discovery](opencode.md) |
| DaVinci Resolve | [GPU, local setup and backups](davinci-resolve.md) |

## Shared installation pattern

Run application installation commands in the DCV desktop as the ordinary workstation user, not in CloudShell. First check `findmnt -T /studio` and `test -w /studio`. Wait for `systemctl status studio-software-setup.service --no-pager`; inspect /var/log/studio-software-setup.log if setup is incomplete.

| Path | Purpose |
|---|---|
| /studio/apps | Shared versioned application payloads |
| /studio/bin | Uppercase Studio launch wrappers |
| /studio/models | Shared model weights |
| /studio/projects | Persistent project source, media and exports |
| /studio/share/applications | Shared desktop entries |
| /studio/share/icons | Shared launcher icons |
| HOME and local EBS | Credentials, local preferences, databases and caches |

Download official Linux x86-64 artifacts, record release and checksum, extract into a new version directory and verify the executable before selecting it. Example pattern (replace APP and VERSION with actual names):

```bash
cd /studio/apps/APP
test -d VERSION
ln -sfnT VERSION current
readlink -f current
```

Only replace an existing symlink or absent path, never a real directory. Close affected applications before changing a shared selection; retain the previous release for rollback. Native installers and machine-local dependencies may require sudo; do not install OS packages into EFS or share entire HOME directories.

Install the [Studio shell and wrappers](../shared-applications.md#studio-shell-and-application-commands) once on EFS from the repository. Use `studio env`, then uppercase commands, or absolute /studio/bin paths from GNOME. A wrapper is not a downloader. Keep the native binary names unchanged.

## Add an application through the GUI

1. Install or extract the application into a stable path under `/studio`, and verify its executable runs.
2. Open **Menu Editor** (MenuLibre) from GNOME's application search as the normal desktop user.
3. Choose **Add Launcher**; enter a name and the absolute executable path in **Command**, with any required arguments. Set **Working Directory** if needed.
4. Choose an icon from the application directory or `/studio/share/icons/`. **Browse Files** records the selected path; it does not copy the image. Avoid icons in a workstation's temporary Downloads directory.
5. Leave **Hide from menus** off, save, and launch the application from GNOME search. No per-entry sudo or publisher step is required.

For an installed shared Blender, use `/studio/bin/BLENDER` as its command. MenuLibre saves new entries as `menulibre-<name>.desktop` in the linked directory, choosing a free filename. Its edits to an existing writable entry keep that filename.

An application may already supply a `.desktop` file: copy it into `/studio/share/applications/` using Files, choosing a new filename if one already exists, then edit it in Menu Editor. Check `Exec`, `TryExec`, `Path` and `Icon` for obsolete installation paths; use absolute shared paths for shared executables and images. Copying a launcher does not install its program. `Exec` takes a program and arguments, not shell activation commands; use the application's launch script when one is needed.

Create entries with their working applications. If a launcher must remain installed while its executable is absent, **Advanced → Try Exec** can name that executable; missing/nonexecutable targets may hide the entry. `Hidden` and **Hide from menus** are explicit visibility settings, not installation checks.

## Acceptance and maintenance

Verify the native executable, shared path, menu launch, first launch with a clean local profile, real application workload, repeat launch, stop/start and replacement workstation. Do not mistake a successful shell probe for GUI acceptance. Keep existing configuration unless an explicit migration is requested. Save project data on EFS before destroy; local EBS survives stop but not destroy. Record version, checksum, native dependencies, local paths, wrapper source, rollback and actual evidence for each upgrade. No simultaneous writes to a shared installation while updating it.

See [menu refresh and troubleshooting](../shared-applications.md#readiness-and-refresh). The app pages retain dated evidence and identify unverified steps.
