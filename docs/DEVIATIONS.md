# RES 2026.06 Deviation Register

> **Historical register with continuing relevance:** The numbered RES deviation reviews here preserve their original design and review chronology. Some early items described proposed changes that were subsequently implemented and validated. For the accepted live runtime and current operator commands, refer to [runtime provenance](../runtime/README.md) and [Studio CLI](studio-cli.md); do not use old approval notes as current deployment gates.

**Status: design choices recorded, including approved Option A manual SSM credential enrollment. No approved standalone code patch set yet.**

Every change from the pinned upstream RES commit must be recorded here *before* implementation. Do not treat a proposed removal as proven safe.

| ID | Upstream path / behavior | Proposed change | Necessity | Evidence required | Status |
|---|---|---|---|---|---|
| D-001 | RES Cognito/AD/SSSD and DCV external auth | Replace with Linux account + DCV `authentication=system` | No RES directory or broker for one machine | Trace auth config generation, PAM behavior, session permissions and successful DCV login | **Proposed** |
| D-002 | RES connection gateway, broker, ALB and session URLs | Direct restricted DCV port 8443 | Eliminate RES management infrastructure | Trace DCV server settings, TLS and session lookup requirements | **Proposed** |
| D-003 | RES EFS/FSx/NFS/S3 mount integration | Encrypted local EBS only | Single-machine storage | Confirm installer and Python bootstrap do not require mounts | **Proposed** |
| D-004 | RES cluster/session agent lifecycle | Retain minimal console startup/readiness behavior; remove RES-only state reporting | No RES control plane | Full Python and systemd dependency graph | **Needs investigation** |
| D-005 | RES CloudFormation/cluster orchestration | One EC2 instance, role, SG and EBS | Reduce cost and complexity | Confirm all bootstrap inputs and instance metadata requirements | **Proposed** |
| D-006 | RES GPU/DCV installation | Preserve upstream code and configuration | Fidelity to working demo | Test NVIDIA T4 and CPU headless variants | **No deviation intended** |
| D-007 | RES runtime reboot/continuation | Preserve original logic; do not invent new systemd state machine | Avoid prior ordering-cycle regression | Trace exact user-data/systemd/cron handoff | **No deviation intended** |

## Gate 3 and Gate 4 review references

- [Gate 3 security review](GATE_3_SECURITY_REVIEW.md): Option A first-login activation is approved as a design; exact historical DCV version, PAM, TLS and runtime authorization remain open.
- [Gate 4 minimal patch specification](GATE_4_MINIMAL_PATCH_SPEC.md): file/function-level proposed adaptations for D-001–D-005; D-006 and D-007 must remain unchanged. Exact unified diffs and explicit implementation approval are still required.

## Entry template

For every additional deviation, document:

- **ID and upstream file/function/line range:**
- **Exact original behavior:**
- **Minimal replacement and patch diff:**
- **Standalone dependency requiring the change:**
- **Alternatives rejected and why:**
- **Evidence and tests:**
- **Security and cost effects:**
- **Reviewer approval and date:**

## Shared Comfy default path (Issue #131)

The shipped Comfy template uses name `StudioComfyUI` and path `/studio/apps/comfyui/main/StudioComfyUI`. Deployment uses the existing `--set-default` selector to capture actual registration or manifest metadata. This is a name/path change; the existing helper behavior and RES 2026.06 remain unchanged.

## Desktop administrator access (Issue #144)

The renderer adds the selected CPU/GPU desktop account to Ubuntu's native `sudo` group after account creation, including an existing-account bootstrap retry. Initial password locking/enrollment and other group memberships are preserved. Ubuntu's password-required sudo policy is reused. RES 2026.06 commit `1f526a241fb293a29fb018f08ccf81c100c05a42` and its standalone bootstrap, driver handling, DCV and reboot continuation remain unchanged; the standalone path already skips directory-service sudoers configuration. Live desktop password authentication remains a separate acceptance check.

## Larger GPU workstation size (Issue #149)

The existing persistent GPU profile also accepts `g4dn.2xlarge` in the CLI,
CloudFormation parameters and inventory ownership checks, including inventory's
legacy profile inference. `g4dn.xlarge` remains the default; the historical Task 7
temporary renderer and launcher retain their original size restriction. Both
sizes use the same RES 2026.06 commit `1f526a241fb293a29fb018f08ccf81c100c05a42`
GPU-family path, pinned archives, AMI, storage and native stop schedules. No
upstream provisioning code changes. Issue #149 records validation results and
remaining live acceptance.

## Historical warning

Earlier custom Image Builder and v0.6.0 systemd-bootstrap implementations diverged substantially from RES and encountered repeated build failures, including a custom service ordering cycle. These are *not* approved deviations and should not be reused as the canonical implementation.

The register is a planning artifact. No AWS resource or provisioning script is changed by this documentation PR.

## Issue #100 — restoring RES runtime GPU installation (draft)

Against upstream `aws/res` **2026.06** commit `1f526a241fb293a29fb018f08ccf81c100c05a42`: retain `scripts/dcv/linux/gpu_drivers.sh`, `virtual-desktop-host/linux/install.sh` and `install_post_reboot.sh` stage order. Replace only the Studio CLI’s custom baked GPU AMI selection with the existing verified Canonical 24.04 parent AMI. Retain upstream RES's signed `aws s3 cp --quiet --recursive` command unchanged. The missing role dependency was restored through IAM inline policy `StudioRes202606NvidiaGridRead`: `s3:ListBucket` for the distribution's `latest/` prefix and `s3:GetObject` for the exact 595.91.07 file; unrelated prefixes/objects are denied. One minimal archive adaptation adds SHA-256 verification (`cae8d82b8407c6371805485b148aa667550abf1775ea7999be69da81cf593156`) immediately after the upstream download, returning nonzero on missing/bad content. A standalone NVIDIA post-reboot check requires the driver executable and `nvidia` module for the active kernel before RES writes its ready marker. The 2026-11-15 guest test confirmed signed S3 List/HEAD was denied and unsigned read succeeded; the old archive failed in `gpu_drivers.sh` at 01:35:37 UTC. **Published candidate, not live accepted:** installation script at `releases/issue100-res-native-20261115/res-2026.06/installation-scripts.tar.gz`, VersionId `sgj5wZfqMTRUImWDDbxR08emK755aU7A`, SHA-256 `6d3187049ecee202167cc462492431e3cb9a2a22d78300a8189db3fa2924ee7d` (42,994 bytes), produced reproducibly from pinned RES sources by the existing CloudShell release tool. The old PR #65 installer and application archives remain unchanged.

## Comfy helper argument validation (Issue #142)

The existing Studio Comfy helper now strictly parses its own options before configuration access or app launch. Native AppImage arguments follow an explicit `--` separator. This prevents a mistyped option from starting Desktop and retains normal launch, default selection and update behavior. RES 2026.06 commit `1f526a241fb293a29fb018f08ccf81c100c05a42`, provisioning, Comfy Desktop, shared templates and local-state handling are unchanged.

## Ubuntu desktop update prompts (Issue #137)

The renderer creates a native per-user XDG `Hidden=true` override for
`update-notifier.desktop` and masks Ubuntu Noble's `update-notifier-release.path`
and `.service` for each new CPU/GPU desktop account. The masks affect the GUI
release notification, not manual upgrades or APT security-update policy. These
preferences are local and precede the first GNOME session. RES 2026.06 commit
`1f526a241fb293a29fb018f08ccf81c100c05a42` installers, driver handling and reboot
continuation remain unchanged; this does not resolve later kernel/driver mismatch.

## Studio application shell (Issue #140)

The optional software layer adds `studio env`, a child Bash shell, and small uppercase wrappers for the existing shared application paths. The existing Comfy helper and its registration/default/update behavior are reused unchanged. OpenCode seeds only an individual global configuration link; application databases and service state remain local. OLLAMA resolves the existing Rez package and shared models without starting a server.

The post-DCV setup creates the user-owned shared command/project directories, adds the local `studio` symlink and installs Ubuntu's `ripgrep` alongside the existing menu utilities. These precede optional Rez. RES 2026.06 commit `1f526a241fb293a29fb018f08ccf81c100c05a42` installers, DCV/GPU behavior, reboot continuation and workstation lifecycle remain unchanged. Source verification and offline checks do not establish live desktop acceptance; the implementation PR records that evidence separately.
