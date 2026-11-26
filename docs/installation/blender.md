# Blender

The November 18 installation used Blender 4.5.16 Linux x64. Download the matching official archive from https://download.blender.org/release/Blender4.5/ and verify its published checksum. From the desktop, with the downloaded archive in the current directory:

```bash
mkdir -p /studio/apps/blender
tar -xJf blender-4.5.16-linux-x64.tar.xz -C /studio/apps/blender
cd /studio/apps/blender
test -x blender-4.5.16-linux-x64/blender
ln -sfnT blender-4.5.16-linux-x64 current
/studio/bin/BLENDER --version
```

Install the shared wrapper from [scripts/studio-shell/BLENDER](../../scripts/studio-shell/BLENDER) using the common shell setup. Menu Editor command: /studio/bin/BLENDER; select an icon from the shared release. Save .blend files and assets under /studio/projects. Keep native Blender preferences and caches local; do not assume add-ons installed in HOME are shared.

Use a GPU desktop for interactive 3D. Verify viewport orbit, save/reopen a shared project and launch on a replacement. Historical replacement launch and smooth viewport operation were reported successful. Roll back by selecting the prior version in current, after closing Blender.


[Installation index](README.md) · [Shared menu and shell setup](../shared-applications.md)
