# CPU Studio Workstation: Rez 3.4.0 (Task 3, offline implementation)

**Status:** Installer and unit tests committed in draft PR #72. It has **not
been executed on EC2**, installed in the AMI, wired to CloudFormation/RES,
mounted EFS, or tested against the real OpenUSD binary.

## Reuse, pinning and prerequisites

Use the **upstream Rez 3.4.0 production installer** `install.py`, not a
generic `pip install rez` entry point. Rez upstream installs isolated
CLI entry points under `bin/rez/` with the `-E` Python flag to avoid
resolved `PYTHONPATH` breaking Rez itself.

The opt-in command `scripts/install_workstation_rez.py` requires pre-staged:

1. The official `rez-3.4.0.tar.gz` source archive, SHA-256:
   `57e03cd2a9dbab818290756de0fd1205fbd898b1e02205dbe2c90a7d0d0f5159`.
2. A local **wheelhouse** containing the Rez build/runtime dependencies and a
   `wheelhouse.sha256` file with *every* wheel listed as
   `<64 lowercase hex SHA256><two spaces><filename.whl>`. No extra wheel,
   symlink, missing item or digest mismatch is permitted. The installer sets
   `PIP_NO_INDEX=1` and `PIP_FIND_LINKS` so upstream pip cannot fetch
   packages from the network. Build/approve the wheelhouse separately;
   this PR **does not publish or generate** those supply-chain artifacts.
3. An existing local checkout of private `vision-kwest/studio-rez`
   at pinned commit `434a3ffe4775d17234b6d0bb2460ab049ece2262`,
   with clean worktree, reachable from the workstation.
4. Ubuntu 24.04 `python3`, `python3-venv`, and the required privileges
   to write `/opt/studio/rez/3.4.0` and `/usr/local/bin/studio-rez-env`.
   These are prerequisites, **not** changed by this script.

The checkout's unmodified `env/studio-prod.sh` and
`config/rezconfig.prod.py` are reused; no new Rez config is invented.
The approved production configuration contains:

```text
REZ_PACKAGES_PATH=/opt/studio/packages/prod
REZ_CONFIG_FILE=<checkout>/config/rezconfig.prod.py
implicit_packages=platform,arch,os
```

The checkout's version and the above package path/implicit package contract
are verified before installation. The `dev` package stage is **never**
silently added to production resolves.

## Future manual installation — separately approved action

After the artifacts and CPU host have been independently validated:

```bash
sudo python3 scripts/install_workstation_rez.py \
  --rez-archive /approved/rez-3.4.0.tar.gz \
  --wheelhouse /approved/wheelhouse \
  --wheel-manifest /approved/wheelhouse.sha256 \
  --studio-rez-root /opt/studio/studio-rez
```

This command **writes the root EBS filesystem** when actually run. It
unpacks only vetted tar members into a staging directory, invokes upstream
`install.py` offline, verifies the Rez production marker/version and
launcher, and publishes the installation at
`/opt/studio/rez/3.4.0` alongside the stable executable
`/usr/local/bin/studio-rez-env`. An exact repeat is idempotent; existing
foreign launchers/installations fail closed rather than being overwritten.

A normal, fresh `workstation` shell can then run:

```bash
studio-rez-env usd-26.05 -- usdcat --help
```

The wrapper automatically **sources `studio-rez/env/studio-prod.sh`** and
executes the upstream production Rez CLI. It does **not** prepend a Rez
virtualenv `bin` directory to the user's global PATH, replace system
Python, or modify `.bashrc`, SSH credentials, the DCV desktop, cloud-init,
RES, or the workstation CLI. The wrapper and source checkout must remain
on the local persistent root EBS volume; thus the command remains available
after a new login/reboot **as long as that EBS volume is retained**. It does
not survive root-volume destruction.

**Important:** Real `usd-26.05` resolves require a separately approved,
healthy EFS package mount and a promoted `prod/usd/26.05` package; those
are not established by this task. The package publisher defaults to
`dev/usd/26.05`. The actual Python ABI, libstdc++/shared-library
compatibility and `pxr` import remain **unverified on EC2**.
[Task 4's OpenUSD smoke checker](rez-openusd-smoke.md) is now available
for a separately approved CPU validation; its offline tests do not import
the real OpenUSD extension.

## Offline tests

`tests/test_workstation_rez_setup.py` uses temporary files and a mocked
upstream installer; no actual Rez install, pip network, EFS mount, EC2 or
AWS calls. It covers checksum and checkout pinning, dev/prod isolation,
unapproved artifacts, unsafe archive members, staging rollback, idempotence,
production marker/version, launcher PATH, argument forwarding and a **fresh
shell** invocation that restores the production configuration. The CI
workflow `rez-openusd-preflight.yml` runs it alongside Tasks 1–2 tests.

## Boundaries

This is a CPU-only, opt-in installation path. Task 3 does not wire any
automatic startup hook or run a live installation. The shared writable
models/projects proposal remains tracked separately in #71; optional SSH in
#70. No merge or live AWS mutation is authorized by this document.

See [RES EFS audit and adapter](rez-openusd-res-efs-audit.md).
