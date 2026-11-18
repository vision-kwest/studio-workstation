# Shared application launchers

[Issue #121](https://github.com/vision-kwest/studio-workstation/issues/121) connects native Ubuntu application menus to writable `/studio` storage. This change installs the menu editor and directory integration; Blender, VS Code, ComfyUI and their launchers are created during later application installations.

**Verification (November 18, 2026):** All 138 local tests passed. New tests exercise real shell migration and native GIO discovery, execution, edits and deletion across two temporary homes, retaining system launchers. GIO ran on local Debian 13; this does not establish live Ubuntu GNOME/DCV behavior.

CPU/GPU templates with and without Rez rendered and passed `bash -n` (largest user data: 12,519 bytes, below 16 KiB). This implementation/review pass made no AWS changes or authenticated GUI checks.

## Locations and setup

CPU/GPU setup uses the existing asynchronous post-DCV service. It finds the desktop account by UID 2000, creates the shared directories as that user, and installs Ubuntu's `menulibre` and `desktop-file-utils` packages. `--no-studio-software` still skips only Rez. Menu setup may finish after the desktop becomes available.

| Location | Purpose |
|---|---|
| `/studio/share/applications/` | Shared `.desktop` launcher files |
| `~/.local/share/applications` | Symlink to `/studio/share/applications` on each workstation |
| `/studio/share/icons/` | Shared custom images referenced by absolute path |
| `~/.local/share/applications.before-studio` | Original local directory, when one existed |
| `~/.config/menus/`, `~/.local/share/desktop-directories/` | Local MenuLibre layout/category data |

Existing local entries are copied into the shared directory without replacing names already present there; the original directory is retained as the backup above. A correct applications symlink is reused. A conflicting file or symlink, or an occupied backup path when migration is needed, causes setup to report an error. Resolve the conflict with the original data intact.

All launchers and overrides saved in the linked applications directory are shared. Desktop preferences, favorites, menu ordering and custom folder layouts remain local. System launchers in `/usr/share/applications` remain available; a shared entry with the same desktop ID can override one.

## Add an application through the GUI

1. Install or extract the application into a stable path under `/studio`, and verify its executable runs.
2. Open **Menu Editor** (MenuLibre) from GNOME's application search as the normal desktop user.
3. Choose **Add Launcher**; enter a name and the absolute executable path in **Command**, with any required arguments. Set **Working Directory** if needed.
4. Choose an icon from the application directory or `/studio/share/icons/`. **Browse Files** records the selected path; it does not copy the image. Avoid icons in a workstation's temporary Downloads directory.
5. Leave **Hide from menus** off, save, and launch the application from GNOME search. No per-entry sudo or publisher step is required.

For example, a future Blender install could use `/studio/apps/blender/current/blender` as its command. MenuLibre saves new entries as `menulibre-<name>.desktop` in the linked directory, choosing a free filename. Its edits to an existing writable entry keep that filename.

An application may already supply a `.desktop` file: copy it into `/studio/share/applications/` using Files, choosing a new filename if one already exists, then edit it in Menu Editor. Check `Exec`, `TryExec`, `Path` and `Icon` for obsolete installation paths; use absolute shared paths for shared executables and images. Copying a launcher does not install its program. `Exec` takes a program and arguments, not shell activation commands; use the application's launch script when one is needed.

Create entries with their working applications. If a launcher must remain installed while its executable is absent, **Advanced → Try Exec** can name that executable; missing/nonexecutable targets may hide the entry. `Hidden` and **Hide from menus** are explicit visibility settings, not installation checks.

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
