# Unity Hub and Editor

Install the official Linux Hub AppImage in a version directory under /studio/apps/unity-hub and select it with current. Use Hub to install the Editor and Android Build Support (SDK/NDK/OpenJDK) into shared storage, then select the Editor release with /studio/apps/unity/current. The wrapper expects Editor/Unity below that link. Install the official Linux x64 .NET 10 SDK under /studio/apps/dotnet (dotnet executable and sdk/10.*); install VS Code and extensions using [its guide](vscode.md). Keep Hub sign-in and license local.

### Unity on current and replacement workstations

[Issue #153](https://github.com/vision-kwest/studio-workstation/issues/153) adds one shared `UNITY` wrapper. Install it with the commands above; current and future workstations use the same `/studio/bin/UNITY` file. No workstation provisioning change is required.

Install the native Unity CLI and Editor menu entry **once on EFS**, from the repository root as the desktop user. The verified source below is Unity CLI **1.0.0**, installed by Hub **4.0.1** at `~/.local/bin/unity`. If absent, obtain the matching official artifact first; the wrapper has no downloader.

```bash
printf '%s  %s\n' bd680055cb4a8d9d244c70d79eefa5828f6668075097559547e1ca4aece07b2b "$HOME/.local/bin/unity" | sha256sum -c - &&
install -D -m 755 "$HOME/.local/bin/unity" /studio/apps/unity-cli/1.0.0/unity &&
ln -sfnT 1.0.0 /studio/apps/unity-cli/current &&
install -m 644 share/applications/unity-editor.desktop /studio/share/applications/unity-editor.desktop &&
desktop-file-validate /studio/share/applications/unity-editor.desktop
```

Before changing local settings, the wrapper checks the shared Unity CLI, Hub AppImage, selected Editor, VS Code, .NET 10 SDK and the Editor's bundled Android tools. These applications and modules must already be installed; the wrapper does not download or update them.

On launch, it:

- Exports `DOTNET_ROOT` and the required `PATH` for Hub, the Editor and their child processes, including launches from GNOME.
- Creates `~/.vscode/extensions` pointing to `/studio/apps/vscode/extensions` only when the local path is absent.
- Seeds only a missing `kScriptsDefaultApp` preference in `~/.local/share/unity3d/prefs` with the shared VS Code path. Existing values, including an explicitly blank choice, are preserved.
- Registers the resolved shared Editor through the native CLI and adds missing project records for `/studio/projects/unity/*/ProjectSettings/ProjectVersion.txt`. Discovery examines only these immediate project directories; existing registrations and project source files are preserved.
- Runs `/studio/apps/unity-hub/current/UnityHub.AppImage` by default. `UNITY --editor [args]` uses the same preparation and environment, then launches the shared Editor directly with those arguments.

The native editor-add result for an already-registered Editor (code `6`) is accepted. Other CLI errors stop launch. Projects are added only when absent from the native project-list query; no Hub registry or authentication profile is copied between workstations.

Unity uses its native bundled Android SDK, NDK and JDK defaults; the wrapper does not add custom Android preference keys. Unity/Hub credentials, license state, caches and other local profile data remain local. No profile is copied from another workstation.

The preference seed follows the **observed Unity 6000.3.29f1** Linux XML format, version `1.1`, with base64-encoded string values. Registration uses the observed Hub 4.0.1 / CLI 1.0.0 native contract. Recheck these contracts when upgrading; they are not a claim of full fresh-workstation GUI acceptance.

When adopting the wrapper on an existing workstation, save your work and quit Unity, Hub and VS Code normally once. Start Hub through `UNITY` so newly started processes inherit the environment; an already-running Hub or VS Code process retains its previous environment.

```bash
studio env
UNITY
```

The existing shared `unityhub.desktop` remains unchanged, using `Exec=/studio/bin/UNITY %u` and `TryExec=/studio/bin/UNITY`. The new **Unity Editor** entry calls `/studio/bin/UNITY --editor` and uses `/studio/apps/unity/current/Editor/Data/Resources/LargeUnityIcon.png`. Both shared menu entries serve existing and replacement workstations.

On a fresh workstation, complete Hub sign-in and license activation normally. The wrapper registers the existing shared Editor and discovered projects before launch; no reinstall or manual **Locate** step is required for that registration.

**Hub discovery validation (Issue #153):** The registration wrapper and new `unity-editor.desktop` were deployed. An empty local HOME/XDG profile ran the actual registration and preparation twice, producing one registered Editor and two projects without duplicates; only the final GUI `os.execv` was stubbed. Both launchers passed `desktop-file-validate`. Native GIO discovered **Unity Editor** with the expected command and icon and `should_show=true`.

The current profile's native CLI lists Editor **6000.3.29f1**, its Android/modules information, and the intact shared `UnitySmokeTest` and `QuestSmokeTest` projects. The real `UNITY --editor -version` command returned `6000.3.29f1` successfully. After a normal restart, Hub displayed the native Unity sign-in screen. Sign-in remains local on each fresh workstation.

**Operator acceptance (Issue #153):** The user confirmed that both projects appeared in Hub and opened and ran one. Project discovery and that project launch are user-accepted; C# editing and an Android build remain separate checks. The reported Editor slowness is tracked independently in Issue #163 below.

**Rollback:** Restore the previous shared `UNITY` executable and remove only the added `unity-editor.desktop` if reverting this extension. Keep the existing Hub launcher, shared applications/projects and local settings. Native registrations remain ordinary Hub configuration.

#### Local project Library cache (Issue #163)

[Issue #163](https://github.com/vision-kwest/studio-workstation/issues/163) moves each discovered project's generated `Library` to local EBS while keeping application binaries and project source on EFS. Unity documents [Library regeneration from project source](https://docs.unity3d.com/6000.3/Documentation/Manual/ImportingAssets.html); its manual also classifies cloud-stored projects as unsupported, so this EFS layout remains a Studio-specific integration.

Live diagnosis found the correct NVIDIA T4 OpenGL renderer, but the Editor and two workers waited in NFS writeback on `Library/*DB-lock` files. Host I/O pressure was about 55%; EFS used Elastic throughput with `PercentIOLimit` below 1%. This evidence motivates moving the cache write path; it does not establish the fix's performance yet.

For each discovered project, `UNITY` hashes its resolved absolute path with SHA-256 and uses the first 16 hex characters as `<key>`. The local target is `$HOME/.cache/studio-unity/<key>/Library`; the project's EFS `Library` becomes a symlink to it.

An existing EFS `Library` directory is preserved as `Library.efs-backup` before linking. The wrapper recreates a missing local target on later launches when the existing link matches the expected target. It preserves conflicting links and backups and reports the conflict rather than replacing them.

Save and close all Unity Editors before migration. The wrapper refuses migration while a Unity process is running or the project's `Temp/UnityLockfile` exists. Only `Library` moves: `Temp` stays on EFS with Unity's native project lock. This assumes the same default workstation account and home path on replacement instances.

The local cache regenerates on a fresh workstation and survives stop/start while that workstation's EBS remains. The first import rebuilds generated data; projects and their source assets remain shared. The symlink needs no elevated privileges or new service; bind mounts were rejected because they require password-based sudo and boot integration.

**Native cache validation:** A disposable copy of the same scene, with source on EFS and Library on local EBS, completed two native Editor batch Play/close cycles at 55.8 and 56.2 frames/second over five seconds. The Library link survived both exits and the native Temp lock was removed. An initial probe used an incorrect scene path and was corrected; the two completed runs also logged a Unity Search indexing exception, so this does not establish error-free GUI acceptance. The 18 wrapper/environment tests pass, including cache preservation, repeat launch, missing local targets and active-project/conflict refusal. Interactive playback and the reported persistent splash window are checked separately on the original project.

**Deployment:** After all Editors had exited, the shared wrapper prepared local Library targets for both `UnitySmokeTest` and `QuestSmokeTest` on `editor`. The source folders remain on EFS. The existing smoke-test Library is preserved as `Library.efs-backup`; Quest had no previous Library to preserve. The previous shared wrapper is saved as `/studio/bin/UNITY.before-local-library`.

**Cache rollback:** With Editors closed, restore the prior wrapper and replace only the expected `Library` symlink with the preserved `Library.efs-backup`, when present. If there was no previous Library, Unity can regenerate it on EFS. Keep project source files and the local cache intact.

## Android development from a local Mac

The reverse SSH tunnel lets a remote Editor use the ADB server on the developer's local computer, with a USB-connected Android device or headset. Install Android platform-tools locally, enable USB debugging on the device and authorize the computer. Start the local ADB server and check the device:

```bash
adb start-server
adb devices -l
ssh -NT -i "$HOME/path/to/private_key" -o IdentitiesOnly=yes \
  -o ExitOnForwardFailure=yes -o ConnectTimeout=10 \
  -R 127.0.0.1:5037:127.0.0.1:5037 workstation@WORKSTATION_HOST
```

Keep that terminal open. On the remote desktop, check using the bundled client:

```bash
/studio/apps/unity/current/Editor/Data/PlaybackEngines/AndroidPlayer/SDK/platform-tools/adb devices -l
```

Use compatible ADB client/server versions. Stop any competing remote ADB server before establishing the tunnel; do not kill the local server while using it. The remote loopback listener is not a public ADB port. See [SSH access](../studio-cli.md#ssh-access) for public-key setup and forwarding; never copy private keys to EFS. The tunnel command reflects the earlier reverse-forward setup; a complete Android build/deploy acceptance is not established here. Verify the device list and Unity Build And Run independently.


[Installation index](README.md) · [Shared menu and shell setup](../shared-applications.md)
