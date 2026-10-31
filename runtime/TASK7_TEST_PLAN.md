# Task 7 controlled GPU test — staging and launch gate

Status: **STAGING ONLY — NO INSTANCE LAUNCH AUTHORIZED BY THIS DOCUMENT.**

## Immutable inputs

- Source: draft PR #61, branch `feature/res-2026-06-standalone-runtime`; pin the exact reviewed head SHA at test time.
- Upstream RES: 2026.06. Build via `runtime/build_res_overlay.py` from the two public upstream archives with hard-coded SHA256 verification.
- GitHub Actions `task7-stage-artifacts.yml` creates two derived tarballs, `SHA256SUMS`, and `provenance.json`; the workflow uploads a short-lived **GitHub Actions artifact**, not S3.
- Verify the two *derived* SHA256 digests after downloading the artifact and again after uploading to S3. Do not substitute the previously published `v0.1.3` archives, which do not include PR #61 fixes.
- Proposed temporary object prefix (not created): `s3://studio-workstation-releases-401233741942-us-east-1/task7/pr61/<reviewed-sha>/`.
- Existing instance profile `StudioWorkstationSSMRole` allows `s3:GetObject` on `releases/*/res-2026.06/{installation-scripts,virtual-desktop}.tar.gz` **only**. The proposed `task7/` prefix is NOT covered. Either use a reviewed permitted release path or request narrowly scoped temporary IAM permission before launching. No policy changes have been made.

## Launch controls

- Region us-east-1; existing AMI `ami-084caad6c89407d25`; g4dn.xlarge; IMDSv2 required.
- Render original RES user-data with `runtime/render_workstation.py --profile gpu`, with reviewed bucket/prefix and local `workstation` account. Verify both archives are present before using user-data.
- GPU renderer sets `ClientCidr` default to `127.0.0.1/32` (closed for remote clients), root EBS `DeleteOnTermination=true`, and `InstanceInitiatedShutdownBehavior=terminate`. CPU template remains unchanged. Explicitly supply a trusted client /32 only when interactive DCV login is needed. No inbound SSH.
- Do not deploy the unrendered `runtime/workstation-template.json`; it contains `RENDER_OFFLINE` placeholder and CPU defaults.
- Configure an external time-limited cleanup safeguard **before** launch (e.g. approved EventBridge Scheduler or other independent termination mechanism), verify its target and execution role, and tag all test resources with issue 60 / PR 61. This is not yet implemented. On completion terminate instance, confirm EBS deletion and security group removal, delete temporary staged S3 objects and cleanup schedule, and confirm no remaining billable resources.
- The local user password is initially locked; use interactive SSM enrollment for credentials. No password in user-data or logs.

## Evidence and acceptance

Capture SSM status, cloud-init, Supervisor/bootstrap and post-reboot logs, semaphore timestamps, `nvidia-smi`, GDM/Xorg logs, Xorg auth, `dcv list-sessions`, `dcv describe-session console --json`, DCV configuration without external verifier, PAM/system authentication, permissions grammar and file ownership, and `glxinfo -B` reporting Tesla T4 rather than llvmpipe. Verify a real local-user DCV login if feasible. Reboot and repeat readiness. Do not mark success based only on mocked CI.

## Blocking gates

1. The derived archives must be downloaded, independently hash-checked, and placed in S3 with matching instance-profile read permission.
2. The exact rendered user-data and root-volume cleanup must be validated.
3. An independent automatic termination mechanism must be configured and tested.
4. The user must separately approve the **actual instance launch** after the above gates are satisfied.

No AMI pipeline modifications, merge, or GPU launch are part of this preparation.
