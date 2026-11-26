# DaVinci Resolve Free

Use the GPU profile with the larger supported g4dn.2xlarge (8 vCPUs, 32 GiB RAM, T4 16 GiB). It is the selected acceptance size, not a claim that smaller instances cannot run Resolve.

```bash
python3 studio workstation create --profile gpu --name resolve --instance-type g4dn.2xlarge --expires-in 8h
```

Run this lifecycle command from the CLI checkout, then connect to DCV. Obtain the official Linux installer from Blackmagic Design. The existing shared installation uses the 21.2-free payload under /studio/apps/davinci-resolve and its current link; use the installation reference below for staging rather than running the native installer blindly over an existing shared payload.

### DaVinci Resolve Free on GPU workstations

[Issue #160](https://github.com/vision-kwest/studio-workstation/issues/160) adds `RESOLVE` for the existing **21.2-free** EFS installation. The [installation README](https://docs.google.com/document/d/e/2PACX-1vS6rO-gmE-zdIpZtyKLDADvPY4AFy0LpSEro3dveRhlim8bstc-T7kxx2cBKYVB1WFnMu3GVBYQaej3/pub) and [wrapper handoff](https://docs.google.com/document/d/e/2PACX-1vSbhdh_EC-QTXYSZd16TeUe-_jvQsAkbV5jddIo0B5c7k1evf3QPN-W_0a5pu28g98IDb8zf1O8SmgK/pub) describe the existing native installation; this wrapper does not install Resolve or its OS/GPU prerequisites.

The post-DCV software setup automatically invokes the existing shared helper on NVIDIA workstations when `/studio/apps/davinci-resolve` is present. It runs after EFS/menu setup and independently of optional Rez. A Resolve preparation failure is logged, optional Rez setup still runs, and the existing software service retries. DCV readiness remains independent. For an existing workstation provisioned before this integration, with `/studio` mounted and Resolve closed, the same preparation can be run once:

```bash
sudo /studio/apps/davinci-resolve/setup-local.sh --user "$(id -un)"
```

This creates `/opt/resolve` pointing to the selected shared release and the machine-local writable targets under `/var/lib/davinci-resolve/<release>`. The existing helper checks Ubuntu 24.04, the selected 21.2-free payload, and the NVIDIA driver, installs native runtime packages, and seeds missing local state/LUTs. `RESOLVE` verifies all eight mappings (`.license`, `LUT`, `easyDCP`, `Extras`, `Fairlight`, `Immersive`, `Apple Immersive`, `logs`), directory access, and actual resolved local storage before launching. Missing preparation reports the command above; ordinary launches never run sudo or APT.

The wrapper's unprivileged `prepare-user.py` initializes only missing native files in `~/.local/share/DaVinciResolve/configs`. Existing preferences, project databases and LUTs remain unchanged. Resolve creates its own project library and device configuration. The wrapper checks the data directory, configurations, project library, logs and cache for local storage, including symlink destinations. These paths follow the observed home-relative Resolve layout; the wrapper does not relocate them through `XDG_*` settings. Network, memory-backed and unrecognized filesystems are rejected for active state. Put source media, renders, project exports and Gallery stills under `/studio/projects/<project>/`.

To adopt the uppercase command and retain the existing `studio-resolve` command, run these commands **once on EFS** from the repository root as the desktop user. The backups retain the previous shared launcher and menu entry:

```bash
cp -pn /studio/bin/studio-resolve /studio/bin/studio-resolve.before-RESOLVE
cp -pn /studio/share/applications/studio-resolve.desktop /studio/share/applications/studio-resolve.desktop.before-RESOLVE
install -D -m 755 scripts/resolve/prepare-user.py /studio/apps/davinci-resolve/launcher/prepare-user.py
install -m 755 scripts/studio-shell/{RESOLVE,studio-resolve} /studio/bin/
desktop-file-edit --set-key=Exec --set-value='/studio/bin/RESOLVE %u' \
  --set-key=TryExec --set-value=/studio/bin/RESOLVE --remove-key=Path \
  /studio/share/applications/studio-resolve.desktop
desktop-file-validate /studio/share/applications/studio-resolve.desktop
```

The menu keeps the name **DaVinci Resolve** and its existing icon/other fields. Removing its redundant `Path=/opt/resolve` lets the wrapper report missing local preparation before changing directory. Errors appear on stderr and through Zenity when available in an X11 desktop. The legacy command delegates to the same wrapper; both preserve native arguments and exit status. From a terminal, use `studio env` followed by `RESOLVE`.

**Fresh-profile defaults:** Missing `config.dat` is derived from the selected release's `share/default-config.dat`, with ordered Media Storage roots at the expanded paths `~/.cache/davinci-resolve`, `~/Videos`, `/studio`, followed by Resolve's virtual entry. Missing `config.user.xml` receives only the five observed save/backup fields: project Live Save, project backups at ten-minute intervals, timeline backups, and the owner-approved destination `/studio/projects/resolve/backups`. The native **Studio Defaults** export supplied by the owner confirmed the XML schema; its preset ID and unrelated preferences are not copied. Initial seeding is specific to **21.2-free** and requires Resolve to be closed. A local lock and atomic create-without-overwrite preserve files created concurrently; an interrupted attempt can initialize whichever file is still missing. Ordinary launches preserve existing settings byte-for-byte. Change existing choices in Resolve's native Preferences UI.

Before replacing a workstation, export the project **with stills and LUTs** to `/studio/projects/<project>/resolve/exports` and keep its media on EFS. The active project database remains local. After import, recheck Gallery, cache and backup paths; the installation notes report that import can reset working folders.

**Acceptance:** Record native fresh-profile initialization and restart separately from offline wrapper/provisioning tests. The [GUI testing reference](https://docs.google.com/document/d/e/2PACX-1vS1VedykcOJq2UDN1-xwZLJa6khM1GiKtlDsTbfZje94gWUHGxV2WJxLBOXm_eIc5LDRdPK1zZOz5Hv/pub) covers EFS project import/render/reopen, actual automatic backup creation/recovery, and stop/start. Use a separate local profile and unique test output paths, then a genuinely fresh GPU workstation; preserve the existing acceptance baseline. The current evidence and remaining gates are tracked in [PR #161](https://github.com/vision-kwest/studio-workstation/pull/161).

**Rollback:** Restore `studio-resolve.before-RESOLVE` and `studio-resolve.desktop.before-RESOLVE` over their original paths, then remove only `/studio/bin/RESOLVE` if desired. Keep the installed application, setup helper, local projects/preferences and shared data.

November 23 live candidate checks confirmed automatic local preparation, all eight writable paths, preference-file initialization, repeat initialization and persistence after stop/start on g4dn.2xlarge. Native GUI interpretation of those files and actual backup recovery remained unverified at that checkpoint. Consult PR #161 for subsequent evidence. Do not treat generated XML as proof of a recoverable backup.


[Installation index](README.md) · [Shared menu and shell setup](../shared-applications.md)
