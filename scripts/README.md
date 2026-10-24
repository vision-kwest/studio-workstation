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
