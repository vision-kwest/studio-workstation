# Project brief — CPU/GPU software delivery (Rez 3.4.0 + OpenUSD 26.05)

**Status:** Approved *minimum-change design*; candidate code is offline-tested in [workstation draft PR #73](https://github.com/vision-kwest/studio-workstation/pull/73) and [OpenUSD draft PR #7](https://github.com/vision-kwest/studio-openusd/pull/7), **not yet live accepted or merged**. This brief is the current scope contract, not a deployment authorization. Details and acceptance checklists belong to [workstation Issue #74](https://github.com/vision-kwest/studio-workstation/issues/74) and [publisher Issue #8](https://github.com/vision-kwest/studio-openusd/issues/8).

## User outcome

Using the existing Studio CLI, a new Ubuntu 24.04 CPU or GPU workstation boots through RES 2026.06, supports trusted Amazon DCV without SSH, and runs the *same* centrally managed, production-only Rez/OpenUSD software. Software survives reboot and stop/start; missing EFS never disables the baseline desktop. Workstation expiration/destruction does not delete shared EFS packages or other user data.

## Existing baseline and actual obligations

- Preserve known-good CPU/GPU RES/DCV/Xorg/NVIDIA bootstrap, independent guarded GPU expiration/cleanup, trusted hostname/EIP/ACM and retained CPU root EBS.
- Existing AWS EFS filesystem `studio-efs` / `fs-054fd0b72429f4569` in `us-east-1` already has mount targets; a November 2026 read-only inventory found no access points, no custom filesystem policy, and no client EFS permissions on the workstation role. **Reverify before live operations.**
- `studio-rez` owns `dev/test/prod` configurations, core implicit packages and promotion commands. `studio-openusd` owns building and publishing `usd/26.05`. Production consumers resolve **prod only**.
- Earlier SSH-based OpenUSD publishing and anonymous NFS mounting were **proof-of-concept experiments**. There are **no external production users or compatibility commitments** to that publishing method. We may replace its workflow with approval; **data, secrets and unrelated resources are still protected**.
- Historically `dev/usd/26.05` ran an actual native `pxr.Usd` import on the experiment host. Present EFS contents, `prod/usd/26.05`, native ABI and current runner configuration remain **unverified**.
- Draft PR #72 is historical/offline reference only; it is not an authorized code base or live success.

## Approved direction and boundaries

1. **Consumer:** Use the AWS EFS mount helper plus Linux/systemd native automount and existing RES-derived user-data path; avoid the full DynamoDB RES control plane and new custom mount controllers. Small independent nonblocking software setup after DCV readiness. Existing `StudioWorkstationSSMRole` obtains read-only EFS authorization via IAM/resource policy; local `ro` by itself is insufficient.
2. **Publisher:** Use a dedicated, trusted CI service IAM writer/EC2 instance-profile identity, fixed non-root POSIX identity where appropriate, TLS/IAM EFS mount and existing build/Rez utilities. No historical publisher identity search required; no new CI server without an evidenced need.
3. **Promotion:** Normal OpenUSD builds write only to `dev`; `dev → test → prod` is an explicit reviewed operation using existing `studio-rez`. EFS IAM is filesystem-wide, not a per-stage IAM guarantee. Use narrowly writable Docker mounts and pinned image/artifact digests.
4. **Install:** Distribute verified pinned Rez 3.4.0 source/dependencies and production `studio-rez` config through existing immutable S3 release infrastructure. Verify S3 exact-key read permission. Do not replace system Python or silently resolve from `dev`.
5. **Future data:** [Issue #71](https://github.com/vision-kwest/studio-workstation/issues/71) separately owns shared writable production assets and AI models. The software consumer role's filesystem-wide write denial cannot also act as a general shared-data writer; design a distinct authorized identity/mount boundary later.

The durable decision and its tradeoffs are recorded in [ADR-0001](docs/adr/0001-software-publishing-and-consumption.md).

## End-to-end definition of done

The following must **actually succeed on both CPU and GPU** with exact-source evidence:

- [ ] New workstation provisioned by normal Studio CLI; trusted DCV GNOME login without daily SSH.
- [ ] EFS mounted privately over TLS/IAM; consumers cannot write, anonymous access rejected, publisher can still write through its separate identity.
- [ ] Rez 3.4.0 in a fresh desktop terminal, production-only package path, required implicit packages; `usd/26.05` identical version/manifest on both profiles.
- [ ] Actual `usdcat`, `usdchecker`, `pxr.Usd` in-memory stage; validate Python SOABI and linked library origins, not just source/mocks.
- [ ] Stop/start and reboot preserve access; missing EFS surfaces software failure **without** disabling DCV.
- [ ] GPU T4/OpenGL, guarded expiration schedules, EIP/ACM transfer and independently verified cleanup intact. No destruction of shared packages/assets or the protected CPU anchor.

## Separate approvals / explicit non-goals

Offline review is not AWS approval. Require exact-resource authorization before attaching instance roles/SGs, changing EFS policy, starting EC2, mounting, publishing bundles/packages, promoting `usd`, modifying guest state, Image Builder, or merging.

Do not expand this effort into Ollama storage, optional SSH (#70), general DCV exposure (#62), GPU expiration enhancements (#68), new package managers, a replacement RES control plane or speculative framework. Record current observed results against the issue acceptance criteria; reserve **Complete** for real CPU **and** GPU success.
