# RES 2026.06 Deviation Register

**Status: proposed decisions only. No approved standalone code patch set yet.**

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

## Historical warning

Earlier custom Image Builder and v0.6.0 systemd-bootstrap implementations diverged substantially from RES and encountered repeated build failures, including a custom service ordering cycle. These are *not* approved deviations and should not be reused as the canonical implementation.

The register is a planning artifact. No AWS resource or provisioning script is changed by this documentation PR.
