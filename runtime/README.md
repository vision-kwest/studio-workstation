# RES 2026.06 standalone implementation candidate

**Status: DRAFT / NOT DEPLOYABLE.** This branch contains concrete upstream-derived code changes for review, **not** a complete, approved workstation provisioning path. No AWS resources have been created or modified.

## Upstream pin and approach

Source: `aws/res` commit `1f526a241fb293a29fb018f08ccf81c100c05a42`. Files under `runtime/overrides/` are full copies of the corresponding original upstream files, with small guarded standalone branches. Apache-2.0 copyright headers retained. Existing non-standalone branches remain for comparison.

**Do not deploy or import these files on a live host.** They are not wired into the RES tar archives or user-data yet. This is deliberate: packaging and app lifecycle must be reviewed before they can be used.

## Concrete code in this PR

| File | Standalone change |
|---|---|
| `scripts/virtual-desktop-host/linux/install.sh` | Skip Cognito PAM/NSS host module install; skip broker-token file in standalone mode. Preserve first-stage packages, semaphore, reboot and post-reboot handoff. |
| `scripts/common/linux/install_app.sh` | Skip RES broker, use `STUDIO_APP_PACKAGE_URI` instead of DynamoDB for original application archive; pass standalone environment to Supervisor. **Original `resserver` startup is still present and is not validated standalone.** |
| `ideavirtualdesktop/app/linux/bootstrap.py` | Skip Cognito PAM/NSS configuration. **Other RES-specific calls remain and will fail without cluster services.** |
| `ideavirtualdesktop/app/linux/post_reboot.py` | Skip RES idle cron and session-state reporting, retain console creation/readiness and existing semaphore. |
| `ideabootstrap/dcv/linux/dcv_host.py` | Use local timeout inputs and `authentication="system"` instead of RES ALB verifier. |
| `ideabootstrap/dcv/dcv.py` | Use owner-only candidate permissions, skip RES state reporting; preserve console configuration and polling. |
| `ideabootstrap/dcv/linux/gl.py` | Use local GPU family list, preserve upstream GL actions. |

## Remaining blockers for this SAME implementation PR

1. Trace and selectively isolate `bootstrap.py` remaining RES-specific operations: shared storage, AD/SSSD, sudo, proxy, tagging, identity and customization. Do not blanket skip.
2. Resolve how original `resserver` / Supervisor initialization is invoked without RES control plane. No replacement systemd state machine or alternate installer allowed.
3. Integrate the reviewed overrides into the **original verified RES archives** in a deterministic, checksum-aware build workflow, without replacing the original user-data template or stage ordering.
4. Establish exact safe `STUDIO_STANDALONE` input propagation from original SDK user-data through both reboot stages and Python process.
5. Verify DCV version-specific PAM, permissions grammar, file modes, TLS, and Option A interactive SSM account enrollment. Validate proposed timeout values and local owner existence.
6. Add mocked standalone-branch tests and unchanged-RES-branch regression tests, then rerun T0–T4. Existing CI in this PR is syntax/static only.
7. Before a controlled EC2 deployment, obtain explicit AWS/IAM approval and verify the standalone role can read the original archives.

## Review gates

- No claim of working standalone runtime, real DCV login, or GPU rendering.
- No IAM, EC2, CloudFormation or Image Builder changes.
- Do not merge this draft until the blockers are resolved and tests pass.
- Preserve D-006 (original NVIDIA/DCV/GNOME/Xorg behavior) and D-007 (original cloud-init, semaphore and reboot continuation).
