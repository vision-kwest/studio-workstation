# Gate 1 — RES 2026.06 original artifact verification

> **Historical Gate 1 evidence:** This source-integrity checkpoint preceded the published PR #65 two-key immutable GPU runtime release. Earlier `not applied`, `proposed`, or `deferred` statements apply **only to the original gate checkpoint**. For current S3 keys/versions and validated live downloads see [runtime provenance](../runtime/README.md) and [Studio CLI](studio-cli.md#gpu-workstation-tested-acceptance-scope).

**Status: CONDITIONALLY COMPLETE (2026-10-22).** Project owner explicitly approved deferring the standalone EC2 instance-role access test until a separately authorized controlled deployment. **No implementation or AWS deployment authorized.**

## Source pin

- Upstream: [aws/res tag `2026.06`](https://github.com/aws/res/tree/2026.06)
- Annotated tag resolves to source commit `1f526a241fb293a29fb018f08ccf81c100c05a42`.
- Packaging sources: `tasks/tools/res_installation_scripts_package_tool.py`, `tasks/package.py`, `tasks/tools/package_tool.py`, `source/idea/pipeline/scripts/publish/commands.sh`.

## Original AWS S3 artifacts

Bucket: `research-engineering-studio-us-east-1` (region `us-east-1`), prefix `releases/2026.06/`.

| Artifact | S3-reported bytes | SHA-256 of downloaded file (observed via CloudShell) | Version ID from HEAD (metadata only) |
|---|---:|---|---|
| `res-installation-scripts.tar.gz` | 41873 | `2e3b0e4b198582ea03b1926aaaf76da21688f2e6809ad4d153b95a9417c72658` | `f5bX2EOZ37AoCuQWw9dg91C5jiUinhp5` |
| `idea-virtual-desktop-2026.06.tar.gz` | 424424 | `f5d2297537bda746fe74411c18140bcd0f2d4322852fa9d32ce7606a93aed7a8` | `8Me81KMeD1Bt8Gy3xCmYMhzxRVW_4zaE` |

Both HEAD calls returned `ServerSideEncryption=AES256` (SSE-S3). Both `aws s3 cp` downloads **without** a version ID succeeded from AWS CloudShell. `get-object --version-id` returned AccessDenied for both; likely requires `s3:GetObjectVersion`, but no IAM policy diagnosis was completed. **The SHA-256 values fingerprint the current objects downloaded, not proven immutable version-specific bytes or independently signed upstream checksums.** Recheck before deployment.

## Archive structure (non-executing listing)

`res-installation-scripts.tar.gz` contains:
- `package_config.yml`, `requirements.txt`
- `./scripts/virtual-desktop-host/linux/install.sh`
- `./scripts/virtual-desktop-host/linux/install_post_reboot.sh`
- Common Linux and DCV installer resource tree (observed common paths; exact complete tree not enumerated in this audit).

`idea-virtual-desktop-2026.06.tar.gz` contains:
- `datamodel-lib.tar.gz`
- `idea-bootstrap-lib.tar.gz`
- `idea-data-model-lib.tar.gz`
- `idea-sdk-lib.tar.gz`
- `idea-virtual-desktop-lib.tar.gz`
- `library-lib.tar.gz`
- `requirements.txt`

The nested `idea-bootstrap-lib.tar.gz` contains:
- `ideabootstrap/dcv/dcv.py`
- `ideabootstrap/dcv/linux/dcv_host.py`
- `ideabootstrap/dcv/linux/x_server.py`
- `ideabootstrap/dcv/linux/start_dcv_service.py`

This establishes that original RES scripts and Python DCV modules are packaged; **it does not demonstrate standalone runtime compatibility**.

## Proposed least-privilege access (NOT applied)

For ordinary unversioned downloads, a standalone EC2 instance profile could request:

```json
{
  "Version": "2012-10-17",
  "Statement": [{
    "Sid": "ReadRES202606Artifacts",
    "Effect": "Allow",
    "Action": ["s3:GetObject"],
    "Resource": [
      "arn:aws:s3:::research-engineering-studio-us-east-1/releases/2026.06/res-installation-scripts.tar.gz",
      "arn:aws:s3:::research-engineering-studio-us-east-1/releases/2026.06/idea-virtual-desktop-2026.06.tar.gz"
    ]
  }]
}
```

This policy is a proposal only. It cannot override the AWS-owned bucket's resource policy. The CloudShell identity is not the intended standalone instance role. Version-specific reads, if required, need separate permission evaluation.

## Deferred deployment gate

Before any standalone workstation deployment:
1. Review/approve the exact EC2 role and bucket/object access assumptions.
2. Verify `s3:GetObject` works for **both objects from that role**; diagnose access failures without widening permissions reflexively.
3. Reconfirm object hashes or copy the approved bytes to an owner-controlled versioned artifact store with reviewed access and integrity controls, if necessary.
4. Verify original source and package licensing obligations.

## Next gate

**Gate 2 — Python initialization compatibility:** analyze whether upstream `ideavirtualdesktop.app.linux.bootstrap.run()` and `post_reboot.run()` can be invoked without `ResApp`, preserving DCV/GNOME/Xorg configuration and semaphore/reboot behavior. Analysis and test design only; no provisioning implementation or AWS deployment.
