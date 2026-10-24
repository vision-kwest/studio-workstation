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

The approved release destination reuses the existing bucket and separates the
Studio Workstation version from its upstream RES version:

```text
s3://research-engineering-studio-us-east-1/
  studio-workstation/releases/v0.1.0/res-2026.06/
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
python3 scripts/release.py --publish --version v0.1.0
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
release prefix (`studio-workstation/releases/v0.1.0/res-2026.06`), rather
than the earlier experimental `cpu-v1` prefix. CPU and GPU builds may share
the same released application artifacts.
