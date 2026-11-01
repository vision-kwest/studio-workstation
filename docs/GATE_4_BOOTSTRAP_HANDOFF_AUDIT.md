# Gate 4 — Bootstrap and application handoff dependency closure

> **Historical Gate 4 dependency audit:** Proposed source edits and `Gate 4 remains open` language below refer to an older review, before the standalone RES adapter and completed GPU test. The accepted runtime preserves original installer/reboot/Supervisor/DCV lifecycle; see [runtime provenance](../runtime/README.md). Use [Studio CLI](studio-cli.md) for current operations, not the historical gate's deployment decisions.

**Analysis only; implementation NOT authorized.** Pinned AWS RES commit `1f526a241fb293a29fb018f08ccf81c100c05a42`. Companion: [proposed source diffs](GATE_4_PROPOSED_SOURCE_DIFFS.md). This document records source-backed remaining boundaries; it is **not** a complete mechanically applicable patch.

## Original runtime call chain (verified)

1. `idea-bootstrap/resources/scripts/virtual-desktop-host/linux/install.sh`: `-p false` uses first-stage semaphore, installs common packages, optional NFS/SSSD/S3 Mountpoint, host PAM/NSS modules and DCV prerequisites; then first reboot; next invocation chains to `install_post_reboot.sh`.
2. `virtual-desktop-host/linux/install_post_reboot.sh`: installs GPU drivers/DCV/Xorg and SSM, sets second-stage semaphore, reboots, then chains to `common/linux/install_app.sh`.
3. `common/linux/install_app.sh`: if component `virtual-desktop-app`, copies RES broker helpers, calls `request_and_export_aws_credentials`, sets `AWS_DEFAULT_PROFILE=bootstrap_profile`, reads `vdi-app.app_package_uri` from DynamoDB, downloads via S3, installs nested Python libraries and requirements, configures Supervisor to launch `resserver`, writes `app_installed.lock`.
4. `idea-virtual-desktop/src/ideavirtualdesktop/app/linux/bootstrap.py` runs local and RES-specific initialization; `post_reboot.py` creates/validates automatic console and reports state to RES.

## New high-priority finding: PAM/NSS host modules

`install.sh` invokes `common/linux/host_modules.sh` **before** desktop prerequisites. That script reads `package_config.host_modules.pam` and `.nss`, attempts DynamoDB first, then downloads `.so` modules from staging/default RES S3 paths and installs them into system PAM/NSS library directories with root ownership.

**Security consequence:** Simply switching DCV to system/PAM authentication is insufficient if RES's custom PAM/NSS modules remain active or alter local identity resolution. Before modifying anything, inspect:
- exact `package_config.host_modules` entries and the modules' activation in PAM/NSS configuration;
- `sssd.sh`, `active_directory.configure()`, `cognito_modules.configure()`, and `set_sudoers.configure()` transitive effects;
- whether skipping module installation is safe for local Linux login and whether downstream bootstrap expects them.

No blanket removal is approved. This is a Gate 3/Gate 4 security blocker.

## D-005 application delivery: smallest candidate source edit

Source: `idea-bootstrap/resources/scripts/common/linux/install_app.sh`.

Original behavior:
- unconditionally uses broker for `virtual-desktop-app`;
- looks up `vdi-app.app_package_uri` from `$ENVIRONMENT_NAME.cluster-settings`;
- downloads the original application archive and installs its nested libraries;
- starts `resserver` through Supervisor.

**Candidate (review only):**
- keep archive download/extraction and `idea_pip` installation unchanged;
- in explicitly selected standalone mode, skip broker credential configuration and use an explicitly pinned `STUDIO_APP_PACKAGE_URI` instead of DynamoDB lookup;
- leave original RES branch unchanged;
- do not replace Supervisor or `resserver` with a custom service without proving the original `ResApp` initialization can be invoked independently and without losing reboot reentry. This remains a separate unresolved application-lifecycle decision.

The candidate diff below is **limited to the URI and credential acquisition seam**, not a complete standalone application solution:

```diff
--- a/source/idea/idea-bootstrap/resources/scripts/common/linux/install_app.sh
+++ b/source/idea/idea-bootstrap/resources/scripts/common/linux/install_app.sh
@@
-if [[ "${COMPONENT}" == "virtual-desktop-app" ]]; then
+if [[ "${COMPONENT}" == "virtual-desktop-app" && "${STUDIO_STANDALONE}" != "true" ]]; then
@@
-APP_PACKAGE_DOWNLOAD_URI=$($AWS dynamodb get-item \
+if [[ "${STUDIO_STANDALONE}" == "true" && "${COMPONENT}" == "virtual-desktop-app" ]]; then
+  APP_PACKAGE_DOWNLOAD_URI="${STUDIO_APP_PACKAGE_URI:?Standalone app URI required}"
+else
+APP_PACKAGE_DOWNLOAD_URI=$($AWS dynamodb get-item \
                         --region "$INSTANCE_REGION" \
                         --table-name "$ENVIRONMENT_NAME.cluster-settings" \
                         --key '{"key": {"S": "'$APP_PACKAGE_URI_KEY'"}}' \
                         --output text \
                         | awk '/VALUE/ {print $2}')
+fi
```

**Important:** This is not yet mechanically checked and is not an approved patch. Existing `AWS_DEFAULT_PROFILE=bootstrap_profile` in the Supervisor environment block must also be reviewed; it cannot silently survive when the broker profile is absent. Shell error handling and blank URI must fail closed. Preserve original `app_installed.lock` behavior.

## D-003 bootstrap call-site classification required

| Original call in `install.sh` | Proposed treatment | Reason for unresolved status |
|---|---|---|
| `install_common.sh`, `install_prerequisites` | Preserve | Essential OS/DCV prerequisites |
| `disable_amazon_ssm_agent.sh` / late `aws_ssm.sh` | Preserve ordering pending review | RES intentionally avoids disruptive SSM updates |
| `nfs_utils.sh`, `sssd.sh`, `s3_mountpoint.sh` | Candidate skip | Check PAM/NSS and storage transitive dependencies |
| `host_modules.sh` | **Security-critical: inspect before change** | Installs RES PAM/NSS `.so` modules |
| `disable_nouveau_drivers.sh`, GPU/DCV installer | Preserve | D-006 |
| `vdi-helper/requirements.txt` | Candidate skip | Determine whether broker helper imports required later |
| `set_reboot_required`, semaphores, reboot | Preserve | D-007 |

## Explicit blockers before exact full patch approval

1. Finish source-level classification of `host_modules.sh`, SSSD and PAM/NSS configuration; prove local PAM/DCV can function without RES identity modules.
2. Trace `resserver`/Supervisor entry point and the minimum safe original application initialization invocation without `ResApp` services; don't invent a replacement service.
3. Validate the standalone package URI contract and instance-profile AWS CLI behavior (role test deferred until authorized deployment).
4. Generate **complete, mechanically checked** unified diffs against the pinned source for `install.sh`, `install_app.sh`, `bootstrap.py`, `post_reboot.py`, and the earlier DCV files. The snippets in Gate 4 docs are design proposals, not apply-ready diffs.
5. Review first-login Option A, permissions, DCV version, TLS and session logging; runtime verification remains separately authorized.

**Gate 4 remains open.** No EC2, IAM, CloudFormation or original provisioning code changed.
