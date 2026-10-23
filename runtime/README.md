# Studio Workstation runtime bootstrap v0.6.0 (CPU-only)

**Experimental, not yet deployed.** This is a new standalone provisioning route, not an update to the old EC2 Image Builder pipeline. It follows the working RES runtime model: EC2 user data installs a persistent local systemd bootstrap service; installation proceeds on the instance across controlled reboots. SSM is used for administration, **not** to execute or keep the bootstrap alive. There is no control EC2, OpenTofu, inbound SSH, directory server, EFS, or RES gateway.

## What gets created

A single CPU EC2 instance, security group (TCP/UDP 8443 only from your CIDR), SSM IAM instance profile, and encrypted 80 GiB gp3 root disk. The root volume is deleted when the instance is terminated; back up anything valuable first. AWS compute, IPv4, and EBS charges begin when deployed.

## Before deployment

- Use **us-east-1**. The default AMI is the same May 2026 Canonical Ubuntu 24.04 AMI as the working RES Demo: `ami-0fc0d6e8d70ab2d42`. Confirm its owner is `099720109477`, architecture x86_64, and root device is `/dev/sda1`. The older AMI may trigger the core-package upgrade guard in stage 1; in that case choose a newer Canonical-owned Ubuntu 24.04 AMI rather than bypassing the guard.
- Pick a VPC and **public subnet with a route to an internet gateway**. The template assigns a public IPv4 and requires outbound HTTPS for APT/DCV and SSM. Verify the VPC/subnet combination.
- Determine your current **public IPv4/32**; enter it as `ClientCidr`. Do not use `0.0.0.0/0`.
- This template is **CPU-only**. Do not use a GPU instance and expect NVIDIA drivers.
- CloudFormation user data embeds a copy of `runtime/bootstrap-cpu.sh`. If editing the script, update the embedded copy too (until packaging is automated).

## Deploy from AWS Console

1. Download `infra/workstation-runtime-cpu-v0.6.0.yaml` from the feature branch.
2. CloudFormation → Create stack → Upload template; enter AMI, VPC, subnet, trusted client CIDR, and instance type.
3. Acknowledge IAM resource creation; submit. No manual EC2 launch or shell commands are needed to start provisioning.
4. CloudFormation `CREATE_COMPLETE` means infrastructure is ready, **not** that DCV has finished. Local bootstrap continues across stages/reboots. Expect multiple minutes and several restarts.
5. Systems Manager → Session Manager (once Online) → inspect `sudo cat /var/lib/studio-workstation/stage`, `sudo tail -n 100 /var/log/studio-workstation/bootstrap.log`, and `sudo systemctl status studio-workstation-bootstrap`. The bootstrap writes `/var/lib/studio-workstation/ready` only after service checks pass.
6. If the service fails, use its log and `sudo journalctl -u studio-workstation-bootstrap --no-pager -n 100`. Fix the code and deploy a new stack/instance for a reproducible test; don't repeatedly mutate an unverified image.
7. Once ready, the Linux user `workstation` is deliberately **locked**. Using an approved interactive SSM session, set a unique strong password with `sudo passwd workstation` (do not paste it into CloudFormation, user data, SSM Run Command or chat). This is the only manual step for initial desktop authentication in v0.6.
8. Open the `DcvUrl` stack output in a browser, authenticate, confirm the actual GNOME desktop, and test stop/start. The initial DCV certificate may be self-signed. Never ignore certificate warnings for production use.
9. Stop or delete the stack after the test to control costs. Deleting terminates the instance and deletes the root disk under this template.

## Local stage machine

| Stage | Action | Continuation |
|---|---|---|
| 0 | GDM-first installation, Wayland disabled | systemd schedules reboot, stage=1 |
| 1 | RES-aligned `ubuntu-desktop`, dummy Xorg, locked Linux user | reboot, stage=2 |
| 2 | Verified DCV archive, server + web viewer + XDCV, local authentication | reboot, stage=3 |
| 3 | Verify GDM/DCV, Xorg, console session, port 8443, locked account | write `ready` marker |

Each stage runs **locally** under a systemd oneshot service and is resumed on boot. An unsuccessful stage does not advance its state; there is no automatic retry loop. Core-system APT transitions are simulated and conservatively blocked to avoid repeating the previous reachability failure. This is not proof that a systemd upgrade was the cause.

## Differences from RES

Preserved: GDM-first staging, separate desktop/DCV phases, reboot continuation, RES-style dummy Xorg, Ubuntu DCV package selection, automatic console-session idea.

Replaced/removed: Cognito/AD/SSSD, broker authentication, gateway, RES session agent, shared studio storage, GPU drivers (CPU profile), and all remote SSM build-command dependencies. Authentication is standard Linux DCV system auth.

## Acceptance criteria

- Infrastructure deployed with **no inbound SSH** and SSM access available.
- All four local stages finish and `ready` exists.
- `dcv list-sessions` reports console, GDM and Xorg work, and TCP 8443 is listening.
- Browser DCV login succeeds and renders the desktop.
- Instance stop/start retains a usable desktop.
- No secret baked into user data or an AMI.

This is **not yet a golden AMI**. Only capture an AMI after validation and sanitization. No NVIDIA image or software profiles are installed.
