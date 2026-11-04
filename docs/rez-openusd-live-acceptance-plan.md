# Rez + OpenUSD — supervised live acceptance and rollback plan

**Review only. Task 6 did not deploy or run this plan.** All AWS inspection, guest installs, EFS mounts, package promotions, live EC2 launches, IAM/networking changes and PR merge require separate, explicit approval. Each gate has a stop/check-in.

## Pinned scope and known limits

| Contract | Reviewed target |
|---|---|
| Region | us-east-1; reverify account/region before any command |
| CPU | Existing anchor stack `studio-workstation-cpu-v013`; never destroy this anchor as rollback |
| GPU | Exact historical `studio-gpu-cli-accept65` scope/token; past test was deleted, any new launch is billable |
| Independent GPU guard | Original PR #65 termination + EBS deletion schedules, checked before and after a supervised launch |
| EFS | Read-only for workstation consumers, TLS + IAM EFS mount helper and reviewed access point |
| Packages | `/opt/studio/packages/prod/usd/26.05`; **do not** silently resolve from dev |
| Rez | Version 3.4.0, pinned source SHA, complete preapproved offline wheelhouse, clean pinned studio-rez checkout |
| OpenUSD | `usd-26.05`, usdcat, usdchecker, CPython compiled `_usd` import and in-memory stage |

**Important:** PR #69 (configurable GPU expiration) remains draft/unmerged. Do not assume its extension options exist on the PR #72 branch. GPU expiration is active even while stopped; reserve ample acceptance and cleanup headroom.

## Gate A — independently verified inventory (read only)

Before any live mutation, identify *actual* EFS filesystem, access point, mount target and security group IDs. **None is established by the current offline PR.** Read-only AWS commands for a later separately approved inventory:

```bash
aws sts get-caller-identity
aws cloudformation describe-stacks --region us-east-1 --stack-name studio-workstation-cpu-v013
aws efs describe-file-systems --region us-east-1
aws efs describe-mount-targets --region us-east-1 --file-system-id FS_ID
aws efs describe-access-points --region us-east-1 --file-system-id FS_ID
aws efs describe-file-system-policy --region us-east-1 --file-system-id FS_ID
aws ec2 describe-security-groups --region us-east-1 --group-ids EFS_MOUNT_TARGET_SG_ID
```

Confirm EFS root/access-point path, matching VPC/AZ routing, **private** TCP/2049 only, encryption, POSIX access and workstation IAM permission `elasticfilesystem:ClientMount` without `ClientWrite`. Client-side `ro` alone is not an IAM security boundary. No public NFS ingress. Verify the package publisher's actual dev/test/prod USD stage and promotion state independently; published docs show `dev` as the default, not guaranteed prod availability. Any package promotion needs separate approval.

Verify the CPU/GPU image has `amazon-efs-utils`, Python 3 + venv, Git and valid local EFS prerequisites. Verify actual Rez 3.4.0 source archive against the pinned SHA-256, **all** dependency wheel hashes, and the clean pinned studio-rez checkout. The offline installer does not download or create these artifacts.

## Gate B — frozen dry review (no mounts)

Record PR #72 head commit and confirm all GitHub offline CI workflows pass. Snapshot and compare current CPU/GPU `/etc/fstab`, mountinfo, existing `/opt/studio/packages`, Rez target, launcher, EC2/EBS identity, Scheduler expiration and access policy. Reject unmanaged files or conflicting mounts. Inspect only the plan, not the actual mount:

```python
from pathlib import Path
from runtime import standalone_efs
print(standalone_efs.plan(
    "fs-0123456789abcdef0", "fsap-0123456789abcdef0",
    profile="cpu", fstab_text=Path("/etc/fstab").read_text()))
```

The IDs above are **syntactic placeholders**. A successful `plan()` does *not* prove those AWS resources or permissions exist. Substitute only independently reviewed real IDs in any later approved execution.

## Gate C — supervised CPU validation (separate write/mount approval)

1. Verify existing CPU DCV warning-free login, console owner, hostname/EIP, GNOME and system health **before** EFS or Rez changes. Preserve the original CPU root EBS and all user files.
2. During an explicitly approved maintenance window, invoke only `runtime.standalone_efs.configure(approved_FS_ID, approved_ACCESS_POINT_ID, profile="cpu")`. Check the exact fstab entry, EFS helper TLS/IAM diagnostics, real read-only NFS mount and independent AWS identity/permissions; avoid global mount commands. An EFS failure must not block basic DCV startup.
3. Confirm `prod/usd/26.05` actually exists with valid metadata, native libraries and file permissions. Verify workstation consumer cannot write (without changing production packages). **STOP** on a missing/unpromoted USD package; do not promote it implicitly.
4. With an approved, locally staged Rez archive/wheelhouse and pinned studio-rez checkout, invoke `scripts/install_workstation_rez.py --profile cpu` deliberately (root EBS write). Verify Rez version, exact production PATH/config, stable local versioned install, fresh-shell launcher and unmodified system Python.
5. Run `python3 scripts/rez_openusd_preflight.py --stage prod` and `python3 scripts/check_openusd_rez.py --profile cpu`. Capture full usdcat/usdchecker stdout/stderr, CPython SOABI/native extension path, binary USD version, and actual USD stage result. Any dependency/ABI error fails acceptance.
6. Only with separate restart approval, verify DCV login, mount, Rez launcher and OpenUSD checks after a CPU stop/start or reboot. Record all evidence before GPU testing.

## Gate D — supervised GPU parity (new billable launch approval)

Only after CPU success: approve an exact scoped GPU test stack/token, fixed start/cleanup UTC, max cost, restricted DCV client ingress and independent PR #65 expiration readbacks. Never rely on draft PR #69. Verify T4 `nvidia-smi`, NVIDIA OpenGL, RES/GDM/DCV ownership, protected HTTPS/EIP handoff, and the independently scheduled expiry *before* spending time on application setup.

Then repeat the **same** EFS mountpoint/read-only policy, pinned Rez 3.4.0 installer and `python3 scripts/check_openusd_rez.py --profile gpu`. Confirm both profiles resolve identical `prod/usd/26.05`; run the real native Python ABI probe. This proves package parity **only**, not OpenUSD GPU acceleration. Require supervisor sign-off before leaving GPU running.

## Rollback and cleanup (explicit scope, no shared data deletion)

- **No-go at Gate A/B:** no changes. Never guess EFS IDs, create a new filesystem or broaden IAM/SG to force access.
- **Mount failure:** the adapter removes only its own newly appended fstab line if the mount failed and is independently checked. An ambiguous/live mount state needs independent inspection before any edits. After explicit approval and checking open consumers, unmount only `/opt/studio/packages` and remove only the verified adapter-owned fstab line. **Never delete EFS, access points, production packages, model downloads or shared assets.**
- **Rez failure:** the installer removes only a freshly created failed installation at the exact versioned target, not an earlier valid install. On later rollback, inspect the exact launcher and local Rez directory and remove only those owned artifacts after approval. Do not wipe home directories or user settings.
- **USD failure:** the checker is read-only. Preserve diagnostics; do not modify `prod/usd/26.05`, build dependencies or GPU drivers during acceptance. Escalate to the package publisher for an independently approved repair.
- **GPU cleanup:** use the existing exact-stack guarded stop/destroy and verify instance/retained EBS, launch template, security group and both expiration schedules absent, and CPU EIP/cert ownership restored. Never delete the original CPU anchor or shared EFS. Partial CloudFormation/Scheduler cleanup requires a separate independently verified reconciliation.

## Evidence and explicit approval boundaries

| Required evidence | Current status |
|---|---|
| Offline PR #72 regressions and invariants | Verified by GitHub CI on exact head, record SHA |
| Actual EFS identity, IAM, SG, RO policy | Not verified on AWS |
| Approved Rez archive + full dependency wheelhouse staged | Not done |
| Actual promoted prod USD package + Python ABI | Not verified on workstation |
| CPU live install, DCV and reboot continuity | Not performed |
| GPU timed supervised test, parity and full cleanup | Not performed |

**Stop here.** The approval for Task 6 covers offline CI, documentation and this plan only. Any read-only live AWS audit, new EC2 launch, guest EFS mount, Rez installation, prod package promotion, IAM/SG mutation, Image Builder change or merge requires a separate request/approval. Related: optional SSH #70; shared writable models/projects #71.
