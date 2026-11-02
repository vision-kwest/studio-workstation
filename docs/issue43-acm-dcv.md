# Issue #43 — Simple stable-IP + ACM trusted DCV (Task 2 / OFFLINE)

**Status: Draft PR #67. Nothing in this file has been deployed.**
This plan supersedes the bespoke Lambda/EventBridge/DynamoDB/DDNS/Certbot
architecture removed from the feature branch. The approved DNS delegation
was created before this PR.

## Objective, boundaries, and ownership

- Hostname: `dcv.kwesidavis.com:8443`.
- Route 53 public hosted zone `Z02607163QPWX22FZ2Q9M` already exists with
  NS/SOA only. Bluehost remains authoritative for `kwesidavis.com`.
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
