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

1. **Partially implemented:** selectively guard RES-only storage, AD, sudo, proxy, tagging, identity and customization calls in `bootstrap.py`; mocked standalone behavior and preservation of essential host setup still require proof.
2. **Candidate implemented, not proven:** use original Supervisor installation to invoke a small Python entrypoint calling upstream `bootstrap.run()` instead of the `ResApp` event loop. Verify execution/reboot behavior and all remaining imports and environment inputs.
3. **Implemented, CI pending:** build both derived archives from SHA-256-verified original RES packages, replacing selected existing modules and adding the standalone entrypoint inside the original Python source distribution. The new integration workflow checks untouched members remain byte-identical. Neither archive is uploaded or deployed.
4. Establish exact safe `STUDIO_STANDALONE` input propagation from original SDK user-data through both reboot stages and Python process.
5. Verify DCV version-specific PAM, permissions grammar, file modes, TLS, and Option A interactive SSM account enrollment. Validate proposed timeout values and local owner existence.
6. Run archive integration CI, then add mocked standalone-branch and unchanged-RES-branch behavioral tests and rerun T0–T4. Archive integration is not a runtime test.
7. Before a controlled EC2 deployment, obtain explicit AWS/IAM approval and verify the standalone role can read the original archives.

## Review gates

- No claim of working standalone runtime, real DCV login, or GPU rendering.
- No IAM, EC2, CloudFormation or Image Builder changes.
- Do not merge this draft until the blockers are resolved and tests pass.
- Preserve D-006 (original NVIDIA/DCV/GNOME/Xorg behavior) and D-007 (original cloud-init, semaphore and reboot continuation).
