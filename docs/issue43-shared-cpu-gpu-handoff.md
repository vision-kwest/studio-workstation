> **Archived Issue #43 pre-merge acceptance plan.** This text reflects the design before merged PRs #67, #79, #89 and #91; its statements that the GPU was not launched, the old CPU was required, or the feature branch was unmerged are historical, **not current operating requirements**. The old CPU has been terminated. See [independent EIP](independent-eip.md) and [current CLI](studio-cli.md) for active commands and constraints.

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
- at least **60 minutes remain** before termination, a deadline
  **no more than 240 minutes away** (the original PR #65 guard bound),
  and EBS cleanup 30–120 minutes after termination.

This 60-minute threshold is a *minimum safety gate*, not authorization
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

## Phase 2 — Task 2 final offline readiness (2026-11-02 UTC)

**PASS for offline design/tests; NO LIVE-GPU authorization implied.** This
review did not modify AWS, start instances, allocate resources, attach IAM,
reassociate the EIP, or edit the original RES/GPU/Image Builder code.
Draft PR #67 remains unmerged and `LIVE_HANDOFF_ENABLED=False`.

### Concrete, separately approved Task 3 prerequisites

1. **Management client:** use existing approved AWS management credentials
   with read-only CloudFormation (`DescribeStackResource`),
   EC2 instance/EIP/volume inventory, IAM role/policy reads, Scheduler
   group/schedule reads, Route 53 record inspection and SSM diagnostics.
   The CPU/GPU guest's intentionally limited instance profile is **not**
   a CLI management identity. Final, unmocked CLI hostname commands
   remain unproven from an authorized operator environment because no
   running workstation or AWS management CLI was used for Task 2.
2. **Guarded GPU launch:** reuse the existing `studio-gpu-cli-accept65`
   stack/token/AMI and original PR #65 launch path. Verify **both**
   independently armed schedules, their immutable EC2/EBS targets,
   retries, trust role, exact IAM policy, and no other policies. Existing
   `runtime/pr65_gpu_create.preflight()` is now reused in the handoff
   preview: a weakened or missing Scheduler execution-role policy fails
   before transferring any EIP. A healthy running GPU, RES console,
   NVIDIA/T4 readiness and root EBS ownership are mandatory.
3. **Handoff clock:** each transfer rechecks the actual termination
   deadline using UTC; it requires **at least 60 minutes** remaining,
   no more than the original 240-minute bound, and volume cleanup
   30–120 minutes after termination. The 60-minute buffer is for user
   login, EIP return and independent resource cleanup. Abort rather than
   extending/cancelling a one-time safeguard. No work should continue
   beyond the Scheduler deadline.
4. **GPU IAM:** obtain the **actual new GPU instance ID** from the guarded
   launch (not a guessed ID). Review the output of
   `hostname-iam --gpu-instance-id <actual-id>`; add a separate inline
   statement on `StudioWorkstationSSMRole` permitting
   `sts:AssumeRole` only when
   `aws:userid=AROAV223FBB3JOQHYPKDX:<actual-id>` and update
   `studio-issue43-dcv-acm-export` role trust with that matching
   additional instance-session condition. **Preserve** existing CPU
   session trust, the single-certificate `acm:ExportCertificate` grant,
   and original PR #65 IAM/schedules. Revoke the specific GPU permission
   and trust addition only as separately approved cleanup after
   GPU acceptance, without revoking CPU access.
5. **Pinned AWS agent / provenance:**
   - AWS Workload Credentials Provider v3.1.2, tagged release
     (2026-10-28). Pin upstream Linux installer to
     `aws/aws-workload-credentials-provider` **tag `v3.1.2`**
     (Git blob SHA-1
     `fe77b1db02b9a61acb6027611d260b52fefd1f6d`).
   - CPU-validated installed x86_64 agent binary SHA-256:
     `1480c04bddfdea241f9b67fedd15f1006e53d33d234b5aca3f8eec6e563f420a`.
     On the GPU Ubuntu x86_64 guest, fetch the same pinned
     version over TLS; check the resulting installed binary against
     this **previously tested CPU artifact hash**, plus a version check.
     The upstream Linux bootstrap installer itself checks that the
     download has ELF magic but **does not authenticate an upstream
     publisher-provided binary digest**. A CPU binary match is an
     identity cross-check, **not a vendor signature/attestation**.
   - Use the same reviewed certificate hook, AWCP TOML (exact ACM and
     export role ARNs), and minimal
     `infra/issue43-awcp-dcv-refresh.conf` systemd permissions
     (`ReadWritePaths=/etc/dcv`, `CapabilityBoundingSet=CAP_CHOWN`).
     Install services disabled/not-started first; review sudoers and
     sandbox before starting only the ACM provider.
     Stage fullchain/key separately in mode 0600 under an
     `aws-wcp`-owned mode 0700 directory. Do not output private key
     contents to SSM/GitHub/CI. Start/enroll and verify healthy DCV.
6. **GPU trusted readiness:** only after SSM is online and RES/GDM/DCV
   automatic console is running should the GPU local certificate/key
   be validated. The SSM probe checks hostname, expiration, matching
   public key, restricted file permissions and trusted localhost
   TLS handshake; `InvocationDoesNotExist` eventual-consistency
   errors retry within the bounded poll, while `AccessDenied` and
   real failures propagate immediately. The **public** trusted TLS
   check follows the EIP association and must use the exact hostname
   and pinned IPv4, without weakening trust.
7. **Return and cleanup:** before any move, confirm CPU is stopped,
   exact EIP association and tags unchanged, and the GPU still has
   enough deadline margin. On failure, attempt to restore only the
   original CPU association and tag, without stealing an unknown
   association. On success, perform the user's GPU DCV browser login
   checkpoint, then stop/terminate GPU under the original safeguards,
   return EIP to CPU and verify it before GPU resource cleanup.
   Independently verify retained EBS, security group, launch template
   and Scheduler schedule removal; the expiry EBS delete remains
   best effort. If CPU EIP restoration cannot be verified, stop
   automatically and escalate for manual intervention.

### Task 2 verification and outstanding limitations

- **Verified via AWS read-only queries:** the exact original PR #65
  Scheduler execution role trust is scoped to
  `studio-task7-pr61` and account `401233741942`, its
  `StudioPr65ExactTaggedExpiry` grants exactly the tagged EC2
  terminate and retained-volume delete operations, it has no
  other inline/managed policies, and the Scheduler group is active.
- **Verified via IAM `SimulateCustomPolicy`:** proposed GPU-only
  source-role permission allows only the new exact GPU-instance
  `aws:userid`; the original CPU session, unrelated instance ID and
  missing session identity all receive `implicitDeny` **under that
  proposed statement**. This does not simulate the future combined
  source/export-role trust, SCPs, runtime credentials or policy
  propagation. Re-verify exact runtime identity during Task 3.
- **Verified AWS CLI contract:** `ec2 associate-address` supports
  `--no-allow-reassociation` for preventing an implicit EIP steal.
  The plan uses explicit disassociation and readback; EC2 documents
  that an EIP reassignment can momentarily interrupt availability.
  Expect and bound a brief handoff outage.
- **Offline fixes:** handoff now reuses full PR #65 role
  `preflight()` before any transfer and again on recheck;
  the SSM probe tolerates bounded
  `InvocationDoesNotExist` propagation but rejects any other
  authorization error. Original CPU return, bad TLS, partial
  disassociation, wrong owner, stale expiry and unexpected GPU EIP
  are tested with AWS mocks.
- **Unproven until separately approved live GPU task:** actual GPU
  bootstrap and AWCP installation, IAM grant propagation, trusted
  localhost/public DCV certificate, EIP transfer/rollback, expiration
  delivery, GPU→CPU return, complete retained-EBS cleanup and real
  management-client CLI commands. Keep all these as explicit checks,
  **not** implied successes.

### External reference sources

- [AWS CLI AssociateAddress and no-allow-reassociation](https://docs.aws.amazon.com/cli/latest/reference/ec2/associate-address.html)
- [AWS EC2 Elastic IP reassociation](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/working-with-eips.html)
- [Pinned AWS Workload Credentials Provider](https://github.com/aws/aws-workload-credentials-provider/tree/v3.1.2)
- [Existing PR #65 guard role and schedules](../runtime/pr65_gpu_create.py)

---

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
