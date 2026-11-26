# GPU workstation profile — RES 2026.06 and automatic stop

## Current workstation lifecycle

New named GPU workstations use one CloudFormation-owned stop schedule. Timeout
stops EC2 and preserves the stack and saved files on its encrypted gp3 root disk.
Explicit `destroy NAME` permanently removes the workstation and local disks.
`DeleteOnTermination=true` does not delete a disk when its instance stops.

```bash
python3 studio workstation create --profile gpu --name art --expires-in 8h
python3 studio workstation stop art
python3 studio workstation start art --expires-in 1d
python3 studio workstation extend-expiration art --additional-minutes 480
```

Create defaults to `150m`; positive whole `m`, `h` and `d` durations have no
four-hour ceiling. Start verifies a fresh stop deadline before starting the
existing instance. See [Studio CLI](studio-cli.md#gpu-automatic-stop) for duration
reuse, near-deadline races and existing deployments that still have legacy timers.
Source/offline validation and observed live acceptance are recorded separately in
the Issue #68 implementation PR.

## Historical GPU acceptance

**Historical acceptance:** GPU AMI `ami-027c98f85b155c2ac` passed live validation in October–November 2026 but later failed on kernel `7.0.0-1017-aws` with an NVIDIA module installed only for `7.0.0-1014-aws`. **Draft Issue #100** switches new GPU launches to Canonical Ubuntu 24.04 `ami-0d6dd9e8265d48906` (the old GPU recipe’s parent), leaving the RES 2026.06 guest driver installer responsible for installing NVIDIA after dependencies/reboots. This change is **offline-only until separately approved live acceptance**. Merged PR #65 completed GPU Studio CLI creation, local DCV password login, NVIDIA OpenGL, stop/start persisted-file verification, and exact test teardown ([Issue #46](https://github.com/vision-kwest/studio-workstation/issues/46)). The successful 0.1.11/1 GPU Image Builder image was built separately; its Image Builder **pipeline remains disabled and points to recipe 0.1.10**.

For the current user-facing commands, use [Studio CLI](studio-cli.md). Earlier source-first design documents are historical.

## Runtime and GPU integration

- The renderer in [`runtime/render_workstation.py`](../runtime/render_workstation.py) accepts `--profile gpu` and invokes the original RES 2026.06 host installer with `-g NVIDIA`. Ordinary persistent workstations allow `g4dn.xlarge` (default) or `g4dn.2xlarge`, selected at creation with `--instance-type`; the historical temporary Task 7 path remains limited to `g4dn.xlarge`.
- **Issue #168 DKMS candidate published; fresh GPU and staged-kernel checks passed:** the new installer installs DKMS before NVIDIA, requests native registration, and verifies current/staged-kernel coverage. Independent APT-update and authenticated-login acceptance remain pending. Exact version/hash and evidence are in [runtime release provenance](../runtime/README.md#november-23-nvidia-dkms-candidate-issue-168).
- New GPU launches use a driver-free Canonical base and let the **existing RES post-reboot installer** install NVIDIA GRID. The application archive, RES cloud-init/reboot continuation and DCV setup are preserved. Before promotion, verify fresh GPU/DCV setup and a subsequent APT-triggered kernel rebuild and reboot.
- `STUDIO_STANDALONE=true` isolates RES-only broker, cluster-setting and external identity dependencies while using DCV Linux `system` authentication.
- The current GPU renderer uses encrypted gp3 root EBS, stop-on-shutdown and `DeleteOnTermination=true`. Saved application files survive EC2 stop/start; explicit destruction deletes local disks and the public IPv4 can change after restart.
- The live Task 5 test verified the same 100 GiB EBS root volume, identical file SHA-256 before/after stop/start, RES readiness and T4/OpenGL after restart. The user confirmed an actual DCV login before the test.
- Issue #98 replaced the original fixed acceptance name/token with named workstations and an internal deployment identity. The earlier fixed-name launch is historical.

## Exact immutable runtime archives

Unlike the early single-prefix proposal, PR #65 fetches the two pinned GPU archives from **different S3 prefixes** under `studio-workstation-releases-401233741942-us-east-1`:

| Archive | Key |
|---|---|
| Installer | `releases/issue100-res-package-policy-20261125/res-2026.06/installation-scripts.tar.gz` |
| Application | `releases/task7-pr61-postreboot-a8ec326/res-2026.06/virtual-desktop.tar.gz` |

The exact SHA-256 and S3 VersionIds are documented in [runtime provenance](../runtime/README.md).
New desktop stacks create their own exact key/version read policy before EC2 starts;
existing shared-role grants remain available for older stacks.

## Offline render for review

```bash
python3 studio workstation create --profile gpu --name art \
  --expires-in 8h --dry-run
```

The base `runtime/workstation-template.json` contains a `RENDER_OFFLINE` placeholder and **must not be deployed directly**. Dry-run previews rendered inputs without creating AWS resources; it does not prove live GPU or timer delivery.

## DCV and test cleanup

During the supervised test, `--client-cidr 0.0.0.0/0 --allow-public-dcv` explicitly allowed login from changing networks. This opens TCP 8443 to the public internet, requires a strong unique Linux password and was removed in acceptance cleanup. SSH stayed closed. For long-term use, prefer restricted ingress or a future identity-aware private path ([Issue #62](https://github.com/vision-kwest/studio-workstation/issues/62)). Enroll the local `workstation` password interactively through SSM, not in user data.

The historical PR #65 test used termination after approximately 150 minutes and
an attempted retained-EBS deletion 45 minutes later. Those were supervised test
safeguards. Future destructive test timers must be created separately for the
explicitly selected disposable test resources; they are not ordinary Studio CLI
behavior. Normal GPU expiration stops the instance and preserves its local files.

## RES sources

- [RES 2026.06 host installer](https://github.com/aws/res/blob/2026.06/source/idea/idea-bootstrap/resources/scripts/virtual-desktop-host/linux/install.sh)
- [RES GPU driver installer](https://github.com/aws/res/blob/2026.06/source/idea/idea-bootstrap/resources/scripts/dcv/linux/gpu_drivers.sh)
- [Ubuntu DCV installer](https://github.com/aws/res/blob/2026.06/source/idea/idea-bootstrap/resources/scripts/dcv/linux/debian/dcv_server.sh)
- [Historical GPU/Image Builder audit](res-gpu-imagebuilder-audit.md)
