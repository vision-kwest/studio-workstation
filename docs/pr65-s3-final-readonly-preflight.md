# PR #65 — Final read-only S3 publisher and destination preflight

> **Historical S3 upload NO-GO checkpoint — superseded:** This read-only proposal captured the state *before* the PR #65 installer was actually published. The new installer was subsequently uploaded to `releases/pr65-pinned-download-084e704e/res-2026.06/installation-scripts.tar.gz`, VersionId `bN0To3QzfVqVsN5P_6YmTraGIQuHcrtB`; the previous application archive remained separately pinned. Both were downloaded successfully by the live GPU acceptance instance after the EC2 role obtained `s3:GetObjectVersion`. Do **not** interpret the original 'unused destination' / 'no upload authorized' statements below as current S3 state. See [runtime provenance](../runtime/README.md).

**Decision: NO-GO for S3 upload. No AWS writes performed.** This document reports observations made on 2026-10-31. All checks must be repeated immediately before a separately authorized write.

## Exact staged source

- GitHub Actions build: https://github.com/vision-kwest/studio-workstation/actions/runs/41254585650 (completed: success).
- Artifact ID: 13062033480, name: task7-verified-res-archives-533e100ea3d1b6798a024efa4451e436d9bc04fd.
- Source commit: 533e100ea3d1b6798a024efa4451e436d9bc04fd; provenance file confirms RES 2026.06 and UNPUBLISHED_DO_NOT_LAUNCH.
- ZIP size: 470052 bytes; ZIP SHA-256 independently recomputed: 91a425b2fc7fbe5e726a89bad564ec02c63f015c2f8caf470422f4d322635ac4 (matches GitHub artifact API digest).
- ZIP member installation-scripts.tar.gz: 42816 bytes; independently recomputed SHA-256: 084e704e5c3e79152b0324664f153a826ced40b878c1a87e31cf62e4f9551bb5.
- ZIP member virtual-desktop.tar.gz: 426437 bytes; independently recomputed SHA-256: e7686d6240db9062b5ea62f94859bd657de8c1231e8a3ba097704f9faa45d147.
- Inspected exact installation archive member ./scripts/common/linux/install_app.sh; contains s3api get-object, --version-id, STUDIO_APP_VERSION_ID, STUDIO_APP_SHA256 and sha256sum --check --status. Exactly one matching member.
- Comparing source commit with current reviewed PR head changed only docs/pr65-gpu-cli-acceptance-gate.md and docs/pr65-s3-publication-plan.md at the time checked; installer files remained unchanged.
- Artifact expires on 2026-11-07. A fresh deterministic rebuild and independent hash check will be necessary after expiry.

## Exact destination, presently unused

- Bucket: studio-workstation-releases-401233741942-us-east-1; region us-east-1.
- Key: releases/pr65-pinned-download-084e704e/res-2026.06/installation-scripts.tar.gz.
- AWS s3:HeadObject returned 404 Not Found for this key.
- AWS s3:ListObjectsV2 returned no current objects at this exact prefix; ListObjectVersions returned no versions and no delete markers.
- Bucket versioning Enabled; SSE-S3 AES256 default encryption; all four public-access block switches enabled; bucket policy nonpublic and its only statement denies insecure transport.
- Prior known-working archive and S3 VersionId remain unchanged. Conditional upload MUST target only this new key.
- Absence is a point-in-time observation, not permission to upload; recheck all versions/delete markers immediately before publication.

## Publisher IAM — existing effective policy is NOT suitable

- Connected AWS identity from STS: arn:aws:iam::401233741942:root. Do not upload from root.
- IAM ListRoles found one historical publisher: studio-task7-pr61-oidc-publisher. Its OIDC trust is restricted to refs/heads/feature/res-2026-06-standalone-runtime, not PR #65 branch.
- The existing role's attached/inline IAM review shows explicit historical Task 7 S3 object ARNs, but not the proposed PR #65 key.
- AWS read-only SimulatePrincipalPolicy on that existing role: PutObject new key implicitDeny; GetObject new key implicitDeny; DeleteObject new key implicitDeny; PutObject old Task 7 key allowed.
- Hence a least-privilege principal that can actually publish the new PR #65 key has NOT been established. IAM custom policy simulation cannot substitute for verifying an actual assigned role, its trust, its boundaries, SCPs and bucket policy.

## Proposed policy and simulations — REVIEW ONLY

- Proposed JSON is infra/pr65-publisher-policy.review-only.json; it does not create an IAM role.
- Allow s3:PutObject only for the one new object ARN and only with s3:if-none-match present and s3:x-amz-server-side-encryption AES256.
- Allow s3:GetObject and s3:GetObjectVersion only for that same object for post-write verification.
- Do not allow DeleteObject, ListBucket, access to old keys, or other buckets. Pre-upload destination inventory is performed under a separately approved read-only identity.
- AWS read-only SimulateCustomPolicy produced 10/10 expected decisions: conditional exact PUT allowed; missing conditional or AES256 headers denied; alternate/old/other-bucket PUT denied; exact GET and GET version allowed; DELETE and bucket list denied.
- This is a hypothetical policy, not an effective-permissions check for a nonexistent role. Creation/attachment of a publisher role needs a separate approval.

## Upload command compatibility (documentation; NOT executed)

- AWS CLI v2 reference documents s3api put-object --body local-file-path --server-side-encryption AES256 --if-none-match '*' and returns VersionId for versioned buckets.
- GitHub CLI manual confirms gh run download RUN --repo REPO --name NAME --dir DIRECTORY.
- The execution host's installed aws and gh versions were NOT checked in this environment (binaries unavailable). Run aws --version, gh --version and inspect aws s3api put-object help for --if-none-match before any authorized write.
- S3 If-None-Match checks only the CURRENT version in a versioned bucket; it does not prove there were never old versions or delete markers. The independent all-versions absence check is mandatory.
- Stop on 412 PreconditionFailed or 409 ConditionalRequestConflict and investigate, rather than automatically retrying.

## Safe exact-key absence preflight for a future authorized operator — READ-ONLY

```bash
set -euo pipefail
BUCKET=studio-workstation-releases-401233741942-us-east-1
KEY=releases/pr65-pinned-download-084e704e/res-2026.06/installation-scripts.tar.gz
aws --version
gh --version
aws sts get-caller-identity --query Arn --output text
aws s3api list-object-versions --region us-east-1 --bucket "$BUCKET" --prefix "$KEY" > /tmp/pr65-key-versions.json
python3 - <<'PY'
import json
r=json.load(open('/tmp/pr65-key-versions.json'))
k='releases/pr65-pinned-download-084e704e/res-2026.06/installation-scripts.tar.gz'
assert not r.get('IsTruncated', False), 'Incomplete listing'
assert not any(x.get('Key') == k for x in r.get('Versions', [])), 'Existing versions'
assert not any(x.get('Key') == k for x in r.get('DeleteMarkers', [])), 'Existing delete markers'
PY
set +e
HEAD_OUTPUT=$(aws s3api head-object --region us-east-1 --bucket "$BUCKET" --key "$KEY" 2>&1)
HEAD_STATUS=$?
set -e
if [ "$HEAD_STATUS" -eq 0 ]; then echo 'ABORT: object already exists'; exit 1; fi
case "$HEAD_OUTPUT" in
  *'404'*|*'Not Found'*) echo 'Confirmed absent at this moment' ;;
  *) echo "ABORT: unrecognized failure: $HEAD_OUTPUT"; exit 1 ;;
esac
echo 'STOP: no upload authorized by this preflight'
```

## Approval gates and rollback

1. Approve a non-root, exact-key scoped publisher identity or policy change. Verify its effective trust, identity/bucket/SCP permissions and condition headers before using it.
2. Repeat artifact SHA, expiration and exact-key absence checks immediately before upload.
3. Obtain separately explicit approval to upload exactly one object to the proposed unique key. Capture and independently verify the returned S3 VersionId and SHA-256 by version-specific GetObject.
4. Do not update the GPU release pins or remove the CLI's fail-closed launch gate until post-upload read-only validation succeeds. The new prefix lacks the virtual-desktop archive, so the renderer needs split bootstrap/app URI handling or a separately approved copy.
5. If publication fails, preserve the previous immutable Task 7 release and the CLI no-launch gate. Do not delete, overwrite, or create a delete marker on any S3 key without new approval.

**Final state: S3 destination unused; artifact independently verified; command syntax documented; current IAM principal ROOT and previous publisher role DENIED for new key. Therefore UPLOAD NO-GO.**
