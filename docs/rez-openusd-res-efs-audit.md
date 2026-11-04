# Task 1 audit: RES 2026.06 EFS reuse for Rez / OpenUSD

**Status:** Design + offline preflight only. No EFS mount, EC2, AWS/IAM edit,
RES runtime edit, AMI rebuild, SSH change or deployment. Task 2 needs approval.

## Audited source and exact call graph

Pinned upstream: [aws/res tag 2026.06](https://github.com/aws/res/tree/2026.06);
our production-proven standalone runtime uses narrow local overrides.

1. `runtime/overrides/ideavirtualdesktop/app/linux/bootstrap.py::run()`
   executes `shared_storage.configure()` **only when
   `STUDIO_STANDALONE != "true"`**. This bypass is intentional.
2. Upstream `ideabootstrap/file_system/shared_storage.py::configure()`
   dispatches to `file_system/linux/shared_storage.py::configure()` for Linux.
3. Linux `configure()` calls `_has_storage_provider("fsx_lustre")`,
   handles `/home/*/.ssh/authorized_keys` backup/restoration, calls
   `_mount_shared_storage()`, then optionally performs Lustre tuning.
4. `_has_storage_provider()` and `_mount_shared_storage()` both call
   `res.resources.cluster_settings.get_config("shared-storage")`:
   `get_config()` → `get_settings()` →
   `res.utils.cluster_settings_utils.get_config_entries()` →
   `table_utils.scan("cluster-settings")` (**DynamoDB RES control plane**).
5. `_mount_shared_storage()` filters the entries through
   `ideabootstrap.file_system.common.eval_shared_storage_scope()`. Its
   `provider == "efs"` branch creates the mount directory and calls
   `_add_efs_to_fstab(efs.dns, mount_dir, mount_options)`.
6. `_add_efs_to_fstab()` uses `_mount_directory_exists()` to avoid a
   duplicate /etc/fstab entry and appends an **NFS4** line with defaults:
   `nfsvers=4.1,rsize=1048576,wsize=1048576,hard,timeo=600,retrans=2,noresvport`.
   This is generic NFS4, not an EFS TLS/IAM/access-point configuration.
7. Finally `_mount_shared_storage()` calls **global `mount -a`** with
   bounded randomized retry attempts. Its last failed attempt does not
   explicitly raise a final readiness error; do not infer a successful mount
   solely from returning from this function. S3 mount support additionally
   depends on the RES credential broker API.

Source links:
- [RES Linux EFS mount implementation](https://github.com/aws/res/blob/2026.06/source/idea/idea-bootstrap/src/ideabootstrap/file_system/linux/shared_storage.py)
- [RES storage dispatcher](https://github.com/aws/res/blob/2026.06/source/idea/idea-bootstrap/src/ideabootstrap/file_system/shared_storage.py)
- [RES scope predicate](https://github.com/aws/res/blob/2026.06/source/idea/idea-bootstrap/src/ideabootstrap/file_system/common.py)
- [RES cluster-settings reader](https://github.com/aws/res/blob/2026.06/source/idea/library/src/res/resources/cluster_settings.py)
- [RES DynamoDB scanner](https://github.com/aws/res/blob/2026.06/source/idea/library/src/res/utils/cluster_settings_utils.py)

**Conclusion:** Do NOT simply remove the standalone skip or call `configure()`.
Doing so restores DynamoDB dependency, unrelated Lustre/S3 paths and SSH
authorized-key copying (contrary to the optional SSH requirements in #70).

## Reuse strategy / minimal standalone adapter for Task 2

Preserve RES 2026.06 and the existing DCV/GDM/Xorg/NVIDIA initialization.
Implement an **isolated, opt-in EFS mount adapter** with validated static
configuration, no RES cluster settings, broker, credential lookup or database.
Reuse RES's EFS NFS4 mount semantics and idempotent fstab design; avoid
duplicating a broad multi-provider storage framework.

1. Accept only a specifically approved filesystem ID / mount target and
   access policy, exact package mountpoint `/opt/studio/packages`, and
   read-only consumer mode. Confirm storage identity and expected VPC/DNS,
   mount target, SG TCP/2049 and workstation profile before any deployment.
2. Prefer an approved EFS mount-helper transport with `tls` and, if IAM
   access points are chosen, `iam,accesspoint=...`. The upstream
   `_add_efs_to_fstab` can be reused directly **only if** the reviewed
   NFS4 DNS/fstab method meets approved security requirements. Do not mistake
   unencrypted NFS4 for EFS TLS/IAM; decide this before implementation.
3. Reuse the upstream fstab/idempotence/retry *behavior* but scope mounts
   to the exact EFS target (not global `mount -a`), verify readback via
   `/proc/self/mountinfo`, and fail observably. Use a bounded optional
   systemd mount/automount path that does not make DCV login depend on EFS.
   Do not modify SSH keys or user home directories.
4. Workstation consumers receive read-only access to the Rez package tree;
   EFS publisher CI retains separately authorized write rights. Separate
   shared writable models/projects belong to #71 and must not inherit the
   software-package mount's access privileges.
5. Add offline configuration/fstab/mount idempotence tests, explicit failure
   paths and a separate supervised CPU mount acceptance checkpoint. No GPU
   change until CPU validated. Never delete shared EFS on workstation destroy.

This is a **design**, not an installed adapter. No filesystem or access-point
identity has yet been read from live AWS.

## Rez + OpenUSD compatibility findings

- `vision-kwest/studio-rez/env/studio.sh` and `env/studio-prod.sh` default
  `REZ_PACKAGES_PATH=/opt/studio/packages/prod`; stage roots are
  `{dev,test,prod,archive}`. Rez implicit packages are `platform,arch,os`.
  Workstation software must NOT silently fall back to `dev` when `prod`
  is missing.
- `studio-openusd/build.py` defaults to **OpenUSD v26.05** and package
  **`usd/26.05` under `/opt/studio/packages/dev`**. The publisher
  builds in the `openusd-builder` container and promotes through
  dev → test → prod; the existence of the published prod package is
  **not verified from GitHub source alone**.
- `studio-openusd/scripts/package_usd.py::render_package_py()` emits
  Rez `name="usd"`, `version="26.05"`, default requirements
  `platform-linux`, `arch-x86_64`, and sets PATH, LD_LIBRARY_PATH,
  PYTHONPATH and USD plugin paths. The published package includes
  `package.py`, `manifest.json`, and `lib/python`.
- The OpenUSD builder defaults to `/usr/bin/python3`; its environment can
  differ from the workstation. The build manifest records
  `python_version()` of the **packaging orchestrator**, not an explicit
  binding ABI contract. The generated Rez package does not pin a Python
  ABI requirement. **A filesystem metadata check cannot prove Python
  compatibility** or shared-library dependencies on Ubuntu 24.04.
- Before declaring functional compatibility, Task 4 must run a real
  `rez-env usd-26.05 -- /usr/bin/python3 -c 'from pxr import Usd; ...'`,
  `usdcat`, and `usdchecker` against the selected stage, and repair any
  actual ABI/dependency mismatch in the publisher, not by weakening Rez
  isolation. Initial CPU validation can explicitly select `dev`, while
  normal workstation usage must resolve `prod` after deliberate promotion.

## Read-only preflight (committed in Task 1)

`scripts/rez_openusd_preflight.py` inspects **only local files**. It
never runs `mount`, AWS, Rez, Python imports from untrusted packages,
or software installers. Usage **after** approved EFS mounting:

```bash
python3 scripts/rez_openusd_preflight.py --stage dev
python3 scripts/rez_openusd_preflight.py --stage prod
```

The preflight checks an exact local NFS4/EFS mountpoint with `ro` in
`/proc/self/mountinfo`, the requested stage, `usd/26.05`, static
`package.py` identity/Linux-x86_64 requirements, `manifest.json`
identity and `lib/python`. It returns the manifest build Python version
but **explicitly labels Python ABI and EFS identity as UNVERIFIED**.
Those need a runtime smoke test and trusted AWS/EFS configuration checks.
Tests use only temporary fixture directories and synthetic mount tables.

## Task 2 prerequisites / stop conditions

- Identify the **actual** EFS file system and mount targets/SG, whether its
  package contents are accessible at `dev/usd/26.05`, and whether a
  promoted `prod/usd/26.05` exists. Current GitHub README documents the
  publisher stage, not live EFS inventory.
- Review `amazon-efs-utils` vs `nfs-common` availability in CPU and GPU
  Ubuntu 24.04 images, TLS/IAM/access-point policy, root path ownership,
  Rez installer availability and Python ABI.
- Any AWS/IAM/mount/network mutation requires a *separate user approval*.
  Keep this opt-in and outside RES reboot/DCV readiness. No software publisher
  or `studio-rez` changes unless a real compatibility defect is proven.

Related: #71 writable studio assets/models; #70 optional SSH; #62 remote
access hardening. No broader storage or access policy is implemented here.
