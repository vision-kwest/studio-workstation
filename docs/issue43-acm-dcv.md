# Issue #43 — Stable IP + ACM trusted DCV (Tasks 3–5 deployed; Task 6 pending)

**Status:** Draft PR #67 remains unmerged. Tasks 3–5 are live: EIP/DNS, ACM certificate, scoped IAM, AWCP v3.1.2 and verified DCV TLS enrollment. The Studio CLI changes in PR #67 are NOT merged, and Task 6 browser/stop-start acceptance is still pending.
This plan supersedes the bespoke Lambda/EventBridge/DynamoDB/DDNS/Certbot
architecture removed from the feature branch. The approved DNS delegation
was created before this PR.

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

## IAM design (review-only)

[`infra/issue43-acm-iam.review-only.json`](../infra/issue43-acm-iam.review-only.json)
is a template for: (a) base instance profile ability to assume ONE approved
export role, (b) dedicated export role trust, and (c) that role's
`acm:ExportCertificate` on exactly ONE certificate ARN. No Route 53 writer
permission is needed for the guest. The existing `StudioWorkstationSSMRole`
is shared by several instances: granting it `sts:AssumeRole` would let
other holders assume the certificate-export role, so approve explicit
ownership, rotation and isolation or a separate instance profile before
changing IAM. Do not change GPU acceptance profile pinning without a
separate GPU review.

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

This rollback was documented, **not executed**. Task 5 requires a
separate approval for IAM permissions, on-host AWCP installation,
first certificate export, key storage and controlled DCV testing.

## AWS steps requiring separate approvals

- **Task 3:** Allocate *new* EIP and associate approved workstation, create
  Route 53 A record, verify ownership and rollback. Do not use NAT EIPs.
- **Task 4:** Request ACM exportable certificate for exact FQDN and add
  only the required validation CNAME. This incurs a certificate issuance fee.
- **Task 5:** Install pinned AWCP and exact IAM roles/permissions, stage
  secrets, perform supervised enrollment and validate DCV behavior.
- **Task 6:** Browser trust, actual local DCV login, stop/start, retained EIP,
  recovery from long stop and renewal/rollback. No live action before approval.
- **Task 7:** GPU ownership/handoff decision, documentation, cleanup evidence;
  merge separately approved. Issue #62 still covers public TCP 8443 access.

## Tests and controls

[`tests/test_issue43_acm_dcv.py`](../tests/test_issue43_acm_dcv.py)
exercises symlink enrollment refusal, cert/key mismatch and hostname
validation, secure file permissions, successful generation activation,
failure before switch, and post-switch rollback. All checks use local mocks,
no AWS calls and no real service restart. Existing Studio CLI and RES
contract suites must continue to pass.

**Security:** never copy an exported private key into Git, user data, CI
artifacts, an unrestricted SSM command, or publicly readable filesystem.
CloudFormation and IAM plans remain review-only and must not broaden
DCV public ingress. Keep this solution separate from Issue #62.

## Upstream authoritative references

- [AWS Workload Credentials Provider](https://docs.aws.amazon.com/acm/latest/userguide/acm-certificate-automation.html)
- [AWCP source](https://github.com/aws/aws-workload-credentials-provider)
- [AWCP certificate file store](https://github.com/aws/aws-workload-credentials-provider/blob/main/aws_certificatemanager_provider/src/certificate_file_store/certificate_store.rs)
- [Amazon DCV certificate requirements](https://docs.aws.amazon.com/dcv/latest/adminguide/manage-cert.html)
- [ACM exportable public certificates](https://docs.aws.amazon.com/acm/latest/userguide/acm-exportable-public-certificates.html)
- [Issue #43](https://github.com/vision-kwest/studio-workstation/issues/43)
