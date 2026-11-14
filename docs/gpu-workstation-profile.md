# GPU workstation profile — validated RES 2026.06 path

**Historical acceptance:** GPU AMI `ami-027c98f85b155c2ac` passed live validation in October–November 2026 but later failed on kernel `7.0.0-1017-aws` with an NVIDIA module installed only for `7.0.0-1014-aws`. **Draft Issue #100** switches new GPU launches to Canonical Ubuntu 24.04 `ami-0d6dd9e8265d48906` (the old GPU recipe’s parent), leaving the RES 2026.06 guest driver installer responsible for installing NVIDIA after dependencies/reboots. This change is **offline-only until separately approved live acceptance**. Merged PR #65 completed GPU Studio CLI creation, local DCV password login, NVIDIA OpenGL, stop/start persisted-file verification, and exact test teardown ([Issue #46](https://github.com/vision-kwest/studio-workstation/issues/46)). The successful 0.1.11/1 GPU Image Builder image was built separately; its Image Builder **pipeline remains disabled and points to recipe 0.1.10**.

For the current user-facing commands, use [Studio CLI](studio-cli.md). Earlier source-first design documents are historical.

## Runtime and GPU integration

- The renderer in [`runtime/render_workstation.py`](../runtime/render_workstation.py) accepts `--profile gpu`, limits GPU instance type to `g4dn.xlarge`, and invokes the original RES 2026.06 host installer with `-g NVIDIA`.
- New GPU launches use a driver-free Canonical base and let the **existing RES post-reboot installer** install NVIDIA GRID; the immutable RES archives, RES cloud-init/reboot continuation and DCV setup remain unchanged. Verify guest S3 driver download rights and `nvidia-smi` after the final reboot before promoting this candidate.
- `STUDIO_STANDALONE=true` isolates RES-only broker, cluster-setting and external identity dependencies while using DCV Linux `system` authentication.
- GPU persistent mode uses encrypted gp3 root EBS, stop-on-shutdown and `DeleteOnTermination=false`. Application files survive EC2 stop/start; its public IPv4 can change.
- The live Task 5 test verified the same 100 GiB EBS root volume, identical file SHA-256 before/after stop/start, RES readiness and T4/OpenGL after restart. The user confirmed an actual DCV login before the test.
- **The merged GPU CLI remains hardcoded to the one approved acceptance stack/token.** Other stack names need a deliberate CLI/IAM scope update; it is not a generic self-service GPU platform.

## Exact immutable runtime archives

Unlike the early single-prefix proposal, PR #65 fetches the two pinned GPU archives from **different S3 prefixes** under `studio-workstation-releases-401233741942-us-east-1`:

| Archive | Key |
|---|---|
| Installer | `releases/pr65-pinned-download-084e704e/res-2026.06/installation-scripts.tar.gz` |
| Application | `releases/task7-pr61-postreboot-a8ec326/res-2026.06/virtual-desktop.tar.gz` |

The exact SHA-256 and S3 VersionIds are documented in [runtime provenance](../runtime/README.md); preserve `s3:GetObjectVersion` instance-role permissions for both exact keys.

## Offline render for review

```bash
python3 runtime/render_workstation.py \
  --profile gpu --lifecycle persistent \
  --bucket studio-workstation-releases-401233741942-us-east-1 \
  --prefix releases/pr65-pinned-download-084e704e/res-2026.06 \
  --user workstation \
  --cleanup-token pr65-b23c8e5fdf67b683cd35 \
  --test-stack studio-gpu-cli-accept65 \
  --output /tmp/studio-gpu-review.json

aws cloudformation validate-template \
  --template-body file:///tmp/studio-gpu-review.json \
  --region us-east-1 --no-cli-pager
```

The base `runtime/workstation-template.json` contains a `RENDER_OFFLINE` placeholder and **must not be deployed directly**. ValidateTemplate checks structure, not GPU functionality. Use [`studio workstation create ... --dry-run`](studio-cli.md) to see CLI-rendered inputs without EC2 launch.

## DCV and cleanup

During the supervised test, `--client-cidr 0.0.0.0/0 --allow-public-dcv` explicitly allowed login from changing networks. This opens TCP 8443 to the public internet, requires a strong unique Linux password and was removed in acceptance cleanup. SSH stayed closed. For long-term use, prefer restricted ingress or a future identity-aware private path ([Issue #62](https://github.com/vision-kwest/studio-workstation/issues/62)). Enroll the local `workstation` password interactively through SSM, not in user data.

The test-only one-shot guard terminates the instance after approximately 150 minutes and attempts retained-EBS deletion 45 minutes later, even through EC2 stop/start. Do not treat it as a permanent workstation service or guaranteed EBS recycler. Test resources were deleted; the tag-scoped IAM expiration role remains.

## RES sources

- [RES 2026.06 host installer](https://github.com/aws/res/blob/2026.06/source/idea/idea-bootstrap/resources/scripts/virtual-desktop-host/linux/install.sh)
- [RES GPU driver installer](https://github.com/aws/res/blob/2026.06/source/idea/idea-bootstrap/resources/scripts/dcv/linux/gpu_drivers.sh)
- [Ubuntu DCV installer](https://github.com/aws/res/blob/2026.06/source/idea/idea-bootstrap/resources/scripts/dcv/linux/debian/dcv_server.sh)
- [Historical GPU/Image Builder audit](res-gpu-imagebuilder-audit.md)
