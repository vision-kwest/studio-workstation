# Image Builder 0.5.0 — RES-staged CPU/DCV candidate

This is a **new recipe**, not another patch to 0.4.x. The CloudFormation template is [infra/image-builder-cpu-v0.5.0.yaml](../../infra/image-builder-cpu-v0.5.0.yaml).

## Execution sequence

1. GDM-first component: verify Ubuntu 24.04, refresh APT indexes, simulate GDM dependencies, install `gdm3`, disable Wayland.
2. **AWSTOE Reboot action**, then verify GDM installation and dpkg database.
3. Desktop component: simulate `ubuntu-desktop` dependency transaction, install RES-aligned desktop and dummy Xorg configuration, create a **locked** `workstation` user.
4. **AWSTOE Reboot action**, then verify desktop packages.
5. DCV component: download the RES 2026.06 Ubuntu 24.04 archive and checksum, install server + web viewer + XDCV, configure DCV system authentication and automatic console.
6. **AWSTOE Reboot action**.
7. Test component: confirm GDM/DCV services, Xorg, DCV console, port 8443 and locked account.

AWS's AWSTOE `Reboot` action is explicitly documented: https://docs.aws.amazon.com/imagebuilder/latest/userguide/toe-action-modules.html

## Why this may fail fast

A prior build lost EC2 instance reachability during the package transaction that installed newer `systemd`. This version **simulates** both APT install transactions and aborts with `CORE_OS_UPGRADE_REQUIRED` if they would upgrade `systemd`, `libsystemd0`, `systemd-sysv`, `udev`, or `libudev1`.

This does **not** prove those upgrades caused the crash. It avoids repeating the observed high-risk transition inside a long SSM-managed installation. If it fails the guard, choose a newer **Canonical-owned Ubuntu 24.04 x86_64 AMI** with up-to-date core packages, verify the AMI owner, and update the `BaseAmiId` stack parameter. Do not disable the guard without evidence. An additional reboot cannot repair a crash that happens inside APT before the reboot action.

## AWS Console deployment

1. Ensure the prior 0.4.x execution has ended and all old build instances are terminated.
2. In us-east-1 CloudFormation, **Update** the existing stack with `infra/image-builder-cpu-v0.5.0.yaml` from this branch. Retain or deliberately update `BaseAmiId` and `BuilderInstanceType`. Review IAM capabilities and change set.
3. Confirm `UPDATE_COMPLETE`. In EC2 Image Builder open `studio-cpu-dcv-pipeline` and verify it references recipe **0.5.0** with four components in the order above.
4. Choose **Run pipeline**. Monitor the Image Builder log group and EC2 instance health. It creates paid temporary EC2 resources.
5. On success, launch a **fresh temporary test instance** from the produced AMI, using a restricted 8443 security group and SSM role. Provision a unique Linux password interactively and securely, without storing it in the AMI or CloudFormation. Verify browser DCV login, real GNOME rendering, and stop/start.
6. Only then consider the AMI validated. Clean up build/test resources and unused snapshots.

## Known limitations

- **Not deployed or tested**. The YAML and component steps need CloudFormation/Image Builder execution validation.
- It is **RES-derived**, not an exact copy of the RES runtime bootstrap: no RES agent, SSSD, EFS, broker, Cognito, gateway or RES token verifier.
- The default base AMI is the previously identified May 2026 Canonical image, which may trigger the core-upgrade guard. The guard intentionally fails safely instead of risking another unresponsive instance.
- DCV download endpoints are the RES 2026.06 package channel and may change over time.
- The image's Linux account is locked. The first-login credential flow is not automated.
- Image Builder's service checks do not prove actual graphical login. Human end-to-end testing is mandatory.
- No GPU variant or application profile installation in this milestone.
