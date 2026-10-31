# Image Builder 0.1.11/1 — exact offline preparation (not registered)

Source of truth: live AWS Image Builder recipe `studio-gpu-ubuntu2404-dcv/0.1.10`, inspected 2026-10-31. Keep parent `ami-0d6dd9e8265d48906`, 100 GiB gp3 root, existing `studio-gpu-image-builder` infrastructure and distribution, g4dn.xlarge, existing reboot order, and original GDM/desktop components.

## Ordered recipe candidate

1. `studio-cpu-gdm-first/0.5.0/1` — unchanged
2. `studio-gpu-desktop-res-stage/0.1.2/1` — unchanged
3. AWS `aws-cli-version-2-linux/1.0.5/1` — unchanged
4. **new** `studio-gpu-nvidia-res-stage/0.1.7/1` — change only immutable installation archive S3 URI and SHA-256; retain original pinned NVIDIA GRID installer, driver object version `b94g7u7FkVYD9m63G6RE3xHNLZSXrz3r`, SHA-256 `cae8d82b8407c6371805485b148aa667550abf1775ea7999be69da81cf593156`, and both NVIDIA reboot checkpoints
5. **new** `studio-gpu-dcv-res-stage/0.1.4/1` — correct the bake-time DPMS generator to `printf '%s\\n' 'Section "Extensions"' ' Option "DPMS" "Disable"' 'EndSection'` (literal backslash-n inside printf's *format*, not output text); retain DCV packages and DCV reboot. The RES runtime remains responsible for final DCV console configuration after launch
6. **new** `studio-gpu-dcv-smoke-test-res/0.1.2/1` — test only bake-time prerequisites: GDM/DCV enabled and active, T4/nvidia-smi/kernel module, nice-dcv-gl package, valid NVIDIA Xorg config, `glxinfo` installed, locked workstation password. **Do not require a console session, X11 xhost DCV permission or GPU GLX desktop rendering at bake time**: those are verified after the RES postlaunch bootstrap. This avoids declaring an otherwise valid base AMI broken before the RES application is installed

Recipe `studio-gpu-ubuntu2404-dcv/0.1.11` must use only those new component versions and unchanged others. Keep GPU pipeline disabled until separately approved build. Do not touch the CPU pipeline.

## Exact pinned RES runtime inputs

The proven Task 7 **postlaunch** installation archive is `s3://studio-workstation-releases-401233741942-us-east-1/releases/task7-pr61-postreboot-a8ec326/res-2026.06/installation-scripts.tar.gz`, SHA-256 `7ca921aa8a493d9399918f032b62f7e0e37ce7754fa3f1a2c805047507ecd7a1`, S3 version `dlOKXdRAEJqpeNZNBotj87q0bdNlI5VC`. Virtual desktop application SHA-256 `e7686d6240db9062b5ea62f94859bd657de8c1231e8a3ba097704f9faa45d147`, S3 version `nzyYrLhYAQ.OTFNpFE_xSHPaK06T1V53`. Do not confuse Image Builder's **bake-time driver install** with the proven RES **postlaunch console initialization**.

Before registration, verify the builder instance profile can read the new installation archive; confirm the archive still includes the expected original GPU installer entrypoints. Use the existing immutable GRID binary and checksum, not the mutable public `latest/` path.

## Security and acceptance gates

- No password in image, user-data, CI or logs. Keep `workstation` password locked in the AMI. Test interactive DCV system login separately through a tightly restricted client IP and temporary SSM-enrolled password.
- Preserve RES/DCV's default display layout. **Do not force 1920x1080**; the working Task 7 console reported 2560x1600.
- GPU smoke checks at bake time are not proof of postlaunch readiness. After image publication, launch with independently enforced termination safeguard, verify RES READY semaphore, automatic console owner, `root:dcv 0640` policy, NVIDIA GLX direct renderer, reboots and eventually interactive login.
- Review full component YAML and run `bash -n` on every changed shell command before any `CreateComponent` or `CreateImageRecipe`. Preserve unchanged component content byte-for-byte.
- Do not register components, create recipe, start build, merge PR #61 or modify AWS during this preparation.
