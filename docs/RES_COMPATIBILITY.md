# RES 2026.06 → Standalone Workstation Compatibility Map

> **Historical source-analysis baseline (October 2026):** This was an *initial* map before the RES standalone adapter, GPU AMI and CLI acceptance were implemented. Its `TO TRACE`, `proposed`, `unresolved` and `not yet validated` cells are preserved historical questions, **not current deployment blockers**. The live-tested RES path is described in [runtime provenance](../runtime/README.md), [GPU profile](gpu-workstation-profile.md) and [Studio CLI](studio-cli.md).

**Status: initial source-backed map; not a complete dependency audit.** This document deliberately marks gaps rather than inventing equivalence. Upstream: [aws/res tree at tag 2026.06](https://github.com/aws/res/tree/2026.06). The successful RES Demo used stock Canonical Ubuntu 24.04 in us-east-1 (previously identified as `ami-0fc0d6e8d70ab2d42`) with runtime provisioning; that AMI ID alone does not reproduce RES's installed desktop.

## Confirmed runtime path

```text
EC2 launched from stock Ubuntu AMI
  -> RES user-data / download and execute bootstrap (exact call site: TO TRACE)
  -> virtual-desktop-host/linux/install.sh -p false
       -> common/linux/install_common.sh and other common dependencies
       -> common/linux/sssd.sh, nfs_utils.sh, s3_mountpoint.sh (RES environment)
       -> dcv/linux/debian/dcv_server.sh: install_prerequisites
       -> first reboot when required
  -> virtual-desktop-host/linux/install_post_reboot.sh -p false
       -> dcv/linux/dcv_server.sh
            -> GPU driver setup where applicable
            -> DCV package installation and Xorg configuration
       -> SSM agent installation near end
       -> second reboot when required
  -> common/linux/install_app.sh (virtual-desktop-app)
       -> RES Python bootstrap / DCV session and startup integration (full call graph: TO TRACE)
```

`PREBAKING_AMI=true` is a separate Image Builder path and deliberately skips self-reboots. Do not use it as evidence of runtime behavior.

## Upstream sources and decisions

| Upstream 2026.06 source | Confirmed role | Standalone disposition | Evidence / gap |
|---|---|---|---|
| [virtual-desktop-host/linux/install.sh](https://github.com/aws/res/blob/2026.06/source/idea/idea-bootstrap/resources/scripts/virtual-desktop-host/linux/install.sh) | Initial host setup; calls common scripts and desktop prerequisites; runtime first reboot | **Preserve sequencing**, adapt environment-specific calls only | Must trace all parameters, env variables, locks and script reentry |
| [virtual-desktop-host/linux/install_post_reboot.sh](https://github.com/aws/res/blob/2026.06/source/idea/idea-bootstrap/resources/scripts/virtual-desktop-host/linux/install_post_reboot.sh) | DCV/GPU stage; SSM agent later; runtime second reboot and application handoff | **Preserve sequencing** | Need complete runtime continuation contract |
| [dcv/linux/dcv_server.sh](https://github.com/aws/res/blob/2026.06/source/idea/idea-bootstrap/resources/scripts/dcv/linux/dcv_server.sh) | GPU driver and DCV installer orchestration | **Reuse where possible** | Map required environment and configuration inputs |
| [dcv/linux/debian/dcv_server.sh](https://github.com/aws/res/blob/2026.06/source/idea/idea-bootstrap/resources/scripts/dcv/linux/debian/dcv_server.sh) | Ubuntu desktop prerequisites, DCV packages, dummy Xorg | **Reuse where possible** | CPU vs NVIDIA branches need full verification |
| [dcv/linux/gpu_drivers.sh](https://github.com/aws/res/blob/2026.06/source/idea/idea-bootstrap/resources/scripts/dcv/linux/gpu_drivers.sh) | GPU driver selection and setup | **Preserve for NVIDIA variant** | Driver licensing/version selection and instance family must be traced |
| [resources/package_config.yml](https://github.com/aws/res/blob/2026.06/source/idea/idea-bootstrap/resources/package_config.yml) | Package and download metadata | **Pin and reuse relevant entries** | Check checksum URLs and mutable release channels |
| [common/linux/install_common.sh](https://github.com/aws/res/blob/2026.06/source/idea/idea-bootstrap/resources/scripts/common/linux/install_common.sh) | Shared OS/bootstrap dependencies | **Audit selectively** | Identify calls essential to desktop vs RES management |
| [common/linux/sssd.sh](https://github.com/aws/res/blob/2026.06/source/idea/idea-bootstrap/resources/scripts/common/linux/sssd.sh) | RES directory identity integration | **Candidate removal** | Confirm no DCV/desktop call requires its output |
| [common/linux/nfs_utils.sh](https://github.com/aws/res/blob/2026.06/source/idea/idea-bootstrap/resources/scripts/common/linux/nfs_utils.sh) | Network storage prerequisites | **Candidate removal** | Confirm no remaining mount dependencies |
| [common/linux/s3_mountpoint.sh](https://github.com/aws/res/blob/2026.06/source/idea/idea-bootstrap/resources/scripts/common/linux/s3_mountpoint.sh) | RES storage integration | **Candidate removal** | Confirm package/config side effects |
| [common/linux/install_app.sh](https://github.com/aws/res/blob/2026.06/source/idea/idea-bootstrap/resources/scripts/common/linux/install_app.sh) | RES Python application bootstrap handoff | **Trace before adaptation** | Not safe to remove wholesale until DCV initialization is mapped |
| [ideabootstrap/dcv/dcv.py](https://github.com/aws/res/blob/2026.06/source/idea/idea-bootstrap/src/ideabootstrap/dcv/dcv.py) | Automatic console permissions/config and readiness polling | **Preserve independent DCV behavior** | Broker/session-state coupling needs isolation |
| [ideabootstrap/dcv/linux/dcv_host.py](https://github.com/aws/res/blob/2026.06/source/idea/idea-bootstrap/src/ideabootstrap/dcv/linux/dcv_host.py) | Linux DCV host config | **Adapt authentication only where necessary** | Trace config and external auth dependencies |
| [ideabootstrap/dcv/linux/x_server.py](https://github.com/aws/res/blob/2026.06/source/idea/idea-bootstrap/src/ideabootstrap/dcv/linux/x_server.py) | Xorg startup/readiness and permissions | **Reuse where possible** | Confirm call sites and service ordering |
| [ideabootstrap/dcv/linux/start_dcv_service.py](https://github.com/aws/res/blob/2026.06/source/idea/idea-bootstrap/src/ideabootstrap/dcv/linux/start_dcv_service.py) | DCV systemd service readiness/configuration | **Reuse where possible** | Identify RES-only service overrides |
| [ideabootstrap/dcv/linux/gnome_utils.py](https://github.com/aws/res/blob/2026.06/source/idea/idea-bootstrap/src/ideabootstrap/dcv/linux/gnome_utils.py) | GNOME startup behavior | **Reuse if required** | Confirm actual runtime call path |

## Infrastructure dependency decisions (provisional)

| RES infrastructure | Standalone target | Decision status |
|---|---|---|
| Cognito / Active Directory / SSSD | Local Linux users + DCV system authentication | **Proposed replacement**, needs exact auth call-site map |
| DCV broker, external verifier, connection gateway, ALB | Direct DCV TCP/UDP 8443 restricted to trusted client CIDR | **Proposed replacement**, must validate console access |
| EFS, FSx, NFS, S3 mounts | Local encrypted EBS | **Proposed removal**, inspect boot dependencies |
| RES multiuser/session agent | Single-workstation lifecycle and console session | **Undecided** until Python bootstrap traced |
| RES cluster manager / VDI service | Minimal CloudFormation EC2 + IAM + network | **Proposed removal**, retain any essential bootstrap inputs |
| RES remote installation SSM policy | SSM optional for maintenance, not mandatory for runtime install | **Proposed**; distinguish from AWS RES's own SSM handling |

## Acceptance and unknowns

**Unresolved:** exact EC2 user-data generation and reentry, RES Python startup call graph, required config files/environment, auth replacement scope, license obligations, service ordering, GPU/Xorg readiness, clean first-login credential provisioning.

**Required validation:** CPU GNOME/DCV browser login, stop/start, GPU `nvidia-smi` and accelerated renderer, independent first-boot provisioning, and cleanup without RES resources. Passing `systemctl` or `dcv list-sessions` alone is insufficient.

No implementation is authorized by this document alone. Record proposed modifications in `docs/DEVIATIONS.md` and obtain review first.
