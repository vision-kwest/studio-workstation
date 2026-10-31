# Studio Workstation

A lightweight AWS Ubuntu 24.04 workstation system using **EC2 Image Builder**, **CloudFormation**, **AWS Research and Engineering Studio (RES) 2026.06 bootstrap**, **Amazon DCV**, and **Systems Manager**. No full RES control plane is required for the standalone workstation.

![Illustrated Studio Workstation architecture](docs/images/system-architecture.svg)

## Visual guides

- [Architecture: how the pieces fit together](docs/visual-architecture.md)
- [CPU and GPU Image Builder pipelines](docs/gpu-image-builder-visual.md)
- [Workstation lifecycle: create, connect, stop, start, destroy](docs/workstation-lifecycle-visual.md)
- [GPU source trace and validation gates](docs/res-gpu-imagebuilder-audit.md)

## Current status

| Area | Status |
|---|---|
| CPU Ubuntu 24.04 + DCV | **Validated**: fresh deployment, local login, stop/start reconnection |
| CPU Image Builder | **Validated**: staged GDM-first CPU recipe 0.5.0 |
| GPU Image Builder | **Validated**: recipe 0.1.11/1 built, tested and distributed; AMI `ami-027c98f85b155c2ac` available in us-east-1 |
| GPU workstation DCV login | **Validated**: RES automatic console, local `workstation` password login and reconnect after reboot |
| Trusted public TLS hostname | Deferred |

The CPU and GPU AMIs are **separate**. Do not deploy a CPU AMI as a GPU workstation. The GPU AMI was validated in a separate live `g4dn.xlarge` acceptance test; see [Issue #60](https://github.com/vision-kwest/studio-workstation/issues/60). The GPU Image Builder pipeline remains disabled and still references recipe 0.1.10; the successful 0.1.11/1 build was user-initiated directly.

## Quick start and operations

See [Studio CLI](docs/studio-cli.md) for the guarded `studio workstation` commands, and [GPU profile](docs/gpu-workstation-profile.md) for the source-first GPU build path.

The repository includes the RES-derived runtime release tooling and CloudFormation renderer. AWS SSM provides administrative access; Amazon DCV provides interactive desktop access using a **local Linux account**, not IAM credentials.

## Security and cost

- Do not commit passwords, private keys, AWS credentials, or NVIDIA driver binaries.
- Public DCV TCP 8443 was opened only for a time-limited interactive acceptance test and removed afterward. Restrict ingress by default; future secure access-from-anywhere is tracked in [Issue #62](https://github.com/vision-kwest/studio-workstation/issues/62). Verify the DCV TLS certificate fingerprint before accepting a self-signed certificate.
- EC2 compute costs stop when a workstation is stopped, but EBS volumes may continue to incur charges.
- Image Builder GPU builds incur temporary GPU compute and storage charges.
- The published GPU AMI source snapshot is currently unencrypted; temporary test roots were encrypted with `DeleteOnTermination=true`. Audit production snapshot encryption and retained volumes.

## Upstream RES 2026.06 references

- [Original NVIDIA GPU driver installer](https://github.com/aws/res/blob/2026.06/source/idea/idea-bootstrap/resources/scripts/dcv/linux/gpu_drivers.sh)
- [Original Ubuntu DCV installation](https://github.com/aws/res/blob/2026.06/source/idea/idea-bootstrap/resources/scripts/dcv/linux/debian/dcv_server.sh)
- [Original Linux workstation installer](https://github.com/aws/res/blob/2026.06/source/idea/idea-bootstrap/resources/scripts/virtual-desktop-host/linux/install.sh)

The project reuses the original RES provisioning logic with narrowly scoped standalone adaptations. See the source audit for exact integration boundaries.
