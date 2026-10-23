# Gate 3 — Security decisions review (RES 2026.06)

**Status: Option A first-login enrollment APPROVED as design; package-version and runtime verification still open. No provisioning implementation or AWS deployment authorized.**

Upstream pin: `aws/res` tag `2026.06`, commit `1f526a241fb293a29fb018f08ccf81c100c05a42`. Related: [Gate 3 design](GATE_3_DCV_AUTH_DESIGN.md).

## Review 1: DCV version, authentication and TLS

**Source-backed facts**
- `source/idea/idea-bootstrap/resources/package_config.yml` lines 245–251 identifies Ubuntu 24.04 x86_64 archive `https://d1uj6qtbmh3dt5.cloudfront.net/nice-dcv-ubuntu2404-x86_64.tgz` and its `.sha256sum` endpoint. The URL is not a numeric version pin.
- `source/idea/idea-bootstrap/resources/scripts/dcv/linux/debian/dcv_server.sh` downloads, verifies checksum and installs `nice-dcv-server_*.deb`, `nice-dcv-web-viewer_*.deb`, and `nice-xdcv_*.deb`.
- `ideabootstrap/dcv/linux/dcv_host.py` writes RES `auth-token-verifier` pointing to internal ALB and `no-tls-strict=true`.
- AWS DCV documentation: [authentication](https://docs.aws.amazon.com/dcv/latest/adminguide/security-authentication.html) states `[security] authentication="system"` delegates to PAM, default `/etc/pam.d/dcv`. [External authentication](https://docs.aws.amazon.com/dcv/latest/adminguide/external-authentication.html) describes `auth-token-verifier` for external authentication.
- `no-tls-strict` controls certificate validation toward the external token verifier, not whether DCV browser transport itself uses TLS. Remove this RES-only exception along with the verifier, subject to version confirmation.

**Decision:** use DCV system authentication and installed default PAM service unless version-specific inspection requires a reviewed override. Keep TLS for DCV client connections. **Exact numeric RES Demo-installed DCV version remains UNVERIFIED.** AWS's current Ubuntu 24.04 installation guide lists DCV server **2026.0.24952-1** at a versioned URL, but this does not establish what the mutable RES URL delivered when the Demo ran. Source: https://docs.aws.amazon.com/dcv/latest/adminguide/setting-up-installing-linux-x11-installation.html . Before a patch is approved, inspect package metadata from the exact checksum-verified `nice-dcv-ubuntu2404-x86_64.tgz` artifact and record `nice-dcv-server` package version and archive SHA-256. Mutable CloudFront URL alone is not an immutable pin. Do not claim current docs prove every historical version.

## Review 2: owner-only console permissions

- RES `library/src/res/resources/dcv/session_permissions.py` creates administrator and guest permissions from cluster settings and session records and adds `%owner%` entries based on the administrator profile.
- AWS [authorization](https://docs.aws.amazon.com/dcv/latest/adminguide/security-authorization.html) says the default permissions file gives only the owner full access. AWS [permission syntax](https://docs.amazonaws.cn/en_us/dcv/latest/adminguide/security-authorization-file-create-permission.html) recognizes `%owner%`.
- Candidate explicit file, keeping RES's existing `/etc/dcv/console/idea.perm` mechanism:

  ```ini
  [permissions]
  %owner% allow builtin
  ```

**Decision:** adopt the above as a **reviewed candidate** for a dedicated, non-root local account. Do not add `%any%`, RES `ideaadmin`, guest users, or admin-profile database lookups. Keep original DCV owner/storage-root/permissions-file application and restart path. The policy is not runtime validated. Test that owner can connect, non-owner cannot, and owner has expected features; compare against installed DCV `/etc/dcv/default.perm` and actual `builtin` semantics before final approval. Verify permissions-file ownership and restrictive mode.

## Review 3: secure first-login enrollment

**APPROVED DESIGN — Option A: two-phase local account activation via Systems Manager interactive session.**

1. During a separately authorized provisioning phase, create a dedicated non-root local Linux account with home directory and **locked password**, without putting any secret into CloudFormation, user data, the AMI, logs or repository. No inbound SSH.
2. Before first DCV login, an authorized administrator uses **interactive SSM Session Manager** to run `sudo passwd <local-user>` and types a unique password into the non-echoing prompt. Never use `passwd --stdin`, `chpasswd`, a plaintext shell argument, SSM Run Command parameters, or a fixed default password.
3. Explicitly review Session Manager session logging/recording and terminal policies to ensure password input is not exposed in audit logs or recordings; do not assert confidentiality without checking account settings. If an approved private input path cannot be guaranteed, use a separately reviewed secure enrollment mechanism instead.
4. Confirm `passwd -S` shows the account activated and PAM/DCV login works. Require account lockout/rate-limiting policy and password reset/recovery procedure. No account activation until a separately authorized runtime test.

**Rationale:** no long-lived directory/broker, no embedded credentials, no SSH. **Tradeoff:** requires a one-time interactive maintenance action; does not deliver a completely unattended first login. The project owner approved Option A. Operational logging/privacy review remains required before use.

## Review exit status

| Question | Result |
|---|---|
| Linux DCV system auth and PAM semantics | Documented by AWS; accepted design |
| Exact RES 2026.06 DCV package build | **OPEN** — verify immutable bytes/version |
| Explicit owner-only permissions | Reviewed candidate; **runtime owner/non-owner test open** |
| Secure first-login enrollment | **Option A approved**; Session Manager logging/privacy review remains open |
| TLS and network restrictions | Required; certificate/trust and port tests open |

**Gate 3 is not fully closed.** Gate 4 may draft a minimal source-level patch specification but must not authorize implementation until these remaining security and version checks are accepted.

## Read-only exact package version verification (still required)

RES's unversioned URL is `https://d1uj6qtbmh3dt5.cloudfront.net/nice-dcv-ubuntu2404-x86_64.tgz`. The following commands may be run in AWS CloudShell after review; they download a DCV installer archive for **inspection only**, do not install packages, and do not modify EC2/IAM. CloudShell storage capacity should be checked first. The resulting version reflects the **download date**, not necessarily the historical RES Demo build.

```bash
mkdir -p ~/res-dcv-version-audit && cd ~/res-dcv-version-audit
curl -fL --retry 2 -o nice-dcv-ubuntu2404-x86_64.tgz \\
  https://d1uj6qtbmh3dt5.cloudfront.net/nice-dcv-ubuntu2404-x86_64.tgz
sha256sum nice-dcv-ubuntu2404-x86_64.tgz
tar -tzf nice-dcv-ubuntu2404-x86_64.tgz | grep 'nice-dcv-server_.*ubuntu2404.deb$'
```

To inspect the package metadata without installing it, locate the exact archive member returned above and stream it to a temporary `.deb` file, then run `dpkg-deb -f FILE.deb Package Version Architecture`. Verify the original SHA-256 checksum URL separately. If the successful RES Demo instance still exists, `dpkg-query -W -f='${Version}\\n' nice-dcv-server` on that instance is the strongest direct historical evidence.

**Do not mark exact historical version verified from the current AWS documentation or the mutable URL alone.**
