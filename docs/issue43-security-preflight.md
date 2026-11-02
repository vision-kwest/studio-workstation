# Issue #43 — independent security review and read-only AWS predeployment audit

**Date:** 2026-11-01 (AWS UTC audit crossed into 2026-11-02)
**Status:** **NO-GO FOR LIVE DEPLOYMENT**. Offline branch/Draft PR #67 only.
**Scope:** `dcv.kwesidavis.com`, Route 53 zone `Z02607163QPWX22FZ2Q9M`, `us-east-1`.
No EC2 launch/start, S3 upload, DNS change, IAM modification, certificate issuance,
Lambda/EventBridge creation, or merge was performed during this review.

## Verified existing AWS state (read-only APIs)

| Item | Evidence | Assessment |
| --- | --- | --- |
| Identity | `sts:GetCallerIdentity`: account `401233741942`, current caller is root | Future deployment should use approved non-root admin role |
| Delegated public zone | `route53:GetHostedZone`: `dcv.kwesidavis.com.`, public, expected four AWS NS | PASS |
| Records | `route53:ListResourceRecordSets`: only apex NS and SOA; no A or ACME TXT | PASS — no accidental preexisting endpoint |
| Workstation | `ec2:DescribeInstances`: validated CPU `i-0bbfbdc878d3a2320`, `stopped`, `m7i.large`, `ami-0d6dd9e8265d48906` | Candidate only; no owner approved |
| Other instances | Two other stopped instances (`g4dn.xlarge` and `m7i.xlarge`) | Do not claim the DCV hostname without explicit owner handoff |
| Ownership tags | CPU lacks `StudioDcvDnsHost=dcv.kwesidavis.com` and `StudioDcvDnsManaged=true` | Deployment blocker; no tagging approved |
| VPC/subnet | Expected `vpc-01bbe5d01838f70b2`, `subnet-0986937ce71fb97cb`; public IPv4 on launch enabled | Consistent with existing CPU profile |
| Security group | `sg-0de6dcc3c9e914d89` allows TCP 8443 from `0.0.0.0/0`, no inbound SSH | Security risk, independent Issue #62 |
| SSM | `DescribeInstanceInformation` returned no online managed instances | Stopped host cannot be checked for live DCV version/cert files |
| Production DNS infra | `lambda:ListFunctions` shows no Issue #43 Lambda; `events:ListRules` empty | Not deployed |
| Alarm delivery | `sns:ListTopics` returned zero topics in `us-east-1` | **Blocker**: template requires an approved existing alarm SNS topic |
| Workstation role | `StudioWorkstationSSMRole` has SSM managed policy and two narrow release-read inline policies | No certbot DNS rights |
| Current role's DNS permissions | `iam:SimulatePrincipalPolicy`: `route53:ListHostedZones`, `GetChange`, `ChangeResourceRecordSets` all `implicitDeny` | **Blocker**: Certbot DNS-01 cannot run as-is |
| Immutable Lambda artifact | Release S3 bucket versioning is `Enabled` | Supports later pinned upload; no upload approved |
| Optional CFN template | `cloudformation:ValidateTemplate` succeeded; `CAPABILITY_IAM` required | Offline syntax validation only; not executable permission proof |

No single EC2 instance has been authorized as owner of this DNS identity.
The existing CPU stack uses `ClientCidr=0.0.0.0/0`; trusted TLS
**does not** make its public login endpoint private or identity-aware.

## Reviewed IAM constraints (read-only simulation)

`iam:SimulateCustomPolicy` against `infra/issue43-iam.review-only.json`:

| Candidate action | Result |
| --- | --- |
| Lambda: A UPSERT for `dcv.kwesidavis.com` | `allowed` |
| Lambda: wrong DNS name or NS record | `implicitDeny` |
| Lambda: record conditions omitted | `implicitDeny` |
| Certbot: TXT UPSERT for `_acme-challenge.dcv.kwesidavis.com` | `allowed` |
| Certbot: other TXT name or DCV A record | `implicitDeny` |
| Certbot: record conditions omitted | `implicitDeny` |

Both policies include `Null:false` for name/type/action keys, avoiding
unexpected `ForAllValues` behavior on missing context.
**Important:** these are custom-policy simulations, not proof that the
future roles, permissions boundaries, service trust and SCPs will allow
the calls in production. No IAM policy has been attached.

Certbot Route 53 needs hosted-zone discovery, Route 53 change, and
GetChange permissions. The current shared workstation role cannot do
any of these. Its EC2 credentials are available to *every* instance
using that role; attaching Certbot permissions to it would grant DNS TXT
mutation to every such workstation. A dedicated instance profile or an
approved scoped AssumeRole mechanism is required, with the GPU CLI's
hard-pinned profile constraint reviewed separately. Do not modify that
existing GPU acceptance guard casually.

## Source-level findings

### F1 — DNS deletion without durable ownership proof (BLOCKING)

`runtime/dcv_dynamic_dns.py` reads the current A record and on
`stopping/stopped/shutting-down/terminated` deletes **whatever single A
record happens to be at the hostname**, without proving its IP was
previously written by the approved instance. An operator-managed
replacement record could be unintentionally deleted. Conversely, when
`DescribeInstances` raises `InvalidInstanceID.NotFound` after instance
retirement, the Lambda raises but cannot safely remove its last A
record. DNS can remain stale indefinitely until manually reconciled.

**Required correction before live test:** use an independent, durable
instance-id/last-written-IP ownership record, tightly scoped to
this hostname and with explicit reconciliation/cleanup semantics.
Consider a narrow Standard SSM parameter or equivalent, but review its
permissions, consistency and life cycle **without creating it yet**.
Deletion should only apply if the current DNS target matches the
persistently recorded writer/IP. Treat unknown state as fail-closed,
alarm, and manual recovery; never silently delete another owner's A
record. Test missed stop, full instance deletion, manual DNS override,
retained old IP, stale EC2 event and owner handoff.

### F2 — TLS certificate/key pair rollout is not transactional (BLOCKING)

`scripts/dcv_tls.py` verifies a valid pair and securely stages two
files, but replaces `dcv.key` and `dcv.pem` sequentially. An I/O
failure or service reload between replacements can temporarily present
a mismatched pair. Automatic DCV certificate reload on recent versions
does not make the two-file replacement atomic.

**Required correction:** design a verified generation-switch and
rollback compatible with the real installed DCV version and file
watch behavior; or arrange a controlled maintenance window/reload
with guaranteed rollback. Test an injected failure between replacements,
permission changes, unexpected DCV version and negative live handshake
before treating this as production-safe. Retain protected backups outside
Git/CI/SSM output.

### F3 — DNS re-resolution race in CLI (FIXED IN DRAFT)

Original `verified_dcv_hostname` checked DNS to the stack IP, then
created a TCP connection via the hostname (which could re-resolve to a
different address). Draft fix: open the TCP socket **directly to the
previously verified EC2 public IP**, then perform CA-backed TLS validation
with SNI set to `dcv.kwesidavis.com`. Regression test requires the
exact IP socket target. Default IP-based connections remain unchanged.

### F4 — Missing independent failure coverage (BLOCKING)

Current template alarms on Lambda `Errors` only, and requires an
approved `AlarmTopicArn`, but this account has no SNS topics. A failure
before Lambda invocation, EventBridge target delivery problem, DNS
propagation failure, or missed reconciliation may not trigger that alarm.

**Required correction:** approve one notification destination and
monitor event target failures plus a periodic read-only DNS/instance
convergence check. Test alarms/recipient before leaving the automation
unattended. Avoid overloading older PR #65 expiration/cleanup roles.
Delete/revoke the optional rule, Lambda, alarms, immutable archive
retention and ownership ledger only after the DNS endpoint is safely
retired.

### F5 — Hostname ownership, renewal credentials and exposure (BLOCKING)

Only one approved instance may own `dcv.kwesidavis.com`. No ownership
is approved. The reference CPU instance is stopped and has no tags.
Boot-time Certbot needs approved scoped DNS credentials and actual
packaging and systemd-path validation. After long stop, certificate
issuance must finish before a trusted URL is advertised. Current CPU
SG permits any IPv4 client to reach TCP 8443; this is not addressed by
TLS and is being tracked in Issue #62.

### F6 — Optional CloudFormation template (PARTIAL PASS)

`infra/issue43-dns-reconciler.review-only.json` passed read-only
CloudFormation `ValidateTemplate`, but has never been deployed.
The current environment has no SNS topic for its required alarm ARN.
`scripts/package_issue43_dns.py` generates deterministic Lambda bytes;
no S3 object exists until independently authorized and published, with
SHA-256 + VersionId pinned. Validate IAM trust, role path, expected
EventBridge event semantics, cold-start failures and rollback in a
separate supervised acceptance plan.

## Exact reproducible **read-only** predeployment commands

Use the already authorized admin account and `us-east-1`. These commands
do not create resources or send SSM commands to stopped instances.

```bash
aws sts get-caller-identity --no-cli-pager
aws route53 get-hosted-zone --id Z02607163QPWX22FZ2Q9M --no-cli-pager
aws route53 list-resource-record-sets \
  --hosted-zone-id Z02607163QPWX22FZ2Q9M --no-cli-pager
aws ec2 describe-instances --region us-east-1 \
  --instance-ids i-0bbfbdc878d3a2320 \
  --query 'Reservations[].Instances[].{Id:InstanceId,State:State.Name,IPv4:PublicIpAddress,Tags:Tags,IAM:IamInstanceProfile.Arn}' --no-cli-pager
aws ec2 describe-security-groups --region us-east-1 \
  --group-ids sg-0de6dcc3c9e914d89 \
  --query 'SecurityGroups[].{Id:GroupId,Ingress:IpPermissions}' --no-cli-pager
aws iam list-attached-role-policies --role-name StudioWorkstationSSMRole --no-cli-pager
aws iam list-role-policies --role-name StudioWorkstationSSMRole --no-cli-pager
aws lambda list-functions --region us-east-1 \
  --query 'Functions[].FunctionName' --no-cli-pager
aws events list-rules --region us-east-1 --no-cli-pager
aws sns list-topics --region us-east-1 --no-cli-pager
aws cloudformation validate-template --region us-east-1 \
  --template-body file://infra/issue43-dns-reconciler.review-only.json --no-cli-pager
```

For read-only IAM principal simulation (role is currently denied):

```bash
aws iam simulate-principal-policy \
  --policy-source-arn arn:aws:iam::401233741942:role/StudioWorkstationSSMRole \
  --action-names route53:ListHostedZones --resource-arns '*' \
  --query 'EvaluationResults[].{Action:EvalActionName,Decision:EvalDecision}' --no-cli-pager
```

Never run an unreviewed `deploy`, `create-stack`, `put-rule`,
`change-resource-record-sets`, `attach-role-policy` or certificate
issuance during this gate.

## Next approval gate

1. Correct F1/F2/F4 **offline** and add failure-injection tests.
2. Decide single-instance hostname ownership and how to scope Certbot
   permissions without altering proven GPU launcher/security assumptions.
3. Prepare exact immutable Lambda publication, IAM, DNS, alarm and
   cleanup changeset as **review only**.
4. Check in for explicit permission before provisioning any new billable
   AWS resources, changing DNS/IAM, starting EC2 or issuing certificates.
5. Only after those approvals conduct supervised CPU first, then GPU
   certificate, stop/start and RES parity acceptance.

**Do not merge Draft PR #67 or modify Image Builder/RES.**
