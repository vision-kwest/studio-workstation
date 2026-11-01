# Studio Workstation — Standalone RES Compatibility Decisions

> **Historical design decision snapshot (October 22, 2026):** The gates and permissions discussions below document what was required *at that point*. The minimal standalone RES adapter, CPU/GPU desktop, GPU CLI stop/start and teardown were subsequently validated, and PR #65 was merged. See [current status](../README.md#status-after-merged-pr-65), [Studio CLI](studio-cli.md) and [Issue #46](https://github.com/vision-kwest/studio-workstation/issues/46) for completed acceptance. New changes still require engineering review.

**Status:** Design direction accepted by project owner; **implementation and AWS deployment NOT authorized**.
**Upstream reference:** `aws/res` tag `2026.06`. Governed by [AGENTS.md](../AGENTS.md), [RES_COMPATIBILITY.md](RES_COMPATIBILITY.md), and [DEVIATIONS.md](DEVIATIONS.md).

## Recorded decisions (2026-10-22)

| Decision | Accepted direction | Approval boundary |
|---|---|---|
| 1 — Authentication | **YES:** local Linux account and Amazon DCV system authentication, retaining upstream console ownership and permissions behavior wherever possible | D-001/D-002 are design-approved only; exact PAM, DCV config, TLS, permission-file policy, and account provisioning require review and tests |
| 2 — Python initialization | **CONDITIONAL:** reuse upstream `ideavirtualdesktop.app.linux.bootstrap.run()` and `post_reboot.run()` without the full `ResApp` event loop if independent initialization can be proven | D-004 remains conditional; do not bypass required imports, environment setup, state handling, or readiness semantics |
| 3 — Package delivery | **YES:** retain original RES installation-scripts and virtual-desktop application archives; use explicit, pinned URIs and a narrowly scoped EC2 instance profile instead of RES broker and DynamoDB discovery | D-005 is design-approved only; object availability, provenance, checksums, access policy and encryption must be verified |

**Not approved:** code edits, standalone installer replacements, new orchestration or systemd state machines, CloudFormation deployment, Image Builder runs, EC2 launches, IAM changes, or production use. No decisions are being made here to remove GPU/DCV/Xorg/GNOME logic. D-006 and D-007 remain preservation requirements.

## Remaining validation gates (must be met before an implementation PR is approved)

### G1 — Upstream reproducibility and package provenance
- Resolve and record the exact commit SHA behind `aws/res:2026.06`.
- Confirm immutable archive version, exact source, package contents, license, integrity hash/signature (where available), and retrieval method for both RES archives.
- Verify a standalone EC2 role can access the exact S3 objects, or document a source-derived build/copy plan. Restrict `s3:GetObject` to specific objects; add `kms:Decrypt` only when required.
- No AWS download or policy mutation until separately authorized.

### G2 — Python invocation contract (conditional decision 2)
- Trace and enumerate all required environment variables, configuration files, package imports, logging initialization and `ResApp`/`app_main.configure_environment()` side effects.
- Show a file/function-level proposed diff that isolates RES-only control-plane calls from `bootstrap.run()` and `post_reboot.run()` without replacing their DCV initialization behavior.
- Validate importability and initialization in an isolated, non-production environment without live DynamoDB, broker, or SSSD dependencies; record failures and residual dependencies.
- Preserve RES semaphore, cloud-init reexecution and reboot behavior.

### G3 — Authentication and console authorization (decision 1)
- Check the actual installed Amazon DCV version's documented `authentication=system` behavior and PAM prerequisites.
- Review a concrete local-owner permissions file against the upstream `%owner%` semantics; prove no unintended guest/admin privileges.
- Define secure first-user/password provisioning without putting secrets in user data, AMIs, commits or logs.
- Validate automatic console ownership, storage-root permissions, TLS certificate/trust, X authorization and restricted TCP/UDP 8443 access.

### G4 — Precise adaptation review
- Update [DEVIATIONS.md](DEVIATIONS.md) with exact upstream functions, original behavior, smallest proposed diffs, security impact and acceptance criteria for D-001 through D-005.
- Preserve D-006 NVIDIA/CPU DCV/Xorg/GNOME installation and D-007 cloud-init/semaphore/reboot semantics unchanged unless a separately reviewed incompatibility is demonstrated.
- Obtain explicit approval of the file-level patch specification **before** changing provisioning code.

## Later runtime acceptance gates (only after separate implementation/deployment authorization)

These are **not yet completed** and do not authorize cloud spending:

1. Static checks: shell/Python syntax, dependency/import tests, IAM least privilege, template linting and secret scanning.
2. Single EC2 runtime bootstrap from pinned Ubuntu 24.04 AMI; observe original RES stage order and reentry after both reboots.
3. CPU workstation: GNOME/Xorg, DCV service/console readiness, real browser login with local account, restart and stop/start.
4. GPU workstation: NVIDIA T4 driver and `nvidia-smi`, Xorg NVIDIA renderer, accelerated DCV desktop, reboot and stop/start.
5. Verify security, costs, cleanup, storage persistence and failure diagnostics; no extra RES control-plane resources.

## Review state

The three design choices are recorded, but **no implementation authorization is implied**. If any gate fails, return to design review with evidence instead of silently introducing a replacement architecture.
