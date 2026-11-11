# Minimum-Change Engineering Playbook

*Reusable process for AI-assisted engineering. [AGENTS.md](AGENTS.md) contains repository-specific invariants; [project brief](PROJECT_BRIEF_WORKSTATION_SOFTWARE.md) names a current objective. Follow this process proportionately—do not turn it into approval theater.*

## First establish the assignment

Before investigating a proposed change, write down:

- **Observable outcome:** What the user can do once the system works. Define acceptance before selecting implementation.
- **System lifecycle:** What is deployed, experimental, retired, or merely proposed? Identify *actual* users, production obligations, data to protect, and operations we may replace. Do **not** invent stakeholders or preserve a proof of concept without a requirement.
- **Authority:** Exactly which repositories, files, AWS/guest resources and operations are approved. Read-only investigation is not approval to launch, mount, install, publish, mutate IAM/SG/EFS, merge or destroy.
- **Issue ownership:** Search existing issues/PRs first. Link the smallest set of outcome-level issues across repositories, and keep future features outside the current scope.

Distinguish **preserving data and security** from **preserving obsolete workflows**. Replace an old process when authorized; do not erase shared data to do so.

## Phase 0 — Evidence and design, without implementation

Inspect current `main`, pinned upstream/platform behavior, existing deployment and config, and relevant AWS inventory. Historical logs establish *what happened then*, not necessarily the current state.

| Deliverable | Required evidence |
|---|---|
| Acceptance contract | Real CPU/GPU or equivalent workflow; failures, reboot, data protection and cleanup |
| Verified baseline | Exact source refs, existing functions, resource IDs, policy and package/artifact stages |
| Unknowns | Separate blocking from nonblocking; cheapest safe proof for each |
| Alternatives | **No code** → native/configuration → existing upstream/repo utility → small custom integration last |
| Decision | Why simpler choices fail; exact ownership boundaries; files changed or code deleted |
| Whole-project estimate | **All** code/config/test LOC, dependencies, engineering through live acceptance, risk and rollback |
| Approval | One concrete decision; no branch or code until approved |

A historical identifier is not itself a blocking dependency. Time-box the investigation when the user is free to retire that workflow. If a material premise changes, revise the design and issue—not an entire subsystem.

## Phase 1 — Approved bounded engineering

- Branch from the approved current `main`, only in repositories that need code changes; keep existing experimental PRs as evidence, not the automatic base.
- Reuse RES, Linux/systemd, AWS EFS mount helpers, existing CI/Rez/promotion utilities; do not reproduce their state machines or add new services without a verified gap.
- Keep each changed subsystem independently explainable, with production/config LOC, test LOC, file counts and an **overall** budget. Count shell scripts, CI YAML, IAM policy documents and infrastructure configuration, not just Python.
- Prefer focused behavior/failure-path tests over many assertions of source strings. Validate interrupted installs, conflicting configuration, retries, reboot entry points and access-denied behavior where relevant.
- Pause and review if complexity grows materially (for example more than ~50 LOC for a simple native-platform operation), a new subsystem appears, risk changes, or a smaller existing feature is found. A budget is an alarm, not a quota.
- Preserve existing reliable behavior; do not modify unrelated files or rewrite historical evidence.

## Phase 2 — Separate supervised live acceptance

Show the exact resources, intended mutations, budgets, deadlines, safety guards and rollback steps *before* seeking approval. Then collect actual evidence: source/package provenance, IAM/SG outcomes, mount identity and rights, native binaries/ABI, human login, persistence, failure recovery and independent cleanup.

**Evidence vocabulary:** `source verified` / `offline tested` / `live accepted` / `unknown`. Passing mocked tests does not prove an EFS mount, IAM enforcement, Rez install, OpenUSD import or DCV login. “Complete” means the user-visible outcome passed end to end.

Avoid per-command approval requests for routine steps already covered by an exact, bounded approval. Stop on unexpected resources, material security or budget changes, data-loss risks or failed independent safeguards.

## Architecture and permissions lessons

- Identify the **publisher** of software separately from its **consumers**. For this studio, historical SSH/NFS builds were proof of concept; the current goal is a dedicated CI writer plus read-only CPU/GPU consumers.
- Cloud IAM EFS client permissions apply at the **filesystem** level; POSIX, access points (if justified), and workflow controls distinguish directories and stages. A client-side `ro` mount is not an enforceable IAM write boundary. Plan future writable projects/models separately.
- GitHub OIDC workflow credentials do not by themselves provide persistent IAM authorization to a running EC2 EFS mount. Verify the actual trusted runner identity, instance profile and continuous credential path.
- Avoid mutable build inputs such as `:latest` for an approved release; verify source, dependency, artifacts and digests before live use.
- Production promotion must be explicit. Assess whether current publisher tools perform an atomic operation; do not create a new publishing framework solely to preempt a hypothetical future requirement.

## Minimal issue / decision format

```text
Outcome / user-visible acceptance:
Current baseline and evidence (dated, exact refs):
Who depends on the old behavior? Production vs proof of concept:
Owners and linked issues/PRs:
Preferred existing mechanism and rejected alternatives:
Smallest approved scope / explicit non-goals:
Production+config LOC, test LOC, files, full effort estimate:
Read-only unknowns / deployment gates / rollback:
What was verified offline vs on real infrastructure:
Next one decision:
```

## Check-in and documentation rule

Report concise **outcome, exact evidence, changed LOC/files versus estimate, safety state, unverified work, and next approval**. Search for existing issues before creating new ones. When a premise or decision changes, reconcile the **current** issue, project brief and architecture decision record; update `AGENTS.md` only when the lesson is a permanent rule.

Keep implementation guides and historical reviews separate: update current operator docs after tested behavior changes; retain the historical report as a dated record. Prefer deleting superseded instructions over writing another parallel guide.
