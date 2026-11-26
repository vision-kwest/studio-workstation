# Visual Studio Code

[Issue #123](https://github.com/vision-kwest/studio-workstation/issues/123) covers the GUI-installed shared VS Code workflow. Download the official Linux x64 `.tar.gz`, extract it under `/studio/apps/vscode`, and make `/studio/apps/vscode/current` a symlink to the directory containing `code`, `bin` and `resources`. Keep the extracted version directory so an upgrade can select a new target for `current`.

The CODE wrapper creates `/studio/apps/vscode/extensions` as the ordinary desktop user. After installing the wrappers above, the shared Menu Editor entry uses:

```text
/studio/bin/CODE %F
```

Use `/studio/apps/vscode/current/resources/app/resources/linux/code.png` for its icon. Omit `%F` for a terminal launch. Keep VS Code's **Default** profile: its extension catalog is stored in the selected extensions directory. Personal preferences and session state stay local. Do not create `current/data`, which activates portable mode and overrides `--extensions-dir`.

Ubuntu 24.04 restricts unprivileged user namespaces. Its packaged VS Code AppArmor rule covers `/usr/share/code`, so an archive extracted on EFS can crash while creating Chromium's sandbox. Workstation setup installs and loads `/etc/apparmor.d/studio-vscode`, granting `userns` to executables named `code` below the resolved `/opt/studio/packages/shared/apps/vscode/` path. This follows Ubuntu's `flags=(unconfined)` compatibility approach; it is not a complete confinement policy. The application keeps its own sandbox, and the system-wide namespace setting remains enabled.

The profile is installed before VS Code needs to exist, independently of optional Rez. AppArmor reloads it at boot; replacement CPU/GPU workstations receive it from the same software-setup script. Existing workstations need the updated setup before retrying a failed launch. The actual GUI launch, extension use and replacement-workstation checks remain the acceptance criteria in #123.

## Development integration

Use CODE /studio/projects from studio env. Install extensions through the shared wrapper, for example:

```bash
CODE --install-extension ms-python.python
CODE --install-extension ms-dotnettools.csdevkit
CODE --install-extension visualstudiotoolsforunity.vstuc
```

The Python interpreter selected during testing was /usr/bin/python3. Interpreter selection and workspace trust are local preferences. The historical --disable-workspace-trust workaround is optional, not a wrapper default; only disable trust checks for code you control. See [Unity](unity.md) for its native external editor and extension link. Verify Python execution, extension discovery and C# editing separately. The shared wrapper and replacement extension reuse were exercised; do not infer Android build success from that.


[Installation index](README.md) · [Shared menu and shell setup](../shared-applications.md)
