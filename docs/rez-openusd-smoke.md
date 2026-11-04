# Task 4 — OpenUSD 26.05 workstation smoke checks (offline implementation)

**Status:** Tested in draft PR #72 using synthetic EFS/package fixtures and
mocked subprocess responses. **No EC2 access, EFS mount, real Rez package
resolution, software install, USD binary invocation, or package promotion
occurred.** Task 5 GPU parity and any live acceptance require approval.

## Reuse and purpose

The source for the command sequence is the existing
[`studio-openusd/scripts/smoke_test_usd.py`](https://github.com/vision-kwest/studio-openusd/blob/main/scripts/smoke_test_usd.py)
and its `examples/hello.usda` fixture. The workstation-specific smoke
script `scripts/check_openusd_rez.py` retains those checks and adds
production-repository isolation, real native-module import, and fail-closed
diagnostics. It is **read-only**: no publishers, builders, EFS configuration,
Rez installation, `mount`, AWS commands, or RES/DCV integration.

## Required environment (future supervised workstation check)

1. A validated CPU EFS mount at `/opt/studio/packages`, accessible
   read-only; Task 2's standalone adapter is not yet installed or invoked.
2. A separately installed, verified `studio-rez-env` wrapper, which sources
   pinned `studio-rez/env/studio-prod.sh` and uses the original
   `config/rezconfig.prod.py`. Task 3's installer is not yet installed
   on EC2.
3. An explicitly promoted **`/opt/studio/packages/prod/usd/26.05`**
   containing `package.py`, `manifest.json`, `lib/python` and
   actual USD utilities/bindings. The builder's documented default
   destination is `dev/usd/26.05`; whether `prod/usd/26.05` exists
   in the real EFS has not been verified. This script **never** silently
   selects `dev` or promotes packages.
4. Ubuntu 24.04 `/usr/bin/python3` and runtime shared libraries compatible
   with the published USD package; compatibility is determined by actual
   import, not declared by a packaging manifest.

After separately approved provisioning, execute from a repository checkout:

```bash
python3 scripts/check_openusd_rez.py
```

This future command reads only the package/fixture and executes:

```text
studio-rez-env usd-26.05 -- usdcat fixtures/openusd/hello.usda
studio-rez-env usd-26.05 -- usdchecker fixtures/openusd/hello.usda
studio-rez-env usd-26.05 -- /usr/bin/python3 -c '<fixed embedded ABI probe>'
```

The first two validate a small, known USD scene. The third imports
`pxr.Usd` **and its compiled `pxr.Usd._usd` extension**, constructs a
`Usd.Stage.CreateInMemory()` scene, and emits a structured report about
the running CPython interpreter, SOABI, USD binary version, native extension
origin, Rez package path and selected production configuration.

Verification requires a real successful process exit, one parseable ABI
report, CPython version/SOABI consistency, OpenUSD binary version 26.05,
`REZ_PACKAGES_PATH=/opt/studio/packages/prod`, a production config path,
and **both imported Python/native modules inside the selected USD package**.
This detects a shadowed system `pxr` installation and catches many
binary/Python ABI incompatibilities that simple metadata tests cannot detect.

Checks are **not** evidence of GPU rendering or GUI `usdview`; those belong
in Task 5/GPU acceptance. The EFS identity remains marked `UNVERIFIED`
until independent AWS/EFS validation. A passing live import confirms binding
compatibility for the tested interpreter/dependencies only, not all future
Python versions or OpenUSD build variants.

## Failure reporting and offline test coverage

The smoke checker rejects missing/writable/non-NFS package mounts, missing
`prod` promotion, broken package metadata, an absent/symlinked/nonexecutable
launcher, malformed fixture, failed/nonzero `usdcat` or `usdchecker`,
timeout (60 seconds/command), Python import error, unparseable ABI results,
SOABI/runtime version mismatch, wrong USD version, unexpected Rez stage/config,
and `pxr` modules originating outside the selected package. All calls are
argument arrays using `subprocess.run` without a shell.

`tests/test_openusd_rez_smoke.py` supplies a temporary synthetic
`prod/usd/26.05` tree, fake NFS mountinfo, and an injected subprocess
runner. The native `.so` is a **dummy file never imported** by the tests.
The dedicated GitHub workflow executes these tests and existing Task 1–3
regressions, without AWS credentials or live infrastructure.

No `studio-rez`, `studio-openusd`, RES 2026.06, CLI lifecycle, DCV,
SSH, IAM, EFS or Image Builder files are modified for this task.

## GPU profile reuse (Task 5)

The same read-only smoke checker now accepts `--profile gpu` (CPU remains
the default). Both profiles run the **same** production `usd-26.05`
resolution, `usdcat`, `usdchecker` and native CPython ABI probe against
the **same** shared package path. The tool reports the selected profile;
it does not claim GPU rendering, verify NVIDIA/OpenGL, or inspect EC2
identity. All GPU parity tests are mocked/offline and the real GPU setup
and OpenUSD binary remain untested. See [GPU parity](rez-openusd-gpu-parity.md).
