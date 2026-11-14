> **Historical design decision (2026-11-05).** The proposed *trusted CI runner* below was superseded by merged [on-demand publisher PR #76](https://github.com/vision-kwest/studio-workstation/pull/76): EC2 publisher profile with SSM, pinned reviewed recipes, IAM/TLS EFS, explicit `dev → test → prod` reference promotion. [PR #73](https://github.com/vision-kwest/studio-workstation/pull/73) and [PR #80](https://github.com/vision-kwest/studio-workstation/pull/80) implemented the consumer bootstrap. The promotion tool now uses shared immutable artifacts with stage symlinks, not payload-copy promotion. Preserve the original ADR as design history; use [current operations](../publisher.md) for deployments.

# ADR-0001 — Publisher/consumer software delivery on Studio EFS

**Date:** 2026-11-05  
**Status:** Accepted design for bounded offline engineering; **not deployed or live accepted**  
**Owners:** [workstation Issue #74](https://github.com/vision-kwest/studio-workstation/issues/74), [OpenUSD publisher Issue #8](https://github.com/vision-kwest/studio-openusd/issues/8). Later shared writable models/projects: [Issue #71](https://github.com/vision-kwest/studio-workstation/issues/71).

## Context and evidence

The existing Ubuntu 24.04 CPU/GPU workstations use pinned RES 2026.06 and Amazon DCV. `studio-rez` defines `/opt/studio/packages/{dev,test,prod}`; `studio-openusd` builds OpenUSD 26.05. EFS `fs-054fd0b72429f4569` already exists with mount targets. Historical `dev/usd/26.05` worked on an older SSH/NFS host, but neither present `prod` contents nor a current trusted CI runner has been live verified.

All prior OpenUSD publishing was a **single-owner proof of concept**, not an external production dependency. We may retire its anonymous NFS and SSH-era security model. Preserve shared data and current working CPU/GPU/DCV lifecycles.

## Decision

- **Mounting:** Reuse the Amazon EFS helper and native `fstab`/systemd automount in the existing RES-derived provisioning flow. Do not invoke the full RES cluster-settings/DynamoDB storage bootstrap or add a separate mount-state framework. Keep software setup independent of DCV readiness.
- **Consumer:** CPU/GPU workstations use the existing `StudioWorkstationSSMRole` for EFS `ClientMount` with write/root denied. Use encrypted IAM/TLS transport and only private NFS connectivity. A local `ro` mount is not an AWS security boundary.
- **Publisher:** A separate, trusted CI-runner EC2 identity (`StudioEfsCiPublisher` proposed) obtains scoped `ClientMount` and `ClientWrite` and a fixed non-root POSIX writer identity. Do not use long-lived AWS keys; verify actual runner and credentials before deployment. Do not share this role with workstations or untrusted PR jobs.
- **Publication:** Reuse existing `studio-openusd`, `studio-rez` and `studio-ci-images`. Ordinary builds target **dev only** with bounded writable mounts and a pinned builder digest; explicit promotion moves `dev → test → prod`. Consumers resolve `prod` only.
- **Storage:** Keep the existing EFS. An access point is not necessary for the initial mount architecture; revisit only if a verified directory/identity isolation need arises.

## Why not the alternatives?

- **Full RES shared-storage initialization:** Requires RES cluster-settings/DynamoDB services absent from the standalone baseline, handles unrelated SSH/provider configuration and invokes global `mount -a`. This is unnecessary for one optional software mount.
- **Selective RES private fstab helper:** Can write the EFS entry but has weak conflict handling and still relies on systemd/OS mounting; importing its private module adds dependencies without useful behavior.
- **Custom EFS adapter/installer framework (draft PR #72):** Duplicates native services and left actual provisioning disconnected. Keep its findings as test evidence, not an implementation requirement.
- **Preserve anonymous proof-of-concept publishing:** No real external consumer requires it; replace the insecure workflow instead of searching for the historical EC2 publisher.

## Tradeoffs, verification and limits

EFS IAM permissions are **filesystem-wide**: POSIX ownership, bind mount scopes and workflow controls separate `dev/test/prod`; they do not provide separate IAM boundaries. An administrator of the trusted runner can bypass workflow separation. Future writable project/model assets under Issue #71 require another scoped writer identity or mount/access-point strategy, not enlargement of the read-only software consumer role.

The current `studio-rez` promotion utility copies directly into the target version directory; an interrupted promotion is not atomic. Verify target integrity before accepting a controlled promotion and stop on incomplete results. Address atomicity in a separate justified change if needed.

**Unverified deployment gates:** trusted runner and POSIX permissions, publisher/client IAM/EFS resource policy (including anonymous denial), existing EFS stage contents, exact pinned Rez bundle and S3 read permission, helper availability on guest images, actual CPU/GPU native ABI and stop/start, independent GPU expiration/cleanup.

No AWS policy, role, SG, EFS contents, EC2 instance, guest, Image Builder or merge operation is authorized by this ADR. Record live proof and rollback before marking the project complete.
