# Documentation index

**Current operating instructions** are separated here from current project plans and historical acceptance records. PR #65 and PR #67 are merged; software-delivery PR #73 and OpenUSD publisher PR #7 remain drafts (status checked 2026-11-05). When an archived note says "NO-GO", "not merged", "not deployed", or "pending", read it as a **statement from the date of that checkpoint**, not a current project-wide prohibition.

## Start here — current information

| Page | Purpose |
|---|---|
| [Main README / Quick Start](../README.md) | Project state, first commands, key costs and access risks |
| [Software packaging guide](software-packaging-guide.md) | **15-step tutorial** for preparing, validating, publishing, promoting and consuming Rez applications; living Issue #94 acceptance recipe |
| [Studio CLI](studio-cli.md) | **Canonical create, preview, check-ready, connect, stop, start, destroy commands** |
| [GPU workstation profile](gpu-workstation-profile.md) | Validated Tesla T4 / RES 2026.06 GPU runtime and pinned limits |
| [GPU runtime provenance](../runtime/README.md) | Exact derived archive keys, versions, SHA-256 and acceptance evidence |
| [Workstation lifecycle](workstation-lifecycle-visual.md) | EBS, stop/start, changed public IP, cleanup |
| [Architecture](visual-architecture.md) | Image creation versus running EC2 workstation |
| [CPU lifecycle/TLS](cpu-acceptance-lifecycle-tls.md) | CPU live-acceptance record and security background |
| [Release tooling](../scripts/README.md) | Release building/publication, historical paths and updated version-read caveat |

## Trusted DCV hostname (PR #67 merged; acceptance history below)

- [Merged PR #67](https://github.com/vision-kwest/studio-workstation/pull/67) implemented scoped ACM/TLS, CPU/GPU shared hostname and guarded EIP handoff; supervised CPU/GPU trusted browser login and cleanup were observed. Exact GPU expiration enhancements remain in [Issue #68](https://github.com/vision-kwest/studio-workstation/issues/68) / draft [PR #69](https://github.com/vision-kwest/studio-workstation/pull/69).
- [Issue #43 — EIP, ACM and AWS Workload Credentials Provider](issue43-acm-dcv.md) and [CPU/GPU shared-hostname design](issue43-shared-cpu-gpu-handoff.md) retain their dated **pre-merge** proposals and acceptance checkpoints; statements such as “unapproved” were true at their original review dates, not today.
- The original Lambda/EventBridge/DDNS approach was retired by PR #67. Always consult the [current Studio CLI](studio-cli.md) rather than treating old design gates as live operating instructions.

## Engineering standards and current software delivery

- [Repository charter](../AGENTS.md) — permanent RES/DCV/safety boundaries and review expectations.
- [Minimum-change engineering playbook](../ENGINEERING_PLAYBOOK.md) — reusable assignment, evidence, estimation, approval and scope-control method.
- [Current workstation software brief](../PROJECT_BRIEF_WORKSTATION_SOFTWARE.md) and [ADR-0001: EFS publisher/consumer architecture](adr/0001-software-publishing-and-consumption.md) — accepted design, explicit dependencies and live definition of done.
- Implementation ownership: [workstation Issue #74](https://github.com/vision-kwest/studio-workstation/issues/74) / draft [PR #73](https://github.com/vision-kwest/studio-workstation/pull/73); [OpenUSD publisher Issue #8](https://github.com/vision-kwest/studio-openusd/issues/8) / draft [PR #7](https://github.com/vision-kwest/studio-openusd/pull/7). **Offline implementation does not mean live accepted.**
- Future shared writable models/projects remain [Issue #71](https://github.com/vision-kwest/studio-workstation/issues/71), separate from read-only production software consumption.

## Historical source audits and design gates

These are preserved evidence/review documents rather than CLI instructions:

- [RES compatibility map](RES_COMPATIBILITY.md), [standalone decisions](STANDALONE_DESIGN_DECISIONS.md), [deviation register](DEVIATIONS.md).
- [Gate 1 archive verification](GATE_1_RES_ARTIFACTS.md); [Gate 2 Python validation](GATE_2_PYTHON_VALIDATION.md); [Gate 3 DCV auth design](GATE_3_DCV_AUTH_DESIGN.md) and [security review](GATE_3_SECURITY_REVIEW.md).
- Gate 4 [bootstrap handoff](GATE_4_BOOTSTRAP_HANDOFF_AUDIT.md), [Cognito/PAM/NSS](GATE_4_COGNITO_PAM_NSS.md), [minimal patch proposal](GATE_4_MINIMAL_PATCH_SPEC.md), and [proposed source diffs](GATE_4_PROPOSED_SOURCE_DIFFS.md).
- [RES Ubuntu provisioning audit](res-2026.06-provisioning-audit.md), [RES GPU / Image Builder audit](res-gpu-imagebuilder-audit.md), [CPU Image Builder hang evidence](image-builder-hang-investigation.md).

## Image Builder and AMI build records

- [CPU/GPU building visual](gpu-image-builder-visual.md) (current outcomes plus older milestone labels).
- [GPU component generator](gpu-imagebuilder-components.md), [GPU 0.1.1 review](gpu-imagebuilder-v011-preflight.md), [GPU 0.1.11 offline review](imagebuilder-0111-review.md).
- Historical/manual CPU plans: [CPU experimental v0.2](../image/cpu/BUILD.md), [CPU v0.4 automated build](../image/cpu/AUTOMATED_BUILD.md).
- **Current GPU AMI:** `ami-027c98f85b155c2ac` was validated at 0.1.11/1, but the GPU Image Builder **pipeline** is still disabled and pointing to recipe 0.1.10. Image availability and pipeline configuration are different facts.

## PR #65 and Task 7 test records (historical)

- [PR #65 GPU CLI predeployment gate](pr65-gpu-cli-acceptance-gate.md) and [one-shot guard evolution](pr65-minimal-expiration-guard.md).
- [Original proposed Lambda/DynamoDB cleanup mechanism](gpu-cli-test-cleanup.md) — **not the safeguard deployed for the successful one-shot test**.
- [PR #65 installer S3 publication plan](pr65-s3-publication-plan.md) and [earlier read-only preflight](pr65-s3-final-readonly-preflight.md).
- [Task 7 GPU supervised test](../runtime/TASK7_TEST_PLAN.md).
- **Final record:** [Issue #46](https://github.com/vision-kwest/studio-workstation/issues/46) and merged [PR #65](https://github.com/vision-kwest/studio-workstation/pull/65).

## Additional test documentation

- [Gate 2 isolated import test](../tests/README_GATE2_T2.md) — a historical, isolated-runner audit procedure, not an AWS workstation command.

## Guidance for future edits

Update **this index, the main README and `docs/studio-cli.md` together** whenever the user-facing CLI or AWS resource lifecycle changes. Keep the archived gate/review reports as dated evidence and add a current-status banner rather than silently rewriting what happened at the time.
