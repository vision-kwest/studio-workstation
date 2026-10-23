# Studio Workstation — Engineering Charter

## Mission

Extract the working **AWS Research and Engineering Studio (RES) 2026.06** Ubuntu 24.04 workstation provisioning implementation and adapt it for **one standalone EC2 workstation**, retaining CPU and NVIDIA GPU options and browser-based Amazon DCV login, while removing only infrastructure dependencies that a standalone workstation cannot satisfy.

**Source of truth:** `aws/res` Git tag `2026.06`. Pin its resolved commit SHA in the implementation PR before vendoring any code. The successful RES Demo runtime-provisioned workstation is our behavioral reference; do not conflate this with RES-ready AMI prebaking.

## Binding engineering rules

1. **Read upstream first.** Before modifying code, read the relevant RES source files and their callers, sourced shell files, config, and Python dependencies. Cite exact repository paths and upstream tag/commit in PR descriptions.
2. **Minimal-diff adaptation.** Preserve RES package selection, stage order, reboot continuation, GPU/DCV/Xorg behavior, and readiness logic wherever feasible. Every modification must correspond to a documented standalone dependency or demonstrated defect.
3. **No reimplementation by default.** Do not create new installers, bootstrap state machines, DCV managers, or orchestration architectures where RES already has a suitable implementation. Seek review before any replacement.
4. **Separate layers.** Infrastructure may be reduced to EC2, IAM, EBS, networking and lifecycle controls; workstation provisioning is independently maintained and RES-derived. Optional developer, 3D and editorial software layers are out of scope until the base desktop works.
5. **Evidence-driven diagnosis.** Distinguish observed logs and source facts from hypotheses. Do not infer kernel panic from EC2 status checks or infer deadlock from the last log line. Require evidence before a corrective patch.
6. **One change at a time.** Each PR identifies upstream code reused unchanged, exact adaptations, why each is required, and how behavior will be tested.
7. **Validate before spending.** Static checks, dependency analysis, safe IAM/network review and a written test plan precede any paid AWS run. Do not claim AWS tests or successful DCV login unless actually observed.
8. **Secure by default.** No inbound SSH; no passwords or tokens in user data, AMIs, commits or logs; restrict DCV ingress; use least-privilege IAM and encrypted disks. Standard Linux authentication must replace RES external auth explicitly, not silently.
9. **Costs and cleanup.** Prefer one on-demand workstation, stop when idle, avoid persistent control servers and unused resources. Document cleanup and data-loss implications.
10. **Stop on unresolved dependencies.** If a RES component depends on broker, SSSD, EFS, Cognito or another service, document the call site and interface before deciding whether to stub, replace, or remove it.

## Required work order

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
