# Gate 3 — Standalone Amazon DCV authentication and console authorization

**Status: proposed design baseline, NOT implementation approval.** RES upstream: [aws/res `2026.06`](https://github.com/aws/res/tree/2026.06), commit `1f526a241fb293a29fb018f08ccf81c100c05a42`. Applies to accepted design decision 1 and deviations D-001/D-002. This document records an owner-only baseline and the remaining security review gates; it does not change RES source, IAM, CloudFormation or EC2.

## Baseline and explicit boundaries

- One dedicated non-root **local Ubuntu desktop user**, provisioned securely (not an account or password embedded in user data, image, repository or logs).
- Amazon DCV **system authentication** backed by Linux PAM instead of the RES external token verifier.
- A **console** session owned by that local user; preserve upstream automatic-console configuration, storage-root creation, Xorg setup and DCV readiness polling.
- Candidate explicit owner-only DCV permissions policy:
  ```ini
  [permissions]
  %owner% allow builtin
  ```
  This is a review candidate, **not** a verified final permissions file. Compare against DCV's default owner permissions and the actual RES admin-profile-derived `%owner%` privileges. No RES guest or `ideaadmin` group by default.
- Direct DCV transport, with TLS and ingress restricted to approved client CIDR(s); no inbound SSH. Do not reuse RES's `auth-token-verifier` or assume the ALB is available.
- Do not change original GPU drivers, Xorg/GNOME sequencing, cloud-init or semaphore reboot continuation.

## Exact upstream source boundaries

| Source | Original RES behavior | Proposed minimum change | Evidence still required |
|---|---|---|---|
| [`idea-bootstrap/src/ideabootstrap/dcv/linux/dcv_host.py`](https://github.com/aws/res/blob/2026.06/source/idea/idea-bootstrap/src/ideabootstrap/dcv/linux/dcv_host.py) `_configure_dcv_conf()` | Looks up internal ALB endpoint, DynamoDB idle timeouts; writes `[security] auth-token-verifier=.../externalAuth/<IDEA_SESSION_ID>`; sets other DCV parameters | Supply reviewed local timeout values and system-authentication setting; eliminate external-verifier dependency **only**; preserve unrelated settings subject to security review | Confirm exact installed DCV version supports syntax and how `authentication`, TLS, `no-tls-strict` and defaults interact |
| Same file `_configure_storage_root()` and `apply_automatic_console_session_config()` | Creates `/home/<owner>/storage-root`; sets owner, permissions file, restarts DCV | Preserve unchanged | Account exists, correct UID/GID, file ownership and restrictive modes, restart/readiness |
| [`idea-bootstrap/src/ideabootstrap/dcv/dcv.py`](https://github.com/aws/res/blob/2026.06/source/idea/idea-bootstrap/src/ideabootstrap/dcv/dcv.py) `configure_automatic_console_session()` | Fetches RES admin username; calls database-backed `generate_permissions_content`; writes `/etc/dcv/console/idea.perm`; restarts DCV and updates RES session state | Provide local-owner permission content through minimal input substitution; retain writing/applying permissions and local readiness; separate RES state reporting | Confirm valid owner principal, DCV permissions syntax, error handling, mode/owner and console semantics |
| [`library/src/res/resources/dcv/session_permissions.py`](https://github.com/aws/res/blob/2026.06/source/idea/library/src/res/resources/dcv/session_permissions.py) `generate_permissions_content()` | Resolves RES admin and guest profiles from cluster settings/session records, adds `%owner%` entries using admin profile | Do **not** retain database queries for standalone owner-only policy; compare exact upstream permission set before approving static policy | Confirm `builtin` privilege scope and no unintended guest access |
| [`idea-bootstrap/src/ideabootstrap/dcv/linux/x_server.py`](https://github.com/aws/res/blob/2026.06/source/idea/idea-bootstrap/src/ideabootstrap/dcv/linux/x_server.py) | Starts graphical target and validates `SI:localuser:dcv` X authorization | Preserve | Real Ubuntu Xorg validation and NVIDIA/CPU paths |
| [`idea-bootstrap/src/ideabootstrap/dcv/linux/start_dcv_service.py`](https://github.com/aws/res/blob/2026.06/source/idea/idea-bootstrap/src/ideabootstrap/dcv/linux/start_dcv_service.py) | Writes original DCV systemd unit and performs readiness checks | Preserve pending review | Ensure no startup ordering cycle and actual service readiness |

**Caution:** Upstream `dcv_host._configure_dcv_conf()` catches and logs some errors. Do not equate return from the function with valid `dcv.conf`. A missing `IDEA_SESSION_ID` raises before that handler (observed in Gate 2 T4).

## Security design questions requiring explicit review

### PAM and account lifecycle
- Identify actual DCV package version installed by RES 2026.06 from `package_config.yml` and download metadata; consult version-matched DCV Linux documentation.
- Inspect `/etc/pam.d/dcv` expected by that version and the Ubuntu 24.04 PAM account/password/session policy. Ensure no inadvertent root/guest login.
- Select secure first-login credential enrollment: e.g. interactive account activation through an approved, audited maintenance channel. **No fixed default password, cloud-init plaintext password, or password in SSM command history.** The method is **not yet approved**.
- Define local username independently of RES admin account; no assumption that `clusteradmin` or `workstation` exists on the standalone host.

### Console authorization
- Compare `%owner% allow builtin` to upstream admin-profile-derived permissions and documented DCV defaults; check screen, clipboard, file transfer, USB, printing, audio and session management permissions against least privilege.
- Verify permissions-file grammar, encoding, file ownership and restrictive filesystem mode; test whether `dcv set-permissions` is needed after changes.
- Confirm automatic console owner, session ID `console`, storage-root and PAM user identity are the same principal.
- Check that no guest or implicit group can connect, and that an authenticated non-owner is denied.

### Transport and TLS
- Review upstream `[security] no-tls-strict=true` and do not carry it into the standalone design without understanding version-specific effects.
- Keep TLS; define certificate issuance or pin/fingerprint verification, rotation and browser warning handling. Do not claim a self-signed certificate gives trusted-browser UX.
- Restrict TCP 8443 and (only if QUIC is enabled) UDP 8443 to approved client CIDR(s); no public SSH. Review IPv6 and changes in client IP.

## Non-deploying validation plan

1. **Version-specific config review:** Compare original DCV package metadata and AWS documentation for `authentication="system"`, PAM, TLS and permissions grammar.
2. **Static proposed-diff review:** Write a line-level diff proposal for only `dcv_host.py` and `dcv.py`, or explain if a narrower input substitution works; do not apply it yet.
3. **Mocked configuration tests:** Verify original console owner, storage root, permissions file path, DCV restart and readiness; reject absent local owner, malformed permissions, wrong auth mode and unexpected guest grants. No real `systemctl`, useradd, network or filesystem mutations.
4. **Later authorized runtime tests:** Confirm PAM login as owner, non-owner denial, DCV console running, TLS, reconnect, stop/start, CPU and GPU desktop rendering. These tests require separate deployment authorization.

## Gate 3 exit criteria

**Design review ready** only when the exact version-matched DCV settings, owner-only permissions policy, credential enrollment method and proposed source-level diff are reviewed and accepted.

**Runtime validated** only after actual login, authorization denial, TLS and restart tests on an explicitly authorized workstation. Until then, label Gate 3 **open**. Gate 4 must review exact deviations before implementation.
