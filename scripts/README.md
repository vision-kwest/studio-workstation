# RES-derived release preparation and publication

**Current documentation note (post-PR #65):** The v0.1.1/v0.1.3 commands below are historical release examples, **not** the pinned keys consumed by the accepted GPU CLI. For workstation creation and daily use see [Studio CLI](../docs/studio-cli.md) and [Quick Start](../README.md#quick-start). The GPU CLI fetches two separately pinned archive versions recorded in [runtime provenance](../runtime/README.md). Old S3 publication and IAM review blockers were resolved during the live acceptance test; do not repeat one-time upload/policy changes merely because an older example still describes them.


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

**Historical v0.1.1 deployment note:** The example below uses its matching `releases/v0.1.1/res-2026.06` prefix. **Do not use that single prefix for the accepted GPU CLI.** The installation scripts and application archive live at different immutable S3 keys; see [current GPU requirements](../docs/studio-cli.md#gpu-workstation-tested-acceptance-scope).

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

The instance profile `StudioWorkstationSSMRole` must be able to read the published release **before** cloud-init starts. The historical policy below provides broad filename-based **`s3:GetObject`** reads:

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

**Additional current GPU requirement:** The PR #65 pinned downloader uses `s3:GetObjectVersion` for **two exact objects**. That narrow permission was separately applied to `StudioWorkstationSSMRole` and validated during live acceptance. The older `s3:GetObject` grant alone is insufficient. See `infra/pr65-runtime-version-read.review-only.json` for the reviewed IAM policy shape and [runtime provenance](../runtime/README.md) for the exact keys and versions. Do not blindly reapply older IAM policies.

Before deploying any new release, verify both archive objects and **effective EC2 instance-profile access**. An administrator's successful S3 read does not prove the EC2 role can fetch the required version.

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
