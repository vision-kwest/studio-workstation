# Release preparation

From a fresh checkout:

```bash
git clone https://github.com/vision-kwest/studio-workstation.git
cd studio-workstation
python3 scripts/release.py
```

The script downloads the two original RES 2026.06 archives **only when absent** from `~/studio-release/original/`, verifies their pinned SHA-256 hashes, builds each derived archive twice, verifies byte-identical reproducibility, runs the archive preservation tests, and writes outputs to `~/studio-release/derived/` plus `~/studio-release/release-manifest.json`.

Requires Python 3, the AWS CLI, and **read access** to `s3://research-engineering-studio-us-east-1/releases/2026.06/`. Existing verified originals are reused without network access. If an existing original fails checksum verification, the script **stops without overwriting it**. Downloaded files are verified before being moved into place.

Options: `--release-dir PATH`, `--offline` (no downloads), `--region us-east-1`.

**No S3 uploads, CloudFormation changes, or EC2 deployment.** Publishing the derived release is a separate approval step.

## Immutable release publication — Option 2

The release destination is an account-owned, private bucket created by
`infra/release-bucket.yaml`. This replaces the original proposal to publish
to the upstream RES bucket, which permits downloads but denies administrative
and publication checks. The source bucket remains read-only.

```text
s3://studio-workstation-releases-401233741942-us-east-1/
  releases/v0.1.1/res-2026.06/
    installation-scripts.tar.gz
    virtual-desktop.tar.gz
    release-manifest.json
```

Build and test locally (default; no AWS writes):

```bash
python3 scripts/release.py
```

Publish only after explicit authorization:

```bash
python3 scripts/release.py --publish --version v0.1.1
```

Publishing validates local hashes, uses S3 `PutObject` with `If-None-Match: *`
for each object, reads each uploaded object back and verifies SHA-256, and
publishes the manifest **last**. Existing release objects are never overwritten
or deleted. The release manifest records the source Git commit, upstream RES
version, release version, object prefix and derived SHA-256 hashes.

**Failure handling:** S3 does not provide an atomic transaction across three
objects. A failure can leave a partial release prefix; do not reuse that
version. Choose a new version after inspecting the failure. The manifest being
present and verified is the publication-completion marker.

Requires AWS CLI for original downloads and `boto3` for publication
(`python3 -m pip install -r requirements.txt` if needed). The publishing
identity needs `s3:PutObject`, `s3:GetObject` and `s3:ListBucket` or
appropriate object existence-check permissions. No EC2, IAM, or CloudFormation
resources are created by this command.

**Deployment note:** `runtime/render_workstation.py` must be given the same
release prefix (`releases/v0.1.1/res-2026.06`), rather
than the earlier experimental `cpu-v1` prefix. CPU and GPU builds may share
the same released application artifacts.

## Release bucket provisioning (separate approval)

After reviewing and merging the bucket infrastructure PR, an authorized AWS
administrator can create the release bucket in `us-east-1` with:

```bash
aws cloudformation deploy \
  --stack-name studio-workstation-release-bucket \
  --template-file infra/release-bucket.yaml \
  --region us-east-1
```

This is a **resource-creating command**. Do not run it until explicitly
authorized. The bucket uses SSE-S3 encryption, blocks all public access,
enables S3 versioning, enforces TLS and is retained if the stack is deleted.
The template does not grant public or cross-account access. Use a properly
authorized IAM role rather than account root.

The original RES archives remain read-only inputs from
`s3://research-engineering-studio-us-east-1/releases/2026.06/`.
The derived release is written only to the dedicated account-owned bucket.
Before deployment, render the workstation template with:

```bash
python3 runtime/render_workstation.py \
  --bucket studio-workstation-releases-401233741942-us-east-1 \
  --prefix releases/v0.1.1/res-2026.06 \
  --user workstation \
  --output ~/studio-release/workstation-cpu.json
```

## Workstation role permissions and release upgrades

The instance profile `StudioWorkstationSSMRole` must be able to read the
published release **before** cloud-init starts. The policy is version-independent
and limited to the two RES-derived archive filenames:

`infra/workstation-release-read-policy.json`

Apply the policy using an IAM-authorized administrator identity, **from CloudShell
or another administrative environment, not from the workstation SSM session**:

```bash
aws iam put-role-policy \
  --role-name StudioWorkstationSSMRole \
  --policy-name StudioWorkstationReleaseRead \
  --policy-document file://infra/workstation-release-read-policy.json
```

The `*` in `releases/*/res-2026.06/` is intentional: it permits new
Studio Workstation release versions without granting object write access.
It can match more than semantic version strings; scope remains limited to the
specified bucket, RES version path and two archive names. Keep the
`AmazonSSMManagedInstanceCore` managed policy attached separately.

Before deploying a new release, verify its **installation-scripts.tar.gz**
and **virtual-desktop.tar.gz** objects exist and that the EC2 role can read
them. An administrator's successful S3 read does **not** establish that the
EC2 instance role is authorized.

### Recovery: S3 403 during bootstrap

Symptoms: `/root/bootstrap/logs/userdata.log` contains a
`HeadObject 403 Forbidden`, followed by a missing `install.sh` and
`cloud-init status --long` reporting a `scripts-user` error.

The original user-data may write
`/root/bootstrap/semaphore/pre_install_finished.lock` even after a failed
archive download. This **false completion marker** can cause the next boot
to skip the download and retry the missing installer. Do not assume that
the marker is absent.

1. Identify the environment using `hostname` and
   `aws sts get-caller-identity`. The workstation should report its
   EC2 assumed-role session ARN, not a CloudShell IAM user ARN.
2. Verify both S3 objects are readable **from the workstation**, including
   as root when cloud-init uses root credentials.
3. Inspect `/root/bootstrap/semaphore/`,
   `/root/bootstrap/latest`, and `/root/bootstrap/logs/userdata.log`.
   Confirm that the installer is absent before treating the extraction as
   incomplete. Preserve the logs before any cleanup.
4. Only for this verified failed-download state, move the incomplete
   `/root/bootstrap/installation-scripts` directory aside, remove the
   `/root/bootstrap/latest` symlink **only if it points to that directory**,
   and remove the invalid `pre_install_finished.lock`. Do not remove
   other RES stage locks or reset all cloud-init state.
5. After confirming the intended EC2 instance ID and the corrected state,
   use the original cloud-init reboot-entry mechanism to retry installation.
   The installer may perform additional reboots. Do not interrupt active
   package builds or manually trigger overlapping bootstrap runs.

These are guarded recovery instructions, **not** an automatic cleanup
procedure. A different failure state requires different handling. A
CloudFormation `CREATE_COMPLETE` status does not establish that the
in-instance RES bootstrap or DCV console is ready.

### v0.1.1 deployment validation

For v0.1.1, render using `--prefix releases/v0.1.1/res-2026.06`.
Verify the EC2 role's read access before launch. After launch, check
`cloud-init status --long`, SSM connectivity across reboot stages,
Supervisor logs, `dcvserver` status and TCP 8443, and the automatic
console session. Do not treat `app_installed.lock` alone as DCV readiness.

## CPU standalone end-to-end acceptance — 2026-10-25

**Observed working instance:** `i-09122a298e3878a57` in `us-east-1`,
Ubuntu 24.04, `m7i.large`, RES 2026.06-derived runtime.
The browser connected to Amazon DCV on port 8443, authenticated as the
local Linux user `workstation`, and displayed an interactive GNOME
desktop with a working terminal. `dcv describe-session console --json`
reported `status: running`, owner `workstation`, display layout
2560×1600. The DCV server was active and listening on TCP 8443.
Cloud-init finished with no errors, and SSM Session Manager worked.
The one-shot Supervisor application reported `EXITED` after logging
`DCV session placement complete with state: READY`; that status alone
is not a failure.

**Important distinction:** v0.1.2 was **manually hotfixed** onto the
already-provisioned v0.1.1 instance by extracting
`standalone_bootstrap.py` from the verified v0.1.2 nested application
archive. The installed module SHA-256 was
`696534dbe781128f70abaa978a822f05cbdaf9fc74dca14350c7cf3155477f9f`.
A successful *fresh* v0.1.2 deployment has **not yet been observed**.
The committed overlay and published v0.1.2 archive contain the same
logging change, but fresh provisioning is a separate acceptance gate.
Do not claim a reproducible fresh deployment until it passes.

### Fresh deployment prerequisites and verification

1. Publish a new immutable release from merged `main` using
   `python3 scripts/release.py --publish --version VERSION`. Do not
   overwrite an existing release.
2. Ensure `StudioWorkstationSSMRole` has
   `AmazonSSMManagedInstanceCore` and the version-independent
   `StudioWorkstationReleaseRead` inline policy. Verify both archive
   objects are readable **using the instance role**. The published
   release prefix must match the rendered user data.
3. Render with `python3 runtime/render_workstation.py --bucket BUCKET
   --prefix releases/VERSION/res-2026.06 --user workstation
   --output OUTPUT.json`, then run `aws cloudformation validate-template`.
4. Deploy to a new test stack only after reviewing billable resources,
   image, subnet, client /32 CIDR and existing instance profile. Allow
   RES to complete its package build and reboot stages.
5. From the instance via SSM run
   `sudo bash /path/to/studio-workstation/scripts/check_cpu_workstation.sh`.
   This is a **read-only** smoke check; the repository must be available
   on the instance or the script copied there.
6. From the approved client network, test DCV HTTPS, local Linux
   authentication, GNOME interaction, terminal and reconnect.
   The account is provisioned with a **locked password**. Interactive
   login requires a separate approved credential-enrollment step
   (`sudo passwd workstation` through SSM). Do not store passwords in
   user data, source control, logs, or CloudFormation parameters.

### Stop/start lifecycle acceptance (requires approval)

Before stopping, record `InstanceId`, EBS volume ID, current
`dcv describe-session console --json`, SSM status and current public
IP. Save open work. From an authorized **CloudShell** identity, use:

```bash
INSTANCE=i-09122a298e3878a57
aws ec2 stop-instances --instance-ids "$INSTANCE" --region us-east-1
aws ec2 wait instance-stopped --instance-ids "$INSTANCE" --region us-east-1
aws ec2 start-instances --instance-ids "$INSTANCE" --region us-east-1
aws ec2 wait instance-status-ok --instance-ids "$INSTANCE" --region us-east-1
```

Afterward verify SSM Online and run the acceptance script **on the
instance**. Confirm DCV is reachable, `console` remains running and
local login works. A stopped instance with an automatically assigned
public IP normally receives a **new public IP** when started; update
client URLs and any DNS records. A session may need to be recreated;
record that as a lifecycle failure instead of assuming persistence.
Do not delete the CloudFormation stack or root EBS volume.

### Trusted DCV TLS — deployment decision

The observed browser warned `Not Secure`: DCV is reachable over
HTTPS but the presented certificate is not trusted by the client.
Do not suppress TLS verification or treat a self-signed certificate
exception as production trust.

**Recommended internet-facing path:** reserve an Elastic IP (billable)
or use an automated DNS update for the changing EC2 public IP; create
a DNS name you control (for example `dcv.example.com`); obtain a
publicly trusted certificate for **that name** using a validated ACME
DNS-01 challenge (or another trusted CA); configure the DCV server's
documented certificate/key paths with root-only permissions; restart
DCV during an approved maintenance window; verify the browser connects
to `https://YOUR_DNS_NAME:8443` without certificate warnings and
that the cert chain, SAN, hostname and renewal process are correct.
Certificate renewal must be automated and monitored. Never commit
private keys to Git or embed them in user data.

**Alternative private path:** use a managed VPN or private overlay
network and a client-trusted private CA, with DCV restricted to that
network. A VPN alone does **not** make an untrusted certificate trusted.
Select a path based on the domain, IP stability, client network and
operational preferences before making infrastructure changes.

No certificate, DNS record, Elastic IP, VPN or renewal automation
has been provisioned by this repository change.
