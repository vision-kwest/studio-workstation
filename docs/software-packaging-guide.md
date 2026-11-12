# Studio Software Packaging Guide

**First-pass tutorial — Issue [#94](https://github.com/vision-kwest/studio-workstation/issues/94).** Adapted from the supplied *Studio Software Packaging Guide* PDF (15 steps). This is a living recipe, **not** evidence that all commands have passed end-to-end live testing. Update commands, blockers, fixes and acceptance evidence as the publisher workflow is exercised.

**Audience:** downstream package maintainers and authorized Studio publishers. **Scope:** immutable Rez software packages for Ubuntu 24.04 CPU/GPU workstations; not workstation creation or GPU Image Builder changes.

## 1. Understand the packaging workflow

```text
Upstream software (release/source/binary)
                |
Downstream package project (build, package.py, manifest, tests)
                |
Studio publisher (authorized build, stage, verify, publish)
                |
EFS / Rez: /opt/studio/packages/{dev,test,prod}
                |
CPU/GPU workstations (read-only Rez consumption)
```

`studio-rez` owns Rez configuration, conventions, validation and promotion. A downstream project owns application-specific preparation and tests. The publisher has authorized write access; workstation identities consume packages read-only. Do not introduce a new package manager or grant publishing rights to workstations.

## 2. Choose the application and version

Write a package specification **before** downloading anything:

| Field | Example (illustrative, not upstream-verified) |
|---|---|
| Application / Rez name | Ollama / `ollama` |
| Version | `0.40.16` |
| Upstream | https://github.com/ollama/ollama |
| Release tag and artifact | **Verify** actual tag and Linux x86_64 asset before use |
| OS / architecture | Ubuntu 24.04 / Linux x86_64 |
| Distribution strategy | Official binary, if verified and compatible |
| Runtime dependencies | Discover with binary inspection and smoke tests |
| GPU dependencies | Decide whether bundled or host-provided |

Use a fixed release and independently verified SHA-256. Never use `latest` as the reproducibility identifier.

## 3. Decide how the application will be built

| Strategy | Use when | Example |
|---|---|---|
| Binary repackaging | Trusted, compatible official Linux release exists | Ollama / FFmpeg |
| Source compilation | Custom features, ABI or architecture require compilation | OpenUSD |
| Python packaging | Python module and entry points are primary deliverables | Python tools |

Record build dependencies (compiler, CMake, Go, Python, Docker image digest) **only where applicable**. For GPU software, document CUDA/driver and library compatibility separately. Do not bundle workstation NVIDIA drivers by default.

## 4. Create the downstream project

Example structure; adapt to the **current** Studio downstream repository and publisher tooling:

```text
studio-example/
├── package.py
├── build.py
├── README.md
├── scripts/
│   ├── fetch_upstream.sh
│   └── smoke_test.sh
└── tests/
```

Generate `manifest.json` alongside the staged install. Reuse `studio-rez` helpers rather than duplicating the common publishing framework.

## 5. Write `package.py`

```python
name = "ollama"
version = "0.40.16"
description = "Ollama local model runtime"
requires = []

def commands():
    env.PATH.prepend("{root}/bin")
```

The package definition describes the Rez environment; it **does not** download software or start system services. Add `PYTHONPATH`, `LD_LIBRARY_PATH`, or other variables only when the actual install layout needs them. Determine real Rez dependency requirements before release.

## 6. Acquire and verify upstream software

```bash
# Supply verified values from the chosen release; these are NOT filled in for you.
test -n "$UPSTREAM_URL" && test -n "$EXPECTED_SHA256"
curl -fL "$UPSTREAM_URL" -o release.tar.gz
printf '%s  %s\n' "$EXPECTED_SHA256" release.tar.gz | sha256sum -c -
```

Inspect the archive and stage the needed executables, libraries and resources locally:

```text
stage/
├── package.py
├── bin/
├── lib/
├── share/
└── manifest.json
```

Never blindly run upstream installers that alter `/usr/local`, systemd, accounts or GPU drivers. Verify the real archive layout rather than assuming every application uses these folders.

## 7. Record provenance and dependencies

Example manifest (placeholders are **not** publishable values):

```json
{
  "name": "ollama",
  "version": "0.40.16",
  "platform": "linux-x86_64",
  "source_type": "upstream_binary",
  "source_url": "<verified-release-url>",
  "source_sha256": "<verified-sha256>",
  "package_format": "rez"
}
```

For source builds also record source commit, builder image digest, compiler, options and dependencies. Keep secrets and credentials out of the manifest.

## 8. Validate using `studio-rez`

With the downstream project and `studio-rez` checked out as siblings:

```bash
cd ~/studio-example
source ../studio-rez/env/studio-dev.sh
studio-rez-doctor --soft
studio-rez-validate-package .
python3 -m pytest -q
```

The pytest command assumes tests exist and pytest is installed. Rez and its environment are required **where packaging runs**; do not assume AWS CloudShell has Rez. These checks validate metadata and project logic, not production runtime behavior.

## 9. Test the package locally

```bash
./stage/bin/ollama --version
ldd ./stage/bin/ollama
```

These are Ollama-specific examples; substitute the application's actual executable. Inspect missing shared libraries, executable permissions, data/resource lookup, Python imports and GUI launch as appropriate. Test CPU and GPU execution independently.

## 10. Publish to the development repository

```text
/opt/studio/packages/
├── dev/
├── test/
├── prod/
└── archive/
```

The `studio-rez` downstream guide documents, for a correctly configured Rez build project:

```bash
rez-build --install --prefix /opt/studio/packages/dev
```

**Important:** This is **not** a universal substitute for the current Studio publisher workflow. The `studio-openusd` downstream project uses staged, verified, atomic publication into EFS; reconcile the canonical CLI/publisher commands during live acceptance and replace this generic example where needed. Publish only as the authorized publisher. Expected illustrative path: `/opt/studio/packages/dev/ollama/0.40.16`.

**Never overwrite an existing package version** (including an existing Ollama 0.40.16). If it exists, inspect and compare manifests/checksums, then stop or choose a deliberate new version.

## 11. Test the published Rez package

```bash
source ../studio-rez/env/studio-dev.sh
rez-env ollama-0.40.16 -- ollama --version
```

Check that Rez resolves the intended stage and version, paths point to the published artifact, and the actual application runs. For Ollama, test server startup, writable model storage **outside** the immutable Rez tree and CPU/GPU inference. `--version` alone is insufficient.

## 12. Promote through test and production

After acceptance in `dev`:

```bash
studio-rez-promote-package ollama 0.40.16 dev test
# Validate test stage before proceeding.
studio-rez-promote-package ollama 0.40.16 test prod
```

These are documented `studio-rez` commands; verify the current publisher's promotion prerequisites and permissions during live testing. Promotion must preserve immutable content and refuse destination overwrite.

## 13. Use the application on a Studio workstation

```bash
source /path/to/studio-rez/env/studio-prod.sh
rez-env ollama-0.40.16 -- ollama --version
```

Use the installed `studio-rez` path appropriate to that workstation. Verify read-only EFS access, resolve behavior and runtime on CPU/GPU as relevant. Application services, writable state and optional GUIs are separate runtime configuration, not implicit package installation.

## 14. Packaging acceptance checklist

- [ ] Upstream version, artifact and checksum verified
- [ ] Strategy, dependencies and GPU compatibility documented
- [ ] Rez `package.py` created and validated
- [ ] Artifact staged without modifying system software
- [ ] Studio validator and downstream tests pass
- [ ] Local CPU smoke test passes (GPU separately where applicable)
- [ ] Immutable artifact published to `dev` with publisher identity
- [ ] Published Rez resolve and runtime tested in `dev`
- [ ] `dev → test → prod` promotions verified without overwrite
- [ ] Production CPU/GPU workstation consumption accepted

**Live testing ledger:** For each numbered step, record date, exact command, host/profile, expected and actual results, evidence, blocker issue, fix PR, retest, and doc correction in [Issue #94](https://github.com/vision-kwest/studio-workstation/issues/94). Distinguish **documented**, **offline tested**, and **live verified**.

## 15. Reference documentation

- [studio-rez standards](https://github.com/vision-kwest/studio-rez/blob/main/README.md)
- [studio-rez downstream usage](https://github.com/vision-kwest/studio-rez/blob/main/docs/downstream-usage.md)
- [Rez package template](https://github.com/vision-kwest/studio-rez/blob/main/templates/package.py.template)
- [studio-openusd downstream implementation](https://github.com/vision-kwest/studio-openusd)
- [Studio publishing/consumption architecture](adr/0001-software-publishing-and-consumption.md)
- [Issue #94 — guide and live blockers](https://github.com/vision-kwest/studio-workstation/issues/94)

> **First-pass status:** This tutorial captures the supplied recipe and existing conventions. It does **not** assert that the generic build/install commands have been reconciled with, or accepted through, the current Studio publisher end-to-end. Update it with real tested commands as the workflow is exercised.
