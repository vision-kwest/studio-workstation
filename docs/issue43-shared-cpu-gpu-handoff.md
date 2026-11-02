# Issue #43 — CPU/GPU shared static DCV hostname (OFFLINE implementation; LIVE DISABLED)

**Status:** An offline, fail-closed handoff workflow exists only in
[draft PR #67](https://github.com/vision-kwest/studio-workstation/pull/67).
**No GPU has been started or equipped with the certificate by this phase.
No EIP reassociation, IAM, DNS, ACM, Image Builder, RES or Scheduler changes
were made. The CPU is still the sole live hostname owner and remains stopped.**

The user's goal is **one hostname** for whichever Studio Workstation is
active, CPU or GPU, not two simultaneous workstations behind one address.

## Simple topology

```
dcv.kwesidavis.com:8443  -> Route 53 A 54.159.180.104 (UNCHANGED)
                                   |
                         Elastic IP allocation
                         eipalloc-0879a34aa1a1298dd
                                   |
                 one-at-a-time EC2 association
                      /                      \
               CPU (currently)           GPU (proposed)
             approved/stopped            only after
              DCV 2026.0             ACM/DCV readiness
                      \                      /
                     same exportable ACM certificate
                  b753beee-5659-4311-a52f-39326b7b3243
```

Route 53, the EIP allocation and ACM certificate do **not** change on
handoff. Reassociation is the only networking mutation. The old source
must be stopped, and the new owner must be prepared before a transfer.
No load balancer, NAT service, DDNS Lambda, second public IPv4 or
second certificate is needed for **one workstation at a time**.

## Current live resources — read-only inventory

| Resource | Approved value |
| --- | --- |
| AWS | `401233741942`, `us-east-1` |
| Hostname | `dcv.kwesidavis.com` |
| Public zone | `Z02607163QPWX22FZ2Q9M` |
| A record | `54.159.180.104`, TTL 300 |
| Retained EIP | `eipalloc-0879a34aa1a1298dd` |
| CPU owner | `studio-workstation-cpu-v013` / `i-0bbfbdc878d3a2320` |
| CPU primary ENI | `eni-0719a8758ddd51d4c` |
| Public ACM certificate | `arn:aws:acm:us-east-1:401233741942:certificate/b753beee-5659-4311-a52f-39326b7b3243` |
| CPU certificate IAM | `StudioIssue43ExactInstanceAssume` / `studio-issue43-dcv-acm-export` |
| GPU source | Pinned RES 2026.06 `ami-027c98f85b155c2ac`; `g4dn.xlarge` |
| GPU scope permitted by current CLI | `studio-gpu-cli-accept65`, independently guarded by PR #65 |
| Status | CPU EIP and TLS accepted; GPU handoff **not** live approved |

The EIP is **separate from the CPU CloudFormation stack**. Keep it
allocated across CPU/GPU profile switching. Retire/release it only after
separate approval and removal of the exact DNS A record.

## Offline implementation

- [`runtime/issue43_hostname_handoff.py`](../runtime/issue43_hostname_handoff.py)
  contains strict read-only plans, a review-only per-GPU-instance IAM
  policy renderer, GPU guest TLS/RES readiness proof, exact GPU
  Scheduler/EBS checks, EIP handoff with rollback, and GPU→CPU return
  after GPU stops or terminates.
- [`scripts/studio_workstation_cli.py`](../scripts/studio_workstation_cli.py)
  exposes these explicit operations:
  ```bash
  # Read only; requires the exact guarded GPU stack and working AWS read rights
  python3 studio workstation hostname-plan

  # No AWS calls; prints exact GPU instance-scoped IAM proposal
  python3 studio workstation hostname-iam --gpu-instance-id i-REPLACE

  # Read-only return plan after GPU is stopped/terminated
  python3 studio workstation hostname-return-plan --gpu-instance-id i-REPLACE

  # Hard-disabled until a new approved live gate changes source code
  python3 studio workstation hostname-handoff --execute
  python3 studio workstation hostname-return --gpu-instance-id i-REPLACE --execute
  ```
- `LIVE_HANDOFF_ENABLED = False` in the module. `--execute` does not
  bypass this code-level gate. No automatic EIP reassignment on
  `create`, `start`, `stop`, `destroy` or a Scheduler event.
  Existing GPU launch and expiration safeguards remain **untouched**.

### Prerequisite A — supervised GPU creation (separate future approval)

The GPU must be started by the **existing PR #65 guarded launch path**,
not a new unguarded or permanent instance path. Existing CLI restrictions
still apply (exact stack/token, GPU AMI, instance type, subnet/profile,
100-GiB encrypted persistent root disk and expiration schedules).

The handoff preflight independently checks the approved GPU instance ID,
stack name, exact `StudioStack` and `StudioCleanupToken` tags, root
EBS ID and retained-disk ownership/attachment, and both independent
EventBridge schedules:

- `studio-pr65-i-<gpu-instance-id>`: enabled exact EC2 termination;
- `studio-pr65-v-<gpu-root-volume-id>`: enabled exact EBS cleanup;
- existing `studio-task7-pr61` group, scoped Scheduler execution role,
  configured retries/target and correct immutable payloads;
- at least **30 minutes remain** before termination, a deadline
  **no more than 240 minutes away** (the original PR #65 guard bound),
  and EBS cleanup 30–120 minutes after termination.

This 30-minute threshold is a *minimum safety gate*, not authorization
to keep the GPU online past its scheduled expiration. The GPU may still
be automatically terminated; an allocated EIP does **not** suspend the
schedule. One-at-a-time handoff must end with a supervised return to CPU
before that deadline. If termination wins the race, the EIP stays reserved
but may become **unassociated**. Use the CPU return procedure below.

### Prerequisite B — GPU certificate/IAM installation (separate future approval)

**The existing CPU-scoped trust does not permit a GPU to export the ACM
certificate.** After obtaining the actual GPU instance ID, run
`hostname-iam --gpu-instance-id i-...` to render a review-only proposal
that adds exactly one per-instance source-role policy and **one**
matching statement to the existing export-role trust. The proposal:

- uses `aws:userid=<verified IAM RoleId>:<actual GPU EC2 ID>`;
- permits only `sts:AssumeRole` to
  `studio-issue43-dcv-acm-export`;
- **preserves the original CPU session trust**, while the export role
  continues to allow only `acm:ExportCertificate` for the original
  certificate ARN;
- grants **no** Route 53 permission, static key, wildcard ACM export or
  Scheduler/termination privilege.

IAM proposal is **not attached by the review command** and needs a
separate explicit IAM approval and test against an actual GPU instance
role session before any deployment.

On the already running/guarded GPU instance, supervise installation of
the **same pinned AWS Workload Credentials Provider v3.1.2**, exact
certificate ARN, role ARN, root-owned reviewed
`/usr/local/sbin/studio-dcv-acm-activate` hook and the already reviewed
`ReadWritePaths=/etc/dcv` plus `CAP_CHOWN` systemd override.
Reuse [the existing AWS agent TOML](../infra/issue43-awcp.review-only.toml)
and [the CPU-tested certificate/rollback instructions](issue43-acm-dcv.md).
This is a guest install after GPU RES bootstrap readiness, **not an
Image Builder change or an edit to the pinned RES 2026.06 scripts**.

First export must be staged to the GPU's own
`/var/lib/studio/dcv-acm` (700 directory, `aws-wcp` ownership;
0600 files). Do **not** copy CPU private-key files or embed credentials
into userdata, CloudFormation, SSM command parameters or GitHub.
The GPU must present valid `dcv.kwesidavis.com` TLS on **localhost**
with SNI/hostname verification and a ready RES/DCV console **before**
its public EIP changes. The handoff's supervised SSM probe checks these
requirements without printing private keys. If the GPU uses built-in
self-signed DCV credentials, enroll through the reviewed guarded
`--enroll-default` procedure; reject unexpected existing TLS files
until supervised review.

### Prerequisite C — safe CPU→GPU transfer (not yet authorized)

The CPU must be **stopped**; the GPU must be **running** and prepared.
The preflight requires:

1. CloudFormation stack identity resolves to exact CPU and scoped GPU
   instance IDs; GPU VPC/AMI/role/ENI/ownership tags match; no foreign GPU EIP.
2. Dedicated EIP still belongs to CPU and carries Issue #43 tags;
   Route 53 A still has the exact single `54.159.180.104` record.
3. Both independent PR #65 schedules are enabled and have sufficient
   future deadline; root EBS matches tag/attachment checks.
4. GPU SSM guest probe succeeds for pinned RES host readiness, GDM,
   DCV `console`, matching cert/key and localhost trusted TLS.
5. Immediately before writes, source EIP ownership and GPU guard
   deadlines are rechecked.
6. Only then: disassociate old CPU EIP association, associate the same
   reserved EIP to GPU with **no implicit reassociation**, verify AWS
   readback, test external CA-trusted TLS and hostname, update the
   Issue #43 **EIP owner tag**, and verify DNS is unchanged.
7. On error, attempt to restore the EIP and owner tag to the original
   CPU without touching an unexpected foreign owner. A failure to
   prove restoration raises a **manual-intervention warning** rather
   than claiming a successful handoff.

There is a short planned connectivity gap during reassociation.
Concurrent EIP handoff by other operators is **not supported**; perform
under one supervised operator, stop if any ownership readback differs,
and never forcibly steal a foreign association.

### GPU→CPU return / expiration recovery (not yet authorized)

Stop the GPU and verify its current ownership tag and EIP association.
Then return the reserved EIP to the **original stopped CPU**.
The return operation leaves CPU stopped; start it separately with the
existing Studio CLI only after appropriate approval. Recheck
`dcvserver`, RES console, AWCP and trusted hostname TLS after boot.

If the independent Scheduler terminated GPU and the EIP has become
unassociated, the return plan accepts that case **only** when the EIP's
ownership tag still names the exact previous GPU ID supplied by the
operator. It never steals an EIP held by an unrelated instance.
Do not delete or bypass expiration schedules. Retained GPU EBS,
security group, launch template and Scheduler cleanup must still be
independently audited under the existing PR #65 process.

The hostname will have downtime while switching profiles or while
both workstations are stopped, but the reserved IP **cannot be
reassigned to a stranger while retained**. This is the desired
cost/simplicity tradeoff rather than custom Lambda reconciliation.

## Test matrix and release gates

[`tests/test_issue43_hostname_handoff.py`](../tests/test_issue43_hostname_handoff.py)
uses mocked AWS results only, covering approved and wrong owners,
CPU running, GPU not ready, wrong AMI/profile, foreign EIP, wrong DNS,
expired/missing/disabled guard schedules, weakened retry policy,
EBS/tag mismatch, SSM probe failure, rollback on bad TLS and API failure,
stopped/terminated GPU return, invalid GPU IDs, no mutation in read-only
plans, and **a hard-disabled live CLI even with `--execute`**.
The Issue #43 workflow also runs baseline PR #65 guard and guarded GPU
creation suites. No test creates AWS resources.

**Before separately approving a live GPU handoff:** choose an authorized
GPU launch under the PR #65 expiration window, verify exact IAM policy
changes, install/verify AWCP in its own staged guest, audit EIP permissions
and expected `--no-allow-reassociation` behavior, test a supervised
handoff and forced-failure rollback, return EIP to CPU **before guard
expiration**, independently clean GPU resources, then leave the
account in its known CPU/EIP ownership state.

The current implementation is a **reviewable offline gate**, not a
claim of completed GPU TLS deployment or a working production switch.
PR #67 must stay draft/unmerged until those follow-up approvals.
Public DCV port 8443 exposure is separately tracked by Issue #62.
