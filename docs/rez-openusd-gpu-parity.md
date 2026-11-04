# Task 5 — CPU/GPU parity for standalone EFS, Rez and OpenUSD

**Scope:** Offline implementation on draft PR #72; **no live CPU/GPU install, EFS mount, AWS/IAM mutation, package promotion, Image Builder change or merge**.

## One shared package stack, two execution profiles

| Contract | CPU (`--profile cpu`) | GPU (`--profile gpu`) |
|---|---|---|
| EFS consumer mount | `/opt/studio/packages` | Same |
| EFS transport | `efs` helper, `ro,tls,iam,accesspoint=...` | Same |
| Rez version | Pinned 3.4.0 | Same |
| Rez executable | `/usr/local/bin/studio-rez-env` | Same |
| Rez package stage | `/opt/studio/packages/prod` | Same |
| OpenUSD package | `usd-26.05` | Same |
| OpenUSD checks | `usdcat`, `usdchecker`, `pxr.Usd._usd` | Same |

All three components now accept the same explicit `cpu` / `gpu` profile selection. The default remains `cpu` for existing callers; neither profile uses a separate application repository, installer, or EFS layout. Unknown profiles fail before any privileged installation or external commands.

### GPU EFS adapter (not wired into startup)

The existing `runtime/standalone_efs.py` `plan()` and `configure()` methods now accept `profile="gpu"` in addition to CPU. Both use the exact same reviewed-format filesystem/access-point requirements, TLS/IAM `ro` fstab configuration, mountpoint, bounded retries, mount-readback checks, and rollback behavior. The profile argument is **a selection, not cryptographic attestation of the running EC2 instance**. Actual filesystem identity, EFS IAM read-only authorization, mount target networking and performance require independent supervised validation.

Example **read-only planning code**, with placeholders rather than real AWS IDs:

```python
from runtime import standalone_efs
preview = standalone_efs.plan(
    "fs-0123456789abcdef0", "fsap-0123456789abcdef0",
    profile="gpu", fstab_text="",
)
```

Do **not** call `configure()` on live hardware without separate deployment approval. No GPU lifecycle/expiration or RES/DCV/SSH boot paths invoke this adapter.

### Rez 3.4.0 installation parity (not yet installed)

`scripts/install_workstation_rez.py` now accepts `--profile gpu` with the same pinned upstream archive, fully SHA-256-verified offline wheelhouse, clean pinned `studio-rez` checkout and production-only configuration as the CPU profile. There is no driver-specific setup or separate Rez version for GPU.

After **separate live approval** and prerequisite verification, the *future* command would be:

```bash
sudo python3 scripts/install_workstation_rez.py \
  --profile gpu \
  --rez-archive /approved/rez-3.4.0.tar.gz \
  --wheelhouse /approved/wheelhouse \
  --wheel-manifest /approved/wheelhouse.sha256 \
  --studio-rez-root /opt/studio/studio-rez
```

The stable launcher remains `studio-rez-env`; every fresh login sources the original `studio-rez/env/studio-prod.sh`. The installer does not modify global Python, GPU drivers or GNOME.

### OpenUSD 26.05 validation parity (read-only, not yet run on GPU)

After the already approved-for-use prerequisites are **separately provisioned and verified**, run:

```bash
python3 scripts/check_openusd_rez.py --profile gpu
```

Exactly the same `usdcat`, `usdchecker`, CPython ABI/native module import and in-memory USD stage checks run on both profiles. This **does not claim GPU acceleration** or exercise NVIDIA/OpenGL: these are general OpenUSD runtime consistency checks. GPU hardware verification remains owned by the existing independent `nvidia-smi`, OpenGL, RES/DCV readiness and expiring GPU lifecycle safeguards. Task 5 did not change those safeguards or force an EFS mount during boot.

## Offline regression results and outstanding acceptance

Mocked tests exercise GPU plan equivalence, EFS mount idempotence/rollback, Rez version/prod-path parity, repeat/fresh-shell configuration, exact same OpenUSD command arrays and simulated GPU Python ABI failure. Existing CPU tests remain in the same CI workflow. All EFS and OpenUSD file operations are synthetic; no actual AWS filesystem or compiled `.so` is loaded.

Still outstanding: actual EFS access-point identity and permissions, availability of EFS utilities and pinned Rez installation artifacts on GPU/CPU machines, existence/promotion of `prod/usd/26.05`, native ABI linkage on either real machine, reboot persistence and sustained OpenUSD application performance. Those must be separately authorized and accepted; do not promote packages or mutate production data as part of this offline task.

Related: [Task 1–4 EFS and OpenUSD audit](rez-openusd-res-efs-audit.md), [Rez setup](rez-workstation-setup.md), [OpenUSD smoke contract](rez-openusd-smoke.md), optional SSH #70 and writable shared production assets/models #71.
