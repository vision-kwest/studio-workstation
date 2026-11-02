# Issue #43 — Stable IP + ACM trusted DCV (Tasks 3–6 accepted; Task 7 offline audit)

**Status:** Draft PR #67 remains **unmerged**. Tasks 3–6 were accepted live on the CPU: static EIP/DNS, exportable ACM, session-scoped IAM, AWCP v3.1.2, successful browser login and two stop/start TLS/persistence checks. The CPU is **stopped**. Task 7's no-op refresh, retention and CLI regression refinements are **offline-only on this feature branch**, not installed on that EC2 instance. No GPU hostname handoff is authorized.
This plan supersedes the bespoke Lambda/EventBridge/DynamoDB/DDNS/Certbot
architecture removed from the feature branch. The approved DNS delegation
was created before this PR.

## Task 7 — offline final adoption, security and release audit (2026-11-02 UTC)

**Execution boundary:** Reviewed IAM, EIP, DNS, ACM and CPU CloudFormation through
read-only AWS calls. Updated only draft PR #67 branch code, tests and
documentation. **No EC2/SSM start, GPU launch, DNS, IAM, ACM or AWCP guest
mutations; no RES/Image Builder edits or PR merge.**

### Audit findings and decisions

| Area | Read-only finding / decision |
| --- | --- |
| EIP | `eipalloc-0879a34aa1a1298dd` holds `54.159.180.104`, associated through `eipassoc-0e09a0e9afddda6db` to the original CPU primary ENI `eni-0719a8758ddd51d4c`. The CPU remains **stopped**. |
| DNS | Zone `Z02607163QPWX22FZ2Q9M` has only expected apex A, NS/SOA and ACM-validation CNAME; A TTL 300 points to `54.159.180.104`. No DDNS or stop-time deletion needed with allocated EIP. |
| ACM | Exact exportable public certificate `b753beee-5659-4311-a52f-39326b7b3243` is `ISSUED`, `Export=ENABLED`, `RenewalEligibility=ELIGIBLE`, expiry `2027-05-18T23:59:59Z`. Validation CNAME remains, status `SUCCESS`. Actual **future reissued** certificate has not yet been observed. |
| IAM | Verified exact export role `studio-issue43-dcv-acm-export` has only `acm:ExportCertificate` for one ARN. Export role trust and shared base-role `StudioIssue43ExactInstanceAssume` policy both restrict `aws:userid` to `AROAV223FBB3JOQHYPKDX:i-0bbfbdc878d3a2320`. A different CPU/GPU session does **not** inherit permission to export. |
| Stack | CloudFormation `studio-workstation-cpu-v013` is `CREATE_COMPLETE`; `DescribeStackResource(Workstation)` resolves the exact stopped CPU instance. EIP allocation is managed separately and MUST be retired separately if stack is destroyed. |
| Ownership | **CPU-only hostname ownership** is the approved present state. A GPU cannot be switched merely by updating DNS: it needs a separately approved EIP reassociation, own trusted certificate staging and deliberately scoped IAM. Do not change GPU PR #65 safeguards. |
| Ingress | Trusted TLS does **not** fix the existing TCP 8443 public exposure; hardening is tracked separately in Issue #62. |
| Credentials | The certificate private key stays in restricted AWS-provider staging and `dcv:dcv 0600` deployed generations. No private key may enter GitHub, CI or SSM output. |
| Cost | AWS EIP/public IPv4 `$0.005/hour` (~$3.65/730h), Route 53 hosted zone `$0.50/month` plus queries, and ACM exportable FQDN `$7` at issuance and each renewal (budget roughly three issuance/renewal charges in a year). ~$5.90/month first-year normalized gross estimate, excluding EC2/EBS and possible other IPv4 charges. EIP's incremental cost versus a public IP already attached to running EC2 may be smaller. |

**CLI:** Read-only AWS operations `DescribeStackResource` and
`DescribeInstances` independently returned the real stack→instance ID,
stopped state and EIP; they match the data path used by Studio CLI.
Previously, the draft CLI's DNS/TLS verifier was run with **real
certificate and DNS**, and the `connect`/`status --hostname` output
was tested with mocked stack discovery; negative offline tests now cover
stopped state, DNS mismatch, SNI/pinned-IP verification and
`--hostname`/`--ssm` incompatibility. **A fully unmocked,
credential-backed CLI invocation against a running workstation remains
unverified**: no EC2 startup was authorized for Task 7.

### New, offline-only refinements in draft PR #67

1. **Idempotent refresh:** Validate staged hostname, lifetime and matching
   key on every refresh. If both staged files are byte-identical to the
   current validated DCV certificate and key, check DCV service/console
   health **without restarting it**, without rewriting keys, and without
   generating another certificate directory. A failed console state
   still fails closed.
2. **Bounded managed retention:** After a successful changed-certificate
   activation, retain the **active** and **immediately preceding**
   generation for rollback and prune only older *positively identified*
   `gen-*` directories. On a no-op startup, select the newest verified
   prior generation as rollback and trim older verified excess generations,
   still without a DCV restart. Root-owned, symlinked, incorrectly
   permissioned, hard-linked, unfamiliar or incomplete entries are never
   force-deleted; they are left for manual review (so the overall directory
   can exceed two entries if unsafe foreign entries exist). Cleanup uses
   directory descriptors and no-follow guards and **only runs following
   successful certificate activation or healthy no-op validation**.
   Cleanup failures are logged but do not roll back a working trusted
   certificate.
3. **Regression coverage:** Includes changed-certificate switch/rollback,
   unchanged-pair no-op, unavailable console, mismatch/expired identity,
   permissions, no-delete policy for unknown files/symlinks/hardlinks,
   cleanup failure, retaining rollback generation and exact CLI
   DNS/TLS/instance checks. Runs without AWS credentials, guest DCV or
   imported private keys.

### Outstanding release approval gates

- **The new hook is not installed on the stopped CPU**. Before merging or
  claiming the new idempotence/retention behavior in production, explicitly
  approve one supervised live rollout and confirm unchanged-certificate
  startup does **not** disconnect DCV, generation count is bounded for
  verified files, and changed-cert/rollback behavior still succeeds.
  Do not start an EC2 instance under this offline Task 7 authorization.
- A future actual ACM-reissued certificate cannot be forced as part of a
  read-only review. Verify its delivery after renewal, and consider a
  renewal/expiry alert channel. Do not delete the validation CNAME.
- Resolve public TCP 8443 access under Issue #62 separately.
- PR #67 stays **DRAFT/unmerged** until this audit is reviewed and any
  required live acceptance and merge authorization are independently
  approved.

### References for costs and renewal

- [AWS VPC/public IPv4 pricing](https://aws.amazon.com/vpc/pricing/)
- [Route 53 pricing](https://aws.amazon.com/route53/pricing/)
- [ACM exportable public certificate pricing](https://aws.amazon.com/certificate-manager/pricing/)
- [ACM public certificate 198-day validity and renewal](https://docs.aws.amazon.com/acm/latest/userguide/gs-acm-request-public.html)

---

## Task 6 — browser, automatic refresh and stop/start acceptance (2026-11-02 UTC; COMPLETED)

**Status:** The user confirmed interactive browser DCV login with **no certificate warnings** and successful GNOME access as `workstation`. Subsequently a supervised stop/start preservation test succeeded; the CPU was stopped again after test cleanup. Draft PR #67 remains unmerged. Do not consider this a test of a future ACM-renewed certificate (only the current issued certificate has been refreshed).

### Verified before the user's browser test

- CPU running; public IP `54.159.180.104`; exact Route 53 hostname
  `dcv.kwesidavis.com` resolves to the unchanged EIP.
- After the Task 5 stop / Task 6 start, SSM came online without repair.
  Ubuntu 24.04, Amazon DCV 2026.0, `gdm3`, `dcvserver` and the
  `aws-workload-credentials-provider-acm` service were active.
  RES readiness semaphore present; DCV console `running`, owner
  `workstation`. Existing `dcv.pem` and `dcv.key` resolved to
  `dcv:dcv` mode `0600`; AWCP staging remained `aws-wcp:awscreds`
  mode `0700` and private staged files mode `0600`.
- Direct connection to `54.159.180.104:8443` with SNI and hostname
  `dcv.kwesidavis.com` validated via the guest's standard trust store:
  TLS 1.3, `Verification: OK`, verified peer name.
- Feature-branch Studio CLI `verified_dcv_hostname` ran against
  **real DNS and real TLS** using verified pinned file SHA. The opt-in
  `connect` and `status --hostname` output paths passed with **mocked
  CloudFormation/EC2 describe data**; full CloudShell CLI AWS discovery
  is not yet live-tested.

### Startup failure discovered and corrected under Task 6

The first AWCP post-boot refresh failed despite its service being active:

1. AWCP's `ProtectSystem=full` service sandbox made `/etc/dcv`
   read-only. The root-owned hook failed on creating a new generation
   with `OSError: [Errno 30] Read-only file system`.
2. An exact `ReadWritePaths=/etc/dcv` exception allowed the new
   directory but the service capability bounding set excluded
   `CAP_CHOWN`. The hook failed attempting to set the generation
   owner to `dcv` with `PermissionError: [Errno 1]`.
3. Only these two exceptions were added to the existing ACM service,
   using a root-owned systemd drop-in:
   [`infra/issue43-awcp-dcv-refresh.conf`](../infra/issue43-awcp-dcv-refresh.conf).
   The original AWCP sandbox and IAM remain otherwise unchanged.
4. Restarting only the AWS ACM agent then succeeded. Agent logs report
   **`Refresh command succeeded`** and **`Certificate refresh successful`**,
   DCV returned to console `running`, and a new generation
   `studio-tls-generations/gen-2fbmncn8` became active.
   Cert/key targets remained `dcv:dcv 0600`. The old working
   generation was retained for rollback.
5. A fresh TLS handshake again passed `Verification: OK` and
   hostname validation. This shows the _active_ agent-driven refresh
   path is working, not merely systemd process startup.

The narrow drop-in must be installed on future instances **before**
enabling AWCP's automatic ACM refresh. Its two directives are guarded
by an offline regression test. Do not broaden `ReadWritePaths` to
`/etc`, disable `ProtectSystem`, or grant broad AWS permissions.

### Browser confirmation and supervised persistence test

**User confirmation:** Browser `https://dcv.kwesidavis.com:8443`
connected successfully, no certificate warning, and login to
the DCV GNOME desktop as the existing local `workstation` user succeeded.

**Supervised second stop/start test, exact approved instance only:**
1. Created one temporary owner `workstation:workstation`, mode 0600 marker
   `/home/workstation/.studio-issue43-task6-persistence-20261102`.
   SHA-256 before stop:
   `cab2677287188c2ad1a50d8ee914e92e0f2bad5cd29dba3f8c5ddf5bf78935f0`.
   This was test-only data; no user files were overwritten.
2. Confirmed DCV `console` `running` owner `workstation`;
   AWCP ACM service active and had a previous successful refresh.
   Stopped `i-0bbfbdc878d3a2320` and independently observed `stopped`.
3. While stopped, `eipalloc-0879a34aa1a1298dd` remained associated via
   `eipassoc-0e09a0e9afddda6db` to primary ENI
   `eni-0719a8758ddd51d4c`, and an authoritative Route 53 DNS test
   returned exactly `54.159.180.104` with `NOERROR`.
4. Started the same instance. SSM came online automatically. SHA-256
   verified **identical** after startup. Ubuntu 24.04 and RES host-ready
   semaphore persisted; DCV 2026.0, GDM, ACM provider and `dcvserver`
   were active; console `running`, owner `workstation`.
5. AWCP automatically exported and refreshed the installed current certificate:
   provider log at **2026-11-02T04:48:14 UTC**:
   `Refresh command succeeded` and `Certificate refresh successful`.
   New active generation became
   `studio-tls-generations/gen-9uzn3i9q`.
   Certificate/key targets stayed `dcv:dcv` with mode 0600.
   Trusted TLS 1.3 handshake again reported `Verification: OK`
   and `Verified peername: dcv.kwesidavis.com`.
6. Independently rechecked the exact marker SHA-256; removed **only**
   the temporary marker and verified its absence. DCV remained healthy.
   Stopped the exact CPU instance again and independently confirmed
   state `stopped`, unchanged EIP attachment/DNS/ACM certificate and
   `studio-workstation-cpu-v013` CloudFormation stack `CREATE_COMPLETE`.
   All permanent EIP/DNS/certificate resources retained.

**CLI verification scope:** The draft Studio CLI's real hostname resolution and
system-CA-validated TLS were verified on the instance using a SHA-checked
feature-branch module; `connect/status --hostname` paths were tested with
*mocked* AWS stack metadata. Full unmocked CloudShell/desktop CLI command
execution has **not been verified** and should be part of Task 7's final
release gate. No new IAM permission was granted to facilitate the test.

**Remaining limitations to track in Task 7 / follow-up:** Automated delivery
after real ACM certificate renewal (a new CA-issued certificate) could not be
exercised before ACM's renewal window; only repeat export/activation of
the currently issued certificate was proven. The activation hook presently
creates a new generation and restarts DCV at each successful post-boot refresh
even if content is unchanged; review an idempotent no-op optimization and
retention/garbage collection policy before merge to avoid unnecessary
disconnects and unbounded growth. Actual browser login was confirmed
before the second restart, not repeated by a user after that restart; the
post-restart console and CA TLS checks passed on the guest.

**End state:** CPU **stopped**, persistent storage preserved, static EIP
`54.159.180.104` and Route 53 A record unchanged, ACM
`ISSUED`/`ELIGIBLE`, PR #67 draft/unmerged. No modifications to RES 2026.06
source, GPU controls, Image Builder or unrelated AWS resources.


---

## Task 5 — approved AWCP installation and DCV TLS enrollment (2026-11-02 UTC)

**Live result: PASS for this task's guest, IAM, certificate and console gates.**
Task 6 browser login and stop/start recovery acceptance remain separate.
No changes were made to RES 2026.06 source, Image Builder, GPU IAM/expiration
mechanisms or the original DCV `dcv.conf`.

### Exact resource and evidence inventory

| Resource / verification | Observed |
| --- | --- |
| Approved CPU | `i-0bbfbdc878d3a2320` (Ubuntu 24.04, `m7i.large`) |
| Static IP | `54.159.180.104`, allocation `eipalloc-0879a34aa1a1298dd` |
| Certificate | `arn:aws:acm:us-east-1:401233741942:certificate/b753beee-5659-4311-a52f-39326b7b3243` |
| Certificate state | `ISSUED`, `Export=ENABLED`, `RenewalEligibility=ELIGIBLE` **after first AWCP export** |
| Base EC2 role | `StudioWorkstationSSMRole` (RoleId `AROAV223FBB3JOQHYPKDX`) |
| Base new inline policy | `StudioIssue43ExactInstanceAssume`: `sts:AssumeRole` only to exact export-role ARN, with `aws:userid` equal to `AROAV223FBB3JOQHYPKDX:i-0bbfbdc878d3a2320` |
| Newly created target role | `studio-issue43-dcv-acm-export` |
| Export role policy | `StudioIssue43ExactAcMExport`: only `acm:ExportCertificate` on exact ACM ARN |
| Export role trust | Only base role principal with matching exact CPU instance `aws:userid` |
| Live instance STS test | `AssumeRole` succeeded for exact CPU session; no secret credentials printed |
| AWCP version | `v3.1.2` pinned from official AWS release and tag |
| Installed binary SHA-256 | `1480c04bddfdea241f9b67fedd15f1006e53d33d234b5aca3f8eec6e563f420a` |
| AWCP service | `aws-workload-credentials-provider-acm.service`: active, enabled |
| Unused capabilities | AWCP Secrets Manager/token services disabled |
| Staged certificate | `/var/lib/studio/dcv-acm/fullchain.pem` — `aws-wcp:awscreds`, mode `0600` |
| Staged key | `/var/lib/studio/dcv-acm/privkey.pem` — `aws-wcp:awscreds`, mode `0600` |
| Staging directory | `aws-wcp:awscreds`, mode `0700` |
| DCV version | Amazon DCV `2026.0 (r24952)` |
| Initial DCV certificate baseline | `/etc/dcv/dcv.pem` and `dcv.key` absent — built-in self-signed certificate |
| First enrollment | Reviewed `studio-dcv-acm-activate --enroll-default`; successful generation creation and DCV service restart |
| DCV certificate/key | Symlink targets both `dcv:dcv`, mode `0600` |
| Active generation link | `studio-tls-generations/gen-initial-59b9fb57fae105fa` |
| DCV readiness | `systemctl is-active dcvserver` active, `dcv describe-session console` running, owner `workstation` |
| TLS handshake on guest | TLS 1.3, TLS_AES_256_GCM_SHA384, `Verification: OK`, verified peername `dcv.kwesidavis.com` |
| Original DCV config | `/etc/dcv/dcv.conf` untouched by this task, root:root mode 0644 |

**Integrity nuance:** The installer and integration files were downloaded from
immutable pinned GitHub refs and verified against their exact upstream Git blob
SHA-1 object identities before execution. The AWS release binary was downloaded
by the version-pinned official AWS installer via HTTPS, and its installed SHA-256
was recorded above. An independently published SHA-256 signature/attestation
of that release binary was **not** separately verified; do not claim otherwise.

**Compatibility findings:** The original DCV installation had **no custom TLS
file pair to back up**. The branch was updated with a guarded
`enroll_default` path which only accepts missing certificate/key paths,
installs a validated new generation, then restarts DCV. If startup fails, it
removes its own links and retries the default self-signed certificate.
The existing normal `activate()` hook remains strict and uses a single
generation-pointer switch for subsequent renewals. Offline tests for the
new first-install path and rollback passed before live application.

**First export:** The ACM provider automatically exported the certificate into
the privately owned staging directory. The first refresh hook was allowed
to fail closed before enrollment, after which supervised
`--enroll-default` succeeded. ACM later reported
`RenewalEligibility=ELIGIBLE`. No private key contents were printed in
SSM/CI or stored in the repository.

### Rollback / Task 6 approval gate

Do not run these operations without an explicit rollback authorization.
To return to the pre-Task-5 default DCV TLS state:

1. Preserve the exact current DCV symlink targets, `dcv.conf`, guest session
   readiness and the root-owned hook before changing anything.
2. Disable/stop the ACM provider service to prevent simultaneous writes.
3. Verify that `/etc/dcv/dcv.pem` and `dcv.key` are the two **expected
   Issue #43 symlinks** (not arbitrary regular files). Remove only those
   exact symlinks and the expected active pointer; preserve the private
   generations for forensic review until the default DCV restart succeeds.
4. Restart `dcvserver`, validate that the default built-in certificate
   returns, that `dcv describe-session console` is running and that the
   local workstation account still works.
5. Separately revoke the two Issue #43 inline policies and retire the
   dedicated export role only after a deliberate decision not to use the
   certificate. Keep the ACM certificate, validation CNAME, dedicated EIP
   and DNS A record unchanged unless their separately approved retirement
   is explicitly requested.
6. Restore the ACMP service/certificate only via a supervised reversal with
   fresh verification of certificate/key and console readiness.

Do not manually print or paste any exported private key into the console,
SSM command, CI logs or GitHub. Task 6 separately verifies browser login,
stop/start, automatic restart reconciliation, TLS renewal/recovery and
operator-facing CLI `connect`.

---

## Objective, boundaries, and ownership

- Hostname: `dcv.kwesidavis.com:8443`.
- Route 53 public hosted zone `Z02607163QPWX22FZ2Q9M` now contains the
  delegated NS/SOA, the approved A record and ACM validation CNAME. Bluehost
  remains authoritative for `kwesidavis.com`.
- ONE newly allocated, dedicated Elastic IP is associated with ONE explicitly
  approved workstation/ENI. Do not repurpose the two RES NAT Gateway EIPs.
- Route 53 apex A record points to that EIP once. No dynamic DNS process.
- One DNS-validated **exportable** public ACM certificate from us-east-1. Keep
  its validation CNAME for unattended renewals.
- AWS Workload Credentials Provider (AWCP) **v3.1.2**, pinned and verified,
  retrieves renewed certificate/key on EC2 startup and about every 24 hours.
- `studio workstation connect/status --hostname dcv.kwesidavis.com` is
  opt-in and verifies that public DNS matches the EC2 public IP and that TLS
  authenticates the hostname. The original IP-based workflow is unchanged.
- No changes to the proven RES 2026.06 bootstrap, GPU AMI/Image Builder,
  GPU acceptance token or expiration safeguards. No inbound SSH or HTTP.

The EIP eliminates DNS writes on start/stop, but still costs money while
allocated, even if the workstation is stopped. A single EIP + hostname serves
one workstation at a time; any CPU/GPU handoff needs explicit reassociation,
ownership and validation.

## AWCP v3.1.2 integration

The AWS-managed provider's actual TOML schema is represented by
[`infra/issue43-awcp.review-only.toml`](../infra/issue43-awcp.review-only.toml).
After separate authorization, replace the **exact** certificate/role ARN
placeholders and have AWCP write to a **private staging directory**:

- `/var/lib/studio/dcv-acm/fullchain.pem`
- `/var/lib/studio/dcv-acm/privkey.pem`

Omit `chain_path`, causing AWCP to append the chain to certificate_path.
Modes are 0600. The staging directory must be created with `aws-wcp` write
access, no untrusted/user writable ancestors. Never have AWCP write directly
to `/etc/dcv/dcv.pem` and `/etc/dcv/dcv.key`: its source stages files and
renames them one at a time, not pair-atomically, and the UNIX permission
schema does not directly set final owner `dcv`.

AWCP's `refresh_command` must invoke the exact root-owned executable
`/usr/local/sbin/studio-dcv-acm-activate`, built from the reviewed
[`scripts/studio_dcv_acm.py`](../scripts/studio_dcv_acm.py), with permissions
such that no unprivileged user can edit it. AWCP's installer generates an
exact-command sudoers rule, which must be reviewed carefully.

On refresh the hook:
1. Verifies root execution and that supervised DCV enrollment already exists.
2. Rejects source symlinks, invalid hostnames, expired certs and mismatched keys.
3. Creates a new, private, `dcv`-owned generation directory (0700) with
   `dcv`-owned certificate and private key (0600); verifies copied files.
4. Switches one `/etc/dcv/studio-tls-current` symlink to the new generation
   **atomically**, while `dcv.pem` and `dcv.key` each resolve through it.
   This makes the pair visible through one pointer instead of two renames.
5. Because DCV's detection of symlink-target updates is **not established**,
   deliberately restarts `dcvserver` on a changed certificate and polls
   `dcv describe-session console --json` readiness. This can disconnect
   the active DCV client during renewal. The existing RES configuration and
   automatic-console settings are preserved, but actual re-creation MUST be
   verified in a controlled live test before adopting for production.
6. Rolls back the generation pointer and retries service startup if the new
   certificate fails to start DCV. Failures are not reported as success.

**No automatic enrollment in Task 2.** Initial conversion of the existing
real `/etc/dcv/dcv.pem` and `dcv.key` to the two expected symlinks is a
separate, supervised Task 5 operation, after securely backing up both original
files. The hook refuses to overwrite/enroll unprepared paths. Prepare
`/etc/dcv/studio-tls-generations/gen-original/` as the old pair, symlink
`/etc/dcv/studio-tls-current` to that original generation and then install
`dcv.pem -> studio-tls-current/dcv.pem` and
`dcv.key -> studio-tls-current/dcv.key`. Do not issue these commands on
a live machine until separately reviewed and approved.

**Unresolved live compatibility gate:** DCV's internal symlink-handling and
automatic console recovery after a service restart are untested. On any
failure the original pair must be recoverable, including if the instance
reboots mid-activation. Before automatic renewal is enabled, validate
interrupted switch/reboot recovery, TLS handshake trust and console persistence.
Keep at least one protected working backup. Do not delete old generations
until the new one has been confirmed and retained for rollback. The agent
refresh runs approximately daily when the workstation is powered on,
including at boot after a long stop; ACM renews independently off-host.

## IAM inventory — Task 5 deployed; policy file is review-only reference

[`infra/issue43-acm-iam.review-only.json`](../infra/issue43-acm-iam.review-only.json)
records the **exact policies deployed during Task 5**: (a) the base
instance role's `sts:AssumeRole` permission only for the dedicated export
role **and only when `aws:userid` identifies the original CPU instance
session**; (b) the export role's matching instance-session trust; and (c)
`acm:ExportCertificate` on exactly one certificate ARN. This JSON is
an **audit reference, not an executable deployment script**. No Route 53
writer permission was added to the guest. The existing
`StudioWorkstationSSMRole` is shared by CPU/GPU instances, but the
newly added permission does not authorize other instance sessions.
Do not broaden or reapply these policies or change GPU profile pinning
without separately approved review.

No IAM, S3, ACM or instance resources have been changed by Task 2.

## Task 3 — approved live Elastic IP and DNS record (completed 2026-11-02 UTC)

The operator approved **only** the Elastic IP allocation/association and one A-record
creation; no EC2 start, IAM change, ACM request or agent installation was authorized.

| Item | Verified value |
| --- | --- |
| AWS account / region | `401233741942` / `us-east-1` |
| Initial approved owner | stopped CPU instance `i-0bbfbdc878d3a2320` |
| Workstation stack | `studio-workstation-cpu-v013` |
| Dedicated EIP address | `54.159.180.104` |
| EIP AllocationId | `eipalloc-0879a34aa1a1298dd` |
| EIP AssociationId | `eipassoc-0e09a0e9afddda6db` |
| Primary ENI | `eni-0719a8758ddd51d4c` |
| Primary private IPv4 | `172.31.4.184` |
| Delegated zone | `Z02607163QPWX22FZ2Q9M` |
| A record | `dcv.kwesidavis.com. A 300 54.159.180.104` |
| Route 53 change | `/change/C00862872TV2F3IPSK8OP` — **INSYNC** |
| DNS authoritative test | `NOERROR`, exact A answer `54.159.180.104` |
| EC2 state after changes | **stopped** |

The EIP was tagged at allocation with `StudioIssue=43`,
`StudioDcvHostname=dcv.kwesidavis.com`,
`StudioDcvInstanceId=i-0bbfbdc878d3a2320`, and
`StudioOwnerStack=studio-workstation-cpu-v013`. The two preexisting EIPs
assigned to RES NAT gateways were **not changed**.

**Operational caveat:** The new Elastic IP is independently managed, **not a
CloudFormation resource of the CPU workstation stack**. Deleting that stack
may leave the EIP allocated and accruing hourly charges. It must be separately
audited and intentionally released when retired. The current A record resolves
to a **stopped** EC2 instance, so it is not yet a working DCV login endpoint;
ACM validation and trusted TLS are separate later tasks.

### Read-only follow-up

```bash
aws ec2 describe-addresses --region us-east-1 \
  --allocation-ids eipalloc-0879a34aa1a1298dd --no-cli-pager
aws ec2 describe-instances --region us-east-1 \
  --instance-ids i-0bbfbdc878d3a2320 --no-cli-pager
aws route53 list-resource-record-sets \
  --hosted-zone-id Z02607163QPWX22FZ2Q9M --no-cli-pager
aws route53 get-change --id C00862872TV2F3IPSK8OP --no-cli-pager
```

### Rollback procedure (DOCUMENTED ONLY; NOT EXECUTED)

1. Obtain explicit separate approval for retirement and confirm the A record
   still exactly equals `54.159.180.104`, with no new owner or DNS handoff.
2. Remove **only** that exact Route 53 A record using `DELETE` with identical
   name/type/TTL/value; wait for `INSYNC` and allow cached responses to expire.
   Preserve delegated zone NS/SOA, all unrelated records and Bluehost NS.
3. Confirm EIP `eipalloc-0879a34aa1a1298dd` remains associated **only**
   with ENI `eni-0719a8758ddd51d4c`. Disassociate using the independently
   verified `eipassoc-0e09a0e9afddda6db`. Recheck allocation status.
4. Release **only** `eipalloc-0879a34aa1a1298dd` once it is unassociated
   and no workstation, renewal or DNS workflow relies on it.
5. Verify the address is absent from EC2 DescribeAddresses, the exact A record
   is absent, and no other EIPs were altered. Recheck resource charges.
   No rollback action is part of Task 3 authorization.

Do **not** release or disassociate this EIP without first removing or
reassigning the corresponding DNS record, and never touch the two RES NAT
gateway EIPs. Reassociation with another CPU/GPU machine is an explicit
future handoff operation, not automatic.

## Task 4 — approved ACM exportable certificate and DNS validation (completed 2026-11-02 UTC)

The owner approved one $7 public exportable ACM certificate issuance and only
its associated DNS validation CNAME. The certificate was requested with
`ValidationMethod=DNS`, `KeyAlgorithm=RSA_2048`, and
`Options.Export=ENABLED`. ACM status is **ISSUED**, and Route 53 change is
**INSYNC**. The validated domain is the exact FQDN only, no wildcard/SAN.

| Resource | Independently verified |
| --- | --- |
| Certificate ARN | `arn:aws:acm:us-east-1:401233741942:certificate/b753beee-5659-4311-a52f-39326b7b3243` |
| Account / region | `401233741942` / `us-east-1` |
| Name | `dcv.kwesidavis.com` |
| Type / key | `AMAZON_ISSUED` / `RSA-2048` |
| Exportability | `Options.Export=ENABLED` |
| ACM status | **ISSUED** |
| ACM DNS validation | **SUCCESS** |
| IssuedAt | `2026-11-02T03:04:21.949000Z` |
| NotAfter | `2027-05-18T23:59:59Z` |
| Hosted zone | `Z02607163QPWX22FZ2Q9M` |
| Validation DNS change | `/change/C062287530VSNSWTJKAXZ` — **INSYNC** |
| Validation CNAME name | `_20c57ecf24474ce9d94e7d4c3dcf91fb.dcv.kwesidavis.com.` |
| Validation CNAME target | `_4e04c3f88921dbe651941c99854d906f.wzccmgtwzk.acm-validations.aws.` |
| Validation DNS TTL | `300` seconds |
| RenewalEligibility before first export | **INELIGIBLE** (expected) |
| Certificate exported/installed | **No — Task 5 gate** |
| Workstation EC2 state | **stopped — unchanged** |

A Route 53 authoritative DNS test returned `NOERROR` with the exact
ACM-supplied CNAME. The existing `dcv.kwesidavis.com` A record still points
to EIP `54.159.180.104`; parent delegation NS and SOA were unchanged.
Do **not** remove the CNAME after issuance; ACM needs it for managed renewal.

### Costs, first export, and renewal

- ACM charges **$7 for this exact FQDN upon issuance** and **$7 again
  on each subsequent certificate renewal**. Charge appears on AWS billing,
  not necessarily immediately in cost explorer.
- Public certificate validity is 198 days, and ACM attempts renewal 45 days
  before expiry **once renewal eligibility requirements are satisfied**.
- Renewal eligibility currently reports `INELIGIBLE` because the newly
  issued certificate has **not yet been exported or attached**. An initial
  successful export in separately approved **Task 5** should change
  eligibility to `ELIGIBLE`; recheck via `DescribeCertificate` and do
  not describe auto-renewal as working before this is verified.
- AWCP v3.1.2 will later retrieve renewed material on the running guest; the
  actual agent setup, permissions, certificate export, private-key handling
  and DCV deployment remain **unperformed**. No private key was requested
  or exposed in this task.

### Exact read-only verification commands

```bash
aws acm describe-certificate --region us-east-1 \
  --certificate-arn arn:aws:acm:us-east-1:401233741942:certificate/b753beee-5659-4311-a52f-39326b7b3243 \
  --query 'Certificate.{Arn:CertificateArn,Name:DomainName,Status:Status,Export:Options.Export,Renewal:RenewalEligibility,IssuedAt:IssuedAt,NotAfter:NotAfter,Validation:DomainValidationOptions}' \
  --no-cli-pager
aws route53 get-change --id C062287530VSNSWTJKAXZ --no-cli-pager
aws route53 list-resource-record-sets --hosted-zone-id Z02607163QPWX22FZ2Q9M --no-cli-pager
aws ec2 describe-instances --region us-east-1 \
  --instance-ids i-0bbfbdc878d3a2320 \
  --query 'Reservations[].Instances[].{Instance:InstanceId,State:State.Name}' --no-cli-pager
```

### Guarded rollback / retirement procedure (NOT EXECUTED)

1. Explicitly authorize certificate retirement. Check exact ARN/domain,
   whether the certificate was exported or installed, and any current
   consuming services. Do not delete an in-use certificate.
2. If certificate has been deployed, switch the DCV service to a safely
   validated alternate certificate **before** ACM deletion and verify
   login/recovery. Revoke any dedicated export permissions only after
   ensuring the replacement no longer depends on them.
3. Call `acm delete-certificate` **only** for the exact ARN after all
   dependencies are removed. This does not reverse the $7 issuance fee.
4. If there is no other consumer requiring DNS validation, delete **only**
   the exact CNAME above, with exact name/type/TTL/value, and verify
   `INSYNC`; preserve the EIP A record and NS/SOA records. Removing
   validation DNS before retirement would break future managed renewal.
5. Confirm the certificate and CNAME are absent and no unrelated ACM/DNS
   resources were touched. EIP retirement is a distinct Task 3 process.

This Task 4 rollback was documented, **not executed**; the subsequent
Task 5 installation and scoped IAM deployment were separately authorized
and completed. Any future rollback requires a new explicit approval.

## Completion and remaining authorizations

- **Completed with separate approvals:** Task 3 EIP/Route 53, Task 4 ACM,
  Task 5 agent/instance-scoped IAM/DCV enrollment, Task 6 human browser login,
  AWCP refresh and supervised stop/start. CPU was stopped at the end.
- **Task 7:** read-only infrastructure audit, draft hook and CLI improvements,
  offline failure-path tests, documentation and final-review preparation.
  Changes remain in draft PR #67 and are **not installed** on the CPU.
- **Still requires explicit permission:** supervised live rollout of the
  updated hook, any extra CPU/GPU launch or EIP handoff, changes to IAM/DNS,
  and PR merge. No GPU TLS capability is implied by the CPU acceptance.
- **Issue #62:** independent public TCP 8443 access hardening.

## Tests and controls

[`tests/test_issue43_acm_dcv.py`](../tests/test_issue43_acm_dcv.py)
exercises symlink enrollment refusal, cert/key mismatch and hostname
validation, secure file permissions, successful generation activation,
failure before switch, and post-switch rollback. All checks use local mocks,
no AWS calls and no real service restart. The new no-op and retention safety checks are offline-only until approved
guest deployment. Run Issue #43 and existing Studio CLI regressions before
the final release decision; RES 2026.06 source and GPU launch safeguards
are unchanged.

**Security:** never copy an exported private key into Git, user data, CI
artifacts, an unrestricted SSM command, or publicly readable filesystem.
No new CloudFormation or IAM resources are authorized by this offline
Task 7. The exact Task 5 IAM policies are already live, documented above;
future policy edits require explicit approval. Never broaden DCV public ingress. Keep this solution separate from Issue #62.

## Upstream authoritative references

- [AWS Workload Credentials Provider](https://docs.aws.amazon.com/acm/latest/userguide/acm-certificate-automation.html)
- [AWCP source](https://github.com/aws/aws-workload-credentials-provider)
- [AWCP certificate file store](https://github.com/aws/aws-workload-credentials-provider/blob/main/aws_certificatemanager_provider/src/certificate_file_store/certificate_store.rs)
- [Amazon DCV certificate requirements](https://docs.aws.amazon.com/dcv/latest/adminguide/manage-cert.html)
- [ACM exportable public certificates](https://docs.aws.amazon.com/acm/latest/userguide/acm-exportable-public-certificates.html)
- [Issue #43](https://github.com/vision-kwest/studio-workstation/issues/43)
