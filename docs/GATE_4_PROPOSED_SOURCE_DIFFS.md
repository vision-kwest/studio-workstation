# Gate 4 — Proposed source diffs against RES 2026.06

**REVIEW ONLY. NOT AN IMPLEMENTATION PR.** Upstream commit: `1f526a241fb293a29fb018f08ccf81c100c05a42`. All snippets below are **proposed** and require review and patch application validation against the pinned source. They are not tested or approved changes.

**Scope:** minimal call-site adaptations for local authentication, local owner permissions, GPU-family selection and RES-only reporting. No new installer, service manager, cloud-init template or reboot logic. The standalone input interface shown below (`STUDIO_STANDALONE`, `STUDIO_GPU_FAMILIES`, `STUDIO_DCV_IDLE_TIMEOUT`) is a **candidate contract**, not an approved configuration format.

## D-001/D-002: `source/idea/idea-bootstrap/src/ideabootstrap/dcv/linux/dcv_host.py`

Proposed unified diff hunk, preserving the original `dcv.conf` generator and all display/GL settings:

```diff
--- a/source/idea/idea-bootstrap/src/ideabootstrap/dcv/linux/dcv_host.py
+++ b/source/idea/idea-bootstrap/src/ideabootstrap/dcv/linux/dcv_host.py
@@
-        internal_alb_endpoint = dcv_utils.get_cluster_internal_endpoint()
-        idle_timeout = cluster_settings.get_setting(constants.IDLE_TIMEOUT_KEY)
-        idle_timeout_warning = cluster_settings.get_setting(
-            constants.IDLE_TIMEOUT_WARNING_KEY
-        )
+        standalone = os.environ.get("STUDIO_STANDALONE") == "true"
+        if standalone:
+            idle_timeout = int(os.environ["STUDIO_DCV_IDLE_TIMEOUT"])
+            idle_timeout_warning = int(os.environ["STUDIO_DCV_IDLE_WARNING"])
+        else:
+            internal_alb_endpoint = dcv_utils.get_cluster_internal_endpoint()
+            idle_timeout = cluster_settings.get_setting(constants.IDLE_TIMEOUT_KEY)
+            idle_timeout_warning = cluster_settings.get_setting(
+                constants.IDLE_TIMEOUT_WARNING_KEY
+            )
@@
-        config["security"] = {
-            "supervision-control": '"enforced"',
-            "auth-token-verifier": f'"{internal_alb_endpoint}/externalAuth/{idea_session_id}"',
-            "no-tls-strict": "true",
-            "os-auto-lock": "false",
-        }
+        config["security"] = {
+            "supervision-control": '"enforced"',
+            "os-auto-lock": "false",
+        }
+        if standalone:
+            config["security"]["authentication"] = '"system"'
+        else:
+            config["security"]["auth-token-verifier"] = (
+                f'"{internal_alb_endpoint}/externalAuth/{idea_session_id}"'
+            )
+            config["security"]["no-tls-strict"] = "true"
```

**Review questions:** validate integer timeout units/ranges, fail-closed behavior, exact DCV version and PAM syntax. This preserves RES behavior in non-standalone mode. Do not mistake `no-tls-strict` for the client-facing TLS setting.

## D-001/D-004: `source/idea/idea-bootstrap/src/ideabootstrap/dcv/dcv.py`

Preserve the original console creation/restart/readiness path; replace only the RES-backed permission source and state reporting in standalone mode:

```diff
--- a/source/idea/idea-bootstrap/src/ideabootstrap/dcv/dcv.py
+++ b/source/idea/idea-bootstrap/src/ideabootstrap/dcv/dcv.py
@@
-    update_session_state("CREATING")
+    standalone = os.environ.get("STUDIO_STANDALONE") == "true"
+    if not standalone:
+        update_session_state("CREATING")
@@
-            admin_username = cluster_settings.get_setting("cluster.administrator_username")
+            admin_username = (
+                None if standalone else
+                cluster_settings.get_setting("cluster.administrator_username")
+            )
@@
-        permissions_content = dcv_session_permissions.generate_permissions_content(
-            session_id=session_id,
-            admin_username=admin_username,
-            newline=newline,
-        )
+        if standalone:
+            if not session_owner:
+                raise ValueError("IDEA_SESSION_OWNER is required")
+            permissions_content = "[permissions]" + newline + "%owner% allow builtin" + newline
+        else:
+            permissions_content = dcv_session_permissions.generate_permissions_content(
+                session_id=session_id,
+                admin_username=admin_username,
+                newline=newline,
+            )
@@
-            update_session_state("INITIALIZING")
+            if not standalone:
+                update_session_state("INITIALIZING")
@@
-        update_session_state("ERROR")
+        if not standalone:
+            update_session_state("ERROR")
@@
-        update_session_state("ERROR")
+        if not standalone:
+            update_session_state("ERROR")
```

**Security review:** `%owner% allow builtin` is a candidate policy; verify grammar and privileges against the exact installed DCV version. The permissions file is still written by upstream logic. Its ownership/mode must be explicitly reviewed; the above diff does **not** yet harden permissions-file creation. A passing return means DCV server readiness, not proven browser login.

## D-004: `source/idea/idea-bootstrap/src/ideabootstrap/dcv/linux/gl.py`

Preserve the original GPU GL actions; replace only the cluster-settings list source:

```diff
--- a/source/idea/idea-bootstrap/src/ideabootstrap/dcv/linux/gl.py
+++ b/source/idea/idea-bootstrap/src/ideabootstrap/dcv/linux/gl.py
@@
-    gpu_instance_families = cluster_settings.get_setting(
-        "global-settings.gpu_settings.instance_families"
-    )
+    if os.environ.get("STUDIO_STANDALONE") == "true":
+        gpu_instance_families = [
+            value.strip() for value in os.environ["STUDIO_GPU_FAMILIES"].split(",")
+            if value.strip()
+        ]
+    else:
+        gpu_instance_families = cluster_settings.get_setting(
+            "global-settings.gpu_settings.instance_families"
+        )
```

**Review question:** define exact family names and prohibit an empty list when GPU is expected. Test CPU and g4dn branches against T3/T4. No change to `_configure_gl`.

## D-004: `source/idea/idea-virtual-desktop/src/ideavirtualdesktop/app/linux/post_reboot.py`

Preserve both branches and original DCV readiness polling; bypass RES state writes and RES idle-check cron in standalone mode. The hunk below is a **proposed edit plan** rather than a complete mechanically applicable diff because the cron block is multi-line; exact unified hunk must be generated and checked before implementation authorization.

```diff
--- a/source/idea/idea-virtual-desktop/src/ideavirtualdesktop/app/linux/post_reboot.py
+++ b/source/idea/idea-virtual-desktop/src/ideavirtualdesktop/app/linux/post_reboot.py
@@
 def run():
+    standalone = os.environ.get("STUDIO_STANDALONE") == "true"
     if os.path.isfile(LINUX_VDI_CONFIG_HOST_READY_LOCK):
@@
-        update_session_host_info()
+        if not standalone:
+            update_session_host_info()
@@
-            update_session_state(session_state)
+            if not standalone:
+                update_session_state(session_state)
@@
-    update_session_host_info()
+    if not standalone:
+        update_session_host_info()
@@
-        update_session_state(session_state)
+        if not standalone:
+            update_session_state(session_state)
```

**Not yet specified:** wrap the entire `Scheduling VDI Idle Check task` try/except block in `if not standalone` while preserving its original content. Avoid implementing a replacement idle scheduler in this patch. Existing ready lock is written before successful DCV polling: retain upstream semantics for fidelity, but require an independent readiness acceptance check.

## D-003/D-004: `source/idea/idea-virtual-desktop/src/ideavirtualdesktop/app/linux/bootstrap.py`

The following RES-only calls are **candidates** for a standalone conditional, not yet a complete safe diff:

- `idea_proxy.set_proxy()`, `idea_service_account.setup_account()`, `shared_storage.configure()`
- `ebs_volume_tags.setup()`, `network_interface_tags.setup()`
- `active_directory.configure()`, `cognito_modules.configure()`, `set_sudoers.configure()`, `ssh_keygen.configure()`, `hibernation.resume_logger()`
- `on_vdi_configured()` and RES customization scripts

**Blocking source review:** prove whether each call is truly RES-only; retain `amazon_ssm_agent.configure()`, `chronyd.configure()`, `gnome_utils.configure()`, `dcv.configure()`, `source_launch_env_file()`, lock creation and reboot logic as required. A blanket skip is prohibited. Do not create a diff for this file until each call is classified.

## D-005: runtime installation scripts / application handoff

**No safe exact diff yet.** The broker token and app package discovery path must be traced at each shell call site, and the Python application initialization invocation contract must be established. This is a genuine Gate 4 blocker. Preserve `install.sh`, `install_post_reboot.sh`, and the SDK user-data template. Do not replace with a custom bootstrap framework.

## D-006/D-007 preservation

**Zero proposed changes** to original GPU/DCV installer, GNOME/Xorg, DCV service unit, workstation SDK user-data template, cloud-init `scripts-user, always`, reboot semaphore files and reboot sequence.

## Review / validation before approval

1. Resolve exact DCV package bytes/version, version-matched authentication and permission syntax; validate owner/non-owner policy and TLS.
2. Validate proposed diff hunks mechanically against pinned upstream source, and add tests for standalone branch and unchanged RES branch.
3. Classify all `bootstrap.py` side effects and identify exact D-005 shell/app handoff substitutions; generate remaining complete unified diffs.
4. Confirm Option A SSM credential enrollment and session recording/privacy settings; do not place credentials in logs or user data.
5. No AWS IAM, EC2 or CloudFormation changes without separate approval.

**Decision: PARTIAL patch specification only; implementation remains blocked.**
