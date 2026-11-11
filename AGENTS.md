# Studio Workstation — Engineering Charter

For reusable review steps see [ENGINEERING_PLAYBOOK.md](ENGINEERING_PLAYBOOK.md); the current software-delivery scope is [Issue #74](https://github.com/vision-kwest/studio-workstation/issues/74) and the publisher scope is [studio-openusd #8](https://github.com/vision-kwest/studio-openusd/issues/8).

> **Implementation status (after merged PR #65):** The RES 2026.06 standalone adapter, CPU/DCV, GPU/Tesla T4/DCV and scoped Studio CLI GPU create/login/stop/start/persistence/cleanup have passed live acceptance. The work order below describes **how to review future changes**, not gates still preventing the completed PR #65. The GPU Image Builder pipeline remains disabled on older recipe 0.1.10, while GPU AMI 0.1.11/1 was successfully built separately. Current operator commands: [Studio CLI](docs/studio-cli.md); [documentation index](docs/INDEX.md).

## Mission

Extract the working **AWS Research and Engineering Studio (RES) 2026.06** Ubuntu 24.04 workstation provisioning implementation and adapt it for **one standalone EC2 workstation**, retaining CPU and NVIDIA GPU options and browser-based Amazon DCV login, while removing only infrastructure dependencies that a standalone workstation cannot satisfy.

**Source of truth:** `aws/res` Git tag `2026.06`. Pin its resolved commit SHA in the implementation PR before vendoring any code. The successful RES Demo runtime-provisioned workstation is our behavioral reference; do not conflate this with RES-ready AMI prebaking.

## Binding engineering rules

1. **Read upstream first.** Before modifying code, read the relevant RES source files and their callers, sourced shell files, config, and Python dependencies. Cite exact repository paths and upstream tag/commit in PR descriptions.
2. **Minimal-diff adaptation.** Preserve RES package selection, stage order, reboot continuation, GPU/DCV/Xorg behavior, and readiness logic wherever feasible. Every modification must correspond to a documented standalone dependency or demonstrated defect.
3. **No reimplementation by default.** Do not create new installers, bootstrap state machines, DCV managers, or orchestration architectures where RES already has a suitable implementation. Seek review before any replacement.
4. **Separate layers.** Infrastructure, RES-derived workstation provisioning, and optional developer/creative software are distinct concerns. The CPU/GPU/DCV base has passed live acceptance; additional software requires its own approved scope and real acceptance, not another redesign of the desktop.
5. **Evidence-driven diagnosis.** Distinguish observed logs and source facts from hypotheses. Do not infer kernel panic from EC2 status checks or infer deadlock from the last log line. Require evidence before a corrective patch.
6. **One change at a time.** Each PR identifies upstream code reused unchanged, exact adaptations, why each is required, and how behavior will be tested.
7. **Validate before spending.** Static checks, dependency analysis, safe IAM/network review and a written test plan precede any paid AWS run. Do not claim AWS tests or successful DCV login unless actually observed.
8. **Secure by default.** No public SSH ingress by default; optional developer SSH is separately scoped under #70 and must never be a DCV prerequisite. No secrets in user data, AMIs, commits or logs; restrict DCV ingress, use least-privilege IAM and encrypted disks.
9. **Costs and cleanup.** Prefer one on-demand workstation, stop when idle, avoid persistent control servers and unused resources. Document cleanup and data-loss implications.
10. **Stop on unresolved dependencies.** If a RES component depends on broker, SSSD, EFS, Cognito or another service, document the call site and interface before deciding whether to stub, replace, or remove it.
11. **Verify compatibility obligations.** Classify old implementations as production, supported, experimental or retired. Identify actual users/dependencies before spending time preserving historical behavior; never invent production stakeholders. A proof of concept may be replaced with approval, but shared data and unrelated resources remain protected.
12. **Reuse and respect ownership.** Prefer existing OS/AWS/RES and Rez/CI capabilities over adapters. Keep EFS publisher writes, workstation software reads and later writable project/model data distinct; IAM EFS rights are filesystem-wide, not per directory. See [ADR-0001](docs/adr/0001-software-publishing-and-consumption.md) and #71.
13. **Track work before coding.** Search existing issues first; use one issue per meaningful owned outcome, not per helper or test. Record dependencies, evidence, whole-project effort, production/config/test LOC budget, security and live-acceptance gates; source contracts must not be mistaken for live proof.
14. **Stop on growth, not on necessary checks.** For a simple platform operation, roughly 50+ new production/config LOC triggers explicit simplification review. Count shell, workflow, IAM and infrastructure config too. Do not defend sunk costs or silently expand scope.

## Required work order for RES/bootstrap changes

For other software-delivery work, follow the lighter [engineering playbook](ENGINEERING_PLAYBOOK.md); do not repeat a full RES compatibility audit when the change does not touch that subsystem.

1. Trace complete RES 2026.06 runtime bootstrap call graph (including user data, scripts, Python service initialization and reboot reentry).
2. Populate `docs/RES_COMPATIBILITY.md` with confirmed reuse/removal decisions and outstanding unknowns.
3. Propose a small patch plan and record every change in `docs/DEVIATIONS.md`.
4. **Request review of the dependency map and deviation plan before implementing provisioning code.**
5. Implement only approved adaptations in a separate PR; test incrementally against RES behavior.

## Pull request checklist (mandatory for implementation)

- [ ] Exact upstream tag and resolved commit SHA recorded
- [ ] Upstream files/functions and call graph linked
- [ ] Unchanged code identified
- [ ] Each adaptation recorded in `docs/DEVIATIONS.md`
- [ ] No unnecessary RES infrastructure dependency introduced
- [ ] Ubuntu 24.04 CPU/GPU and DCV behavior impacts stated
- [ ] Static validation performed; results attached
- [ ] AWS test plan, budget exposure, cleanup, and rollback documented
- [ ] Manual DCV browser login remains an explicit acceptance gate
- [ ] No unapproved architectural substitution

## Existing work

Prior Image Builder and custom runtime bootstrap PRs are **experimental history, not canonical RES ports**. Do not silently build on them. This charter supersedes their design assumptions, not the source evidence in their logs.
