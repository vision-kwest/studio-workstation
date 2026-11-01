# Gate 4 — RES Cognito PAM/NSS dependency closure

> **Historical Cognito/PAM/NSS review:** This page explored a future standalone identity adaptation. The tested final path uses local Linux `workstation` credentials with DCV system auth and omits the RES external identity control plane. Source code and acceptance are described in [runtime provenance](../runtime/README.md); current password enrollment is in [Studio CLI](studio-cli.md). Do not interpret the original `not yet verified` statements as current runtime status.

**Analysis only — no provisioning changes or AWS deployment authorized.** Source: `aws/res` pinned `2026.06`, commit `1f526a241fb293a29fb018f08ccf81c100c05a42`. Companion: [Gate 4 handoff](GATE_4_BOOTSTRAP_HANDOFF_AUDIT.md), [proposed diffs](GATE_4_PROPOSED_SOURCE_DIFFS.md).

## Exact identity integration

1. `source/idea/idea-bootstrap/resources/package_config.yml` lines 84–88 declares two host modules: `libnss_cognito` (NSS) and `pam_cognito` (PAM).
2. `source/idea/idea-bootstrap/resources/scripts/common/linux/host_modules.sh` invokes `install_modules "pam"` and `install_modules "nss"`; copies `.so` files into Ubuntu system PAM/NSS directories, fetching from DynamoDB, staging S3 or default RES S3.
3. `source/idea/idea-bootstrap/resources/scripts/virtual-desktop-host/linux/install.sh` invokes `host_modules.sh` during first-stage provisioning.
4. `source/idea/idea-bootstrap/src/ideabootstrap/common/cognito_modules.py`:
   - `configure()` checks `identity-provider.cognito.enable_native_user_login` from RES cluster settings;
   - `_setup_pam_config_file_ubuntu()` inserts `pam_cognito.so` into `/etc/pam.d/common-auth` and `common-account`;
   - `_setup_nss()` appends `cognito` to `passwd:` and `group:` in `/etc/nsswitch.conf`;
   - `_setup_cognito_config_file()` writes `/etc/cognito_auth.conf`, including Cognito pool/client IDs and optional `bootstrap_profile`;
   - `_start_nscd()` enables and starts `nscd`;
   - `configure()` can trigger another reboot after Cognito setup.
5. `source/idea/idea-bootstrap/src/ideabootstrap/directory_service/active_directory.py` uses RES cluster settings for AD join; `source/idea/idea-bootstrap/src/ideabootstrap/sudo/set_sudoers.py` queries RES directory/sudo settings. Their removal is a separate narrow dependency decision, not permission to skip all host initialization.

## Standalone implications

**Confirmed necessary exclusions for Option A local Linux authentication:** avoid installing the Cognito PAM/NSS host modules and avoid invoking `cognito_modules.configure()` in standalone mode. Otherwise local login would be coupled to RES identity settings or altered PAM/NSS configuration.

Do **not** delete or rewrite `cognito_modules.py`, `host_modules.sh`, or the package's global `package_config.yml`. Prefer minimal **caller-side conditional skips**, retaining the original RES branch unchanged.

### Proposed exact call-site hunks (review only; not applied)

```diff
--- a/source/idea/idea-bootstrap/resources/scripts/virtual-desktop-host/linux/install.sh
+++ b/source/idea/idea-bootstrap/resources/scripts/virtual-desktop-host/linux/install.sh
@@
-    /bin/bash "${SCRIPT_DIR}/../../common/linux/host_modules.sh" -o $BASE_OS -s "${SCRIPT_DIR}/.." -e "${ENVIRONMENT_NAME}" -b "${STAGING_BUCKET}"
+    if [[ "${STUDIO_STANDALONE}" != "true" ]]; then
+      /bin/bash "${SCRIPT_DIR}/../../common/linux/host_modules.sh" -o $BASE_OS -s "${SCRIPT_DIR}/.." -e "${ENVIRONMENT_NAME}" -b "${STAGING_BUCKET}"
+    fi
```

```diff
--- a/source/idea/idea-virtual-desktop/src/ideavirtualdesktop/app/linux/bootstrap.py
+++ b/source/idea/idea-virtual-desktop/src/ideavirtualdesktop/app/linux/bootstrap.py
@@
-        cognito_modules.configure()
+        if os.environ.get("STUDIO_STANDALONE") != "true":
+            cognito_modules.configure()
```

**Important:** `STUDIO_STANDALONE` is only a proposed input contract; how it persists through cloud-init, `/etc/environment`, Supervisor and Python execution is not yet verified. No implementation approval is implied.

## Security validation

- On a newly provisioned standalone Ubuntu host, `/etc/pam.d/common-auth`, `common-account` and `/etc/nsswitch.conf` must have **no RES Cognito additions**; `pam_cognito.so` and `libnss_cognito.so` must not be installed by our bootstrap.
- Local user lookup via `getent passwd` and PAM-backed DCV authentication must work without Cognito/SSSD/AD; non-owner DCV access must be denied.
- Do not alter system PAM files by string removal on an existing host; this design assumes a clean base AMI.
- Preserve the existing RES Ubuntu/DCV package installation and reboot sequence. Skipping Cognito-specific module installation is a justified exception, not a new bootstrap architecture.

## Remaining Gate 4 blockers

1. Finish the `resserver`/Supervisor lifecycle boundary and direct application archive delivery, with exact source-level, mechanically checked diffs.
2. Audit `shared_storage`, `sssd.sh`, `active_directory`, `set_sudoers`, proxy and tag calls individually. Do not blanket-disable them.
3. Validate exact DCV package version and local PAM/permissions syntax; review Option A SSM session logging/privacy.
4. Run test-only checks for unchanged RES branch and isolated standalone branch; request explicit implementation approval separately.

**Status:** PAM/NSS dependency traced, proposed call-site exclusion documented. No original RES source modified.
