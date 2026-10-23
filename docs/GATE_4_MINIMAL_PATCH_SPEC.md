# Gate 4 — Minimal RES 2026.06 standalone source patch specification

**Status: PROPOSED FOR REVIEW. No implementation authorized.** This is a source-level change specification, not a patch or permission to deploy. Upstream [aws/res `2026.06`](https://github.com/aws/res/tree/2026.06), commit `1f526a241fb293a29fb018f08ccf81c100c05a42`. Design: [approved choices](STANDALONE_DESIGN_DECISIONS.md), [Gate 3](GATE_3_SECURITY_REVIEW.md), [deviations](DEVIATIONS.md).

## Preserve unchanged

- `idea-sdk` workstation `BootstrapUserDataBuilder`, `bootstrap_userdata_linux_base_non_substitution.sh.jinja2`, `cloud_final_modules: [[scripts-user, always]]` and existing semaphores/reboot stages.
- Original `virtual-desktop-host/linux/install.sh` and `install_post_reboot.sh` package installation sequence, DCV installer, NVIDIA driver logic, Ubuntu GNOME/Xorg initialization and DCV service configuration unless a **specific** incompatibility is demonstrated.
- Original DCV session readiness polling and automatic-console session creation; keep the `console` session ID.
- The original published RES archives and pinned hashes; no invented replacement package system.

## Proposed minimal source-level changes (NOT applied)

| ID | Upstream file/function and location | Original dependency | Narrow proposed adaptation | Proof required before approval |
|---|---|---|---|---|
| D-001 | `idea-bootstrap/src/ideabootstrap/dcv/linux/dcv_host.py`, `_configure_dcv_conf()`, ~44–94 | `dcv_utils.get_cluster_internal_endpoint()`, DynamoDB idle timeout settings, `auth-token-verifier` and `no-tls-strict` | Preserve all unrelated `dcv.conf` entries; inject locally reviewed idle timeouts; configure `[security] authentication="system"` and remove RES external verifier and its TLS exception. Avoid new DCV config generator. | Version-matched DCV package metadata, syntax validation, PAM config, TLS review, mocked config diff |
| D-001 | `idea-bootstrap/src/ideabootstrap/dcv/dcv.py`, `configure_automatic_console_session()`, ~48–99 | `cluster.administrator_username` and RES `session_permissions.generate_permissions_content()` | Supply owner-only permission content through the narrowest reviewed input seam; preserve file write, `apply_automatic_console_session_config`, DCV restart and readiness; separate RES state reporting from local success | Validate `%owner% allow builtin`, file ownership/mode, no guest/admin grants, correct local account |
| D-001 | `library/src/res/resources/dcv/session_permissions.py`, `generate_permissions_content()`, ~41–100 | Database-backed admin/guest profiles | Prefer bypassing this database-backed function only at the standalone call site; **do not edit the shared library** unless proven necessary | Demonstrate no RES profile lookup during standalone console creation |
| D-002 | `dcv_host.py` `_configure_dcv_conf()`; original gateway routing | RES ALB and token-verifier endpoint | Direct TLS DCV access on restricted TCP 8443, UDP 8443 only if QUIC enabled; no inbound SSH. This is primarily minimal infrastructure/config input, not a new proxy | Security group/IPv6 review, certificate trust, non-owner denial |
| D-003 | `idea-virtual-desktop/src/ideavirtualdesktop/app/linux/bootstrap.py`, `run()`, ~39–111 | Shared storage, Active Directory/SSSD, Cognito, tags, RES customization paths | Preserve host and GNOME/DCV operations; skip **only** independently proven RES-only storage/identity/control-plane operations using explicit standalone inputs | Full call-order comparison against T3 and precise call-site exclusions |
| D-004 | `idea-virtual-desktop/src/ideavirtualdesktop/app/linux/post_reboot.py`, `run()`, ~23–83; `idea-bootstrap/src/ideabootstrap/bootstrap_common.py`, `update_session_state/update_session_host_info`, ~149–182 | RES session-state updates, idle-check cron, DynamoDB | Preserve console creation, polling and reboot flags; omit RES-only reporting/idle scheduler after proving no local dependency; define explicit local readiness check independent of RES reporting | T3/T4 regression and failure semantics, first/subsequent boot tests |
| D-004 | `idea-bootstrap/src/ideabootstrap/dcv/linux/gl.py`, `_is_gpu_instance_type()`, ~15–22 | GPU-family list from RES cluster settings | Supply the exact equivalent GPU family list locally; preserve `_configure_gl()` behavior | CPU no-GPU and g4dn GPU branch tests |
| D-005 | `virtual-desktop-host/linux/install.sh`, `install_post_reboot.sh`, `common/linux/install_app.sh`, user-data builder caller | Broker token, DynamoDB app package URI, RES S3 credentials | Keep runtime script/reboot sequence; supply pinned package URIs and instance-profile access via narrowly scoped, reviewed inputs. Do not invent new bootstrap service or copy a controller-host template. | Line-by-line dependency map for broker and application handoff; IAM `GetObject` test deferred to controlled deployment |
| D-006 | Original NVIDIA/DCV/GNOME/Xorg installers and `dcv.configure()` order | None requiring alteration established | **No deviation intended** | T3 order and later GPU/CPU runtime acceptance |
| D-007 | SDK workstation user-data template, semaphores, reboot logic | None requiring alteration established | **No deviation intended** | Boot/reboot/reentry trace and later runtime test |

**Line numbers above are approximate source anchors for reviewers; re-open pinned source and attach exact line-level unified diffs before approving implementation.** They are not completed patches. Do not conflate `install_app.sh` package retrieval with standalone application initialization: the RES application server and framework setup must be isolated only after dependency review.

## Option A — approved credential enrollment contract

- Provision a dedicated non-root Ubuntu user with home directory and locked password; do not store secrets in user data, templates, AMI, GitHub, SSM Run Command or logs.
- Activate **once per newly created account** using interactive SSM Session Manager and `sudo passwd <username>`, after reviewing SSM session recording/privacy settings and authorized operator access.
- Keep SSH closed. Require PAM/DCV login, owner access, non-owner denial and secure reset/recovery tests. Option B automatic enrollment is explicitly deferred.
- **This operational design is approved, not its implementation.**

## Blocking reviews before code authorization

1. Exact installed RES Demo DCV version, or record that it is irrecoverable and obtain explicit approval of a checksum-verified versioned replacement. Current AWS documentation mentions DCV `2026.0.24952-1` for Ubuntu 24.04, but the upstream RES `2026.06` installer references an **unversioned** CloudFront URL. The two facts do not establish historical equality.
2. Verify DCV PAM, permissions file grammar/owner, TLS and non-owner denial using version-matched documentation and later runtime checks.
3. Confirm Session Manager logging/privacy for password enrollment.
4. Produce exact file-level unified diffs (not yet generated) with test impact, preserve D-006/D-007, and obtain explicit review.
5. Standalone EC2 role `s3:GetObject` test remains deferred until separately approved deployment, per Gate 1.

## Proposed acceptance tests

- Static config comparison: only RES verifier and related inputs change; no accidental removal of display/GL/QUIC options.
- Mocked console: owner/permissions path unchanged; RES DynamoDB lookups absent; readiness and errors observable.
- Negative tests: absent owner, missing session ID, failed DCV restart, malformed permissions, non-owner denied, no false ready lock.
- CPU and NVIDIA GPU integration: after separate authorization, validate real GNOME/Xorg/DCV browser login, PAM, TLS, `nvidia-smi`, accelerated renderer, stop/start and both reboot transitions.
- No RES controller, broker, gateway, AD, DynamoDB table or persistent control instance provisioned.

**Review gate:** this document can be merged as analysis. It does not authorize code changes, IAM modifications or AWS deployment.
