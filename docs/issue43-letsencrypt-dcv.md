# Issue #43 — Let's Encrypt DNS-01 + dynamic DNS for Amazon DCV (review only)

**Status:** feature-branch implementation and offline tests. **NOT DEPLOYED.**
No EC2, Lambda, EventBridge, certbot, IAM, DNS A/TXT or RES/AMI changes were made
by this branch. The Route 53 public hosted zone and Bluehost NS delegation were
created separately before this branch (zone `Z02607163QPWX22FZ2Q9M`).

## Scope and invariants

- HTTPS endpoint: `https://dcv.kwesidavis.com:8443`. Parent domain remains at
  Bluehost; only the delegated subdomain is hosted in Route 53.
- One explicitly approved EC2 instance owns this name at a time.
- The original RES 2026.06 DCV initialization, system authentication, console
  creation, NVIDIA/Xorg/GDM, Image Builder, archives and GPU launch guard remain
  **unchanged**.
- No SSH/HTTP ingress, public networking, certificates, or IAM permission
  changes are included in this feature branch.
- This provides TLS identity, **not** a replacement for the separate
  public-ingress / zero-trust access decision in Issue #62.

## Component 1: dynamic A record (NOT wired to AWS yet)

`runtime/dcv_dynamic_dns.py` is a dependency-injected Lambda handler
(`lambda_handler`) using AWS SDK at runtime; account and region must be
approved before provisioning. It enforces:

1. Exact `STUDIO_DNS_INSTANCE_ID` Lambda environment pin, plus expected
   `us-east-1` EC2 event shape/region and the exact `StudioDcvDnsHost =
   dcv.kwesidavis.com` / `StudioDcvDnsManaged = true` instance tags.
2. Reconcile current DescribeInstances state instead of trusting event ordering.
3. Running + eligible public IPv4 -> UPSERT the sole A record (TTL 60).
   Stopping/stopped/shutting-down/terminated -> DELETE exact existing A record.
   Pending or running-without-public-IP -> defer. All unexpected owners,
   missing instances and alias/multi-value A records fail closed.
4. Read-before-write; unchanged records make no calls to ChangeResourceRecordSets.
5. Five-minute EventBridge scheduled reconciliation (separate from state-change
   notifications) must be configured to recover from missed/early state events.

The **review-only** EventBridge event pattern, schedule payload, pinned Lambda
environment and tagging plan are in `infra/issue43-eventbridge.review-only.json`.
Provisioning requires an approved, real instance ID and an independent rollback
plan. Do not deploy a broad account-wide event handler. Never change the
currently scoped GPU acceptance IAM/Scheduler safeguards for this work.

A record deletion intentionally removes stale endpoints when stopped. DNS caching
may retain an old A record for its TTL or longer; the CLI checks the IP match and
TLS handshake before publishing the trusted URL. Do not attach this single
hostname to simultaneous CPU and GPU instances. If a pinned instance is gone
before it can be described, the handler **raises and requires manual recovery**:
it never assumes an empty inventory proves DNS is safe.

## Component 2: on-host Let's Encrypt (NOT installed)

`scripts/dcv_tls.py ensure --email OPERATOR_EMAIL` runs only as root. It invokes
Certbot with `--dns-route53`, `--keep-until-expiring`, hostname and certificate
name fixed to `dcv.kwesidavis.com`. It then verifies certificate hostname,
remaining lifetime (at least 48 hours), and matching public/private keys using
OpenSSL before staging files as root and moving them into:

- `/etc/dcv/dcv.pem` (full chain)
- `/etc/dcv/dcv.key` (private key)

Both files are owned by the DCV service account and mode 0600. No private
keys go into GitHub, CI, SSM Run Command arguments, or CloudFormation user data.
The original `/etc/dcv/dcv.conf` is never edited, and no DCV service restart
or session recreation is requested. On DCV versions >=2022.0, Amazon DCV reloads
updated TLS material automatically. Actual running DCV version and SSL paths
must be checked in the first supervised guest test.

**Important limitations:** certificate/key filenames must be validated for the
actual DCV package, replacement is sequential rather than pair-atomic, and a
file-system failure between replacements requires supervised rollback. The
initial issuance requires approved Internet egress, public DNS delegation,
Certbot with `python3-certbot-dns-route53` or equivalent, root access, a valid
contact email and an instance profile with the scoped DNS TXT permissions.
Do not run until all permissions are reviewed. Normal renewal doesn't require
a website, port 80 or EC2 public IP; DNS-01 uses Route 53. While EC2 is stopped,
the on-host timer cannot run. The boot timer reruns issuance/renewal after start,
even following a long stop (possibly with an initial reconnection delay).

The review-only systemd units are:
`infra/issue43-studio-dcv-tls.service` and
`infra/issue43-studio-dcv-tls.timer`. They reference a root-owned
`/etc/studio/dcv-acme.env` (`STUDIO_ACME_EMAIL=...`) and an explicitly installed
copy at `/opt/studio-workstation/scripts/dcv_tls.py`; these are NOT copied into
an instance automatically. Review unit paths and Certbot packaging on Ubuntu
24.04 before enabling. Failures must be alerted (CloudWatch/SSM/monitoring
to be designed for deployment) instead of silently accepting expired TLS.

## Component 3: Studio CLI opt-in (offline-ready)

The existing IP-based CLI behavior is unchanged. Once a workstation is
configured and *live* validation succeeds, use:

```bash
python3 studio workstation connect --stack STACK --hostname dcv.kwesidavis.com
python3 studio workstation status --stack STACK --hostname dcv.kwesidavis.com
```

The CLI resolves the hostname, requires its IPv4 answer set to match the
exact EC2 stack's public IPv4, performs a system-CA-verified TLS handshake
with SNI / hostname validation on port 8443, then (and only then) prints the
trusted HTTPS URL. It raises rather than claiming trust for stale DNS,
self-signed certificates, expired certificates, stopped instances or
unexpected hostnames. The existing `connect --ssm` remains separate.

This is an **opt-in validation flag**, not an assertion that trusted TLS is
already installed. It does not change `create`, default `status`,
default `connect`, `stop`, `start` or `destroy`.

## IAM and security gates

`infra/issue43-iam.review-only.json` contains **two distinct policy sketches**:
- DNS Lambda: DescribeInstances (AWS requires resource '*'), list records for
  the one hosted zone; change only that hostname and type A, UPSERT/DELETE.
- EC2 Certbot: discover hosted zones, GetChange, change only the exact
  `_acme-challenge.dcv.kwesidavis.com` TXT record in the approved hosted zone.

Do **not** attach both policies to the existing shared workstation role;
this would widen CPU/GPU credential powers. Review separating Certbot's route53
permission on a purpose-specific instance role or a narrowly controlled assume-role
path. Validate actual Certbot IAM behavior with a sandbox/staging action and
AWS policy simulation before production approval.

## Controlled deployment / rollback gates (approval required)

1. Review baseline account, region, IAM and instance ownership, and the current
   Route 53 delegation/record inventory. Verify no existing A/TXT conflicts.
2. Approve dedicated Lambda IAM role and Route 53 permissions, tagging of **one**
   approved instance, event rule and five-minute schedule. Test both "running"
   and "stopped" transitions, retry behavior, and failure monitoring.
3. Approve minimum privilege Certbot permissions, install Certbot and the
   Route 53 plugin, configure guarded unit/timer and root-owned contact config.
4. Perform supervised issuance and verify full-chain TLS, DNS A response,
   local Linux/DCV login, console READY, GPU Xorg/OpenGL where applicable,
   and persistence after stop/start. Test long-stop simulation and renewal.
5. Verify rollback: disable the EventBridge rule and reconciliation schedule,
   remove only the proven-owned DNS A record, revoke Certbot permissions,
   uninstall timer if desired, restore original DCV TLS material from a
   protected backup, check DCV login and validate every resource removed.
   Keep the delegated public hosted zone unless its cost is explicitly retired.

**Open question:** one hostname for one instance cannot serve both CPU and GPU
concurrently. Explicit ownership handover or separate per-profile names must
be designed before production multi-instance support.

## References

- [Amazon DCV TLS certificate management](https://docs.aws.amazon.com/dcv/latest/adminguide/manage-cert.html)
- [Certbot Route53 plugin](https://certbot-dns-route53.readthedocs.io/en/stable/)
- [Amazon Route 53 scoped record permissions](https://docs.aws.amazon.com/Route53/latest/DeveloperGuide/resource-record-sets-permissions.html)
- [EC2 event notifications](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/monitoring-instance-state-changes.html)
- [Issue #43](https://github.com/vision-kwest/studio-workstation/issues/43)
- [Issue #62](https://github.com/vision-kwest/studio-workstation/issues/62)
