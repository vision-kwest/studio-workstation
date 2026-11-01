# PR #65 — S3 publication record (completed November 1, 2026)

> **Current outcome — publication, role access and acceptance COMPLETE:** The PR #65 installer archive was published under its own immutable key/VersionId, the GPU instance-profile `s3:GetObjectVersion` permissions were updated, and the live GPU CLI acceptance subsequently passed. The historical command snippets below preserve the *preapproval publication plan* and should not be run again. Source of truth for the two distinct archive keys, hashes and versions: [runtime README](../runtime/README.md); current launch and cleanup: [Studio CLI](studio-cli.md).

## Publication result and current state

**COMPLETED — no further upload required.** The user authorized their existing AWS root session for development and published exactly one new, checksum-verified installation archive via CloudShell. This section supersedes the historical proposed-command, non-root preference and original no-upload language below. Do **not** run the historical upload instructions again.

- Key: `releases/pr65-pinned-download-084e704e/res-2026.06/installation-scripts.tar.gz`
- S3 VersionId: `bN0To3QzfVqVsN5P_6YmTraGIQuHcrtB`
- SHA-256 verified by CloudShell: `084e704e5c3e79152b0324664f153a826ced40b878c1a87e31cf62e4f9551bb5`
- Independent AWS read-only HeadObject: 42,816 bytes, AES256, exact VersionId. ListObjectVersions: one current version, no delete markers.
- App archive: unchanged, still at original Task 7 key and pinned VersionId.
- GitHub renderer/CLI pins and release manifest updated on the draft PR #65 branch.
- **Not approved for EC2 launch:** scoped cleanup safeguard not deployed/verified; the EC2 instance role lacks `s3:GetObjectVersion` for both pinned archives (read-only IAM simulation `implicitDeny`). A review-only exact-two-key policy is in `infra/pr65-runtime-version-read.review-only.json`.

No new GitHub Actions archive build or S3 upload is needed. Do not merge PR #65 or modify Image Builder without explicit approval.

## Freeze artifact and destination

Source: GitHub Actions run 41254585650, artifact ID 13062033480, `task7-verified-res-archives-533e100ea3d1b6798a024efa4451e436d9bc04fd`. The run's source commit is `533e100ea3d1b6798a024efa4451e436d9bc04fd`. Artifact ZIP digest from GitHub API is `sha256:91a425b2fc7fbe5e726a89bad564ec02c63f015c2f8caf470422f4d322635ac4`; this ZIP digest is NOT the installation archive digest. ZIP artifact expires 2026-11-07.

Install archive member: `installation-scripts.tar.gz`; verified SHA-256 `084e704e5c3e79152b0324664f153a826ced40b878c1a87e31cf62e4f9551bb5`. Expected unchanged app archive SHA-256 `e7686d6240db9062b5ea62f94859bd657de8c1231e8a3ba097704f9faa45d147`.

Proposed **new, single-object** destination (not existing):
```
s3://studio-workstation-releases-401233741942-us-east-1/releases/pr65-pinned-download-084e704e/res-2026.06/installation-scripts.tar.gz
```
This new prefix does not currently contain the virtual-desktop archive. **Before launch**, adapt the GPU renderer to use this new bootstrap URI while continuing to use the old pinned virtual-desktop URI, or separately authorize a copy of the unchanged app archive. The current renderer assumes both archives share one prefix. Do not change or overwrite the previous known-working release.

## Read-only findings

AWS bucket versioning: Enabled. Default SSE-S3 AES256. Public access block all four settings enabled; bucket policy is not public. Existing old prefix holds exactly the known GPU template and two original derived archives. IAM `SimulateCustomPolicy` with a proposed exact-object `s3:PutObject` grant: intended new object allowed, previous object, virtual-desktop object, and other bucket implicitly denied (4/4). This does not establish the actual uploading principal's effective IAM, permissions boundary, SCP, bucket policy or VPC endpoint behavior. Current inspected AWS caller is account root: **do not use root to upload**. A dedicated temporary scoped publisher role with the exact object ARN and no delete permission must be approved separately.

## Prepared operator commands — DO NOT RUN without separate upload authorization

```bash
set -euo pipefail
RUN=41254585650
ARTIFACT=task7-verified-res-archives-533e100ea3d1b6798a024efa4451e436d9bc04fd
BUCKET=studio-workstation-releases-401233741942-us-east-1
KEY=releases/pr65-pinned-download-084e704e/res-2026.06/installation-scripts.tar.gz
EXPECTED=084e704e5c3e79152b0324664f153a826ced40b878c1a87e31cf62e4f9551bb5
mkdir -p /tmp/pr65-stage
gh run download "$RUN" --repo vision-kwest/studio-workstation --name "$ARTIFACT" --dir /tmp/pr65-stage
printf '%s  %s\n' "$EXPECTED" /tmp/pr65-stage/installation-scripts.tar.gz | sha256sum -c -
tar -tzf /tmp/pr65-stage/installation-scripts.tar.gz >/dev/null
python3 - <<'PY'
import tarfile
p = "/tmp/pr65-stage/installation-scripts.tar.gz"
with tarfile.open(p, "r:gz") as tf:
    matches = [m for m in tf if m.isfile() and m.name.removeprefix("./") == "scripts/common/linux/install_app.sh"]
    assert len(matches) == 1, "RES installer archive member missing or ambiguous"
    source = tf.extractfile(matches[0]).read()
    assert b"s3api get-object" in source and b"--version-id" in source
    assert b"sha256sum --check --status" in source
PY
aws sts get-caller-identity --query Arn --output text
# Follow the safe read-only exact-key preflight in docs/pr65-s3-final-readonly-preflight.md.
# This session currently resolves to root; do not use root to publish.
echo "STOP: No exact-key publisher IAM role or S3 upload authorization exists."
exit 1
# THE FOLLOWING MUTATING COMMANDS ARE REFERENCE-ONLY, NOT REACHABLE AS A SCRIPT.
aws s3api put-object --region us-east-1 --bucket "$BUCKET" --key "$KEY" \
  --body /tmp/pr65-stage/installation-scripts.tar.gz --server-side-encryption AES256 \
  --if-none-match '*'
# Capture VersionId from put-object response. Never upload again if the key already exists.
aws s3api head-object --region us-east-1 --bucket "$BUCKET" --key "$KEY" --version-id 'REPLACE_WITH_RETURNED_VERSION_ID'
aws s3api get-object --region us-east-1 --bucket "$BUCKET" --key "$KEY" \
  --version-id 'REPLACE_WITH_RETURNED_VERSION_ID' /tmp/pr65-stage/verify-versioned.tar.gz
printf '%s  %s\n' "$EXPECTED" /tmp/pr65-stage/verify-versioned.tar.gz | sha256sum -c -
```

`gh run download` retrieves the ZIP and extracts it. The archive member path and pinned-download contents must be inspected before any upload. The put-object request is conditional to prevent overwriting the chosen new key. `head-object` 404 is a required precondition, not a failure to ignore. The AWS CLI v2 reference supports `--if-none-match '*'`, but the eventual operator environment must verify its installed CLI version. Do not treat the `head-object` 404 as an ordinary success in `set -e` scripts; use the explicit error handling and version-listing checks in the final read-only preflight.

## Post-upload requirements (not yet authorized)

Record returned VersionId, exact key, checksum, AWS caller identity and GitHub artifact provenance in the release manifest. Update GPU renderer and CLI preflight to the **new bootstrap key + VersionId + SHA-256** while retaining old app archive URI/version/hash. Revalidate exact rendered CloudFormation, repeat CI, and confirm the instance profile can `s3:GetObjectVersion` on the new key. Maintain fail-closed scoped GPU launch gate until these checks pass. No EC2 launch authorized by publication.

## Rollback

Because the new object has a unique key and `--if-none-match '*'`, rollback is **not** overwriting or deleting the existing validated release. Keep the CLI scoped GPU launch gate active and revert the unpublished renderer/pin changes if necessary. Preserve the new version for audit; do not delete the object or introduce a delete marker without separate authorization. If a later runtime test fails, stop the test and use independent cleanup. Do not point production to the staged release.

## Final publisher and destination verification

See [Final read-only preflight](pr65-s3-final-readonly-preflight.md) for independently verified ZIP and archive SHA-256, exact-key absence via 404 plus empty version/delete-marker listings, the actual existing publisher role's implicit-deny result, the proposed exact-key policy's 10/10 simulator results, and corrected operator preflight commands. The original upload snippet above intentionally contains an unconditional `exit 1` before the `put-object` example; no AWS upload has been approved.
