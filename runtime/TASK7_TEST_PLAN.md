# Task 7 controlled GPU test — staging and launch gate

Status: **STAGING ONLY — NO INSTANCE LAUNCH AUTHORIZED BY THIS DOCUMENT.**

## Immutable inputs

- Source: draft PR #61, branch `feature/res-2026-06-standalone-runtime`; pin the exact reviewed head SHA at test time.
- Upstream RES: 2026.06. Build via `runtime/build_res_overlay.py` from the two public upstream archives with hard-coded SHA256 verification.
- GitHub Actions `task7-stage-artifacts.yml` creates two derived tarballs, `SHA256SUMS`, and `provenance.json`; the workflow uploads a short-lived **GitHub Actions artifact**, not S3.
- Verify the two *derived* SHA256 digests after downloading the artifact and again after uploading to S3. Do not substitute the previously published `v0.1.3` archives, which do not include PR #61 fixes.
- Proposed temporary object prefix (not created): `s3://studio-workstation-releases-401233741942-us-east-1/releases/task7-pr61-<reviewed-sha>/res-2026.06/`.
- Existing instance profile `StudioWorkstationSSMRole` allows `s3:GetObject` on `releases/*/res-2026.06/{installation-scripts,virtual-desktop}.tar.gz` **only**. The proposed prefix matches this existing resource pattern; verify actual access from the instance role before launch. No IAM policy changes are planned.

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

## Launch-readiness review — 2026-10-31

**Do not launch as part of this review.** The pinned derived archives are published under `releases/task7-pr61-c73e905/res-2026.06/` and the GitHub publishing run 41179637293 attempt 2 passed OIDC, S3 upload and byte-for-byte readback. Pin source commit and both S3 object versions/digests in the execution record.

### Exact launch parameters

- AMI `ami-084caad6c89407d25`, region `us-east-1`, instance type `g4dn.xlarge`, example default VPC `vpc-01bbe5d01838f70b2`, subnet `subnet-0986937ce71fb97cb` (us-east-1a). Check AZ capacity and public egress at launch time.
- IAM instance profile `StudioWorkstationSSMRole` has `AmazonSSMManagedInstanceCore` and S3 GetObject permission on the two named archives in the release prefix. A live instance-role GetObject remains an acceptance check.
- Render `runtime/render_workstation.py --profile gpu --bucket studio-workstation-releases-401233741942-us-east-1 --prefix releases/task7-pr61-c73e905/res-2026.06 --user workstation --output <reviewed-file>`. Never use the placeholder template directly. The original RES cloud-init multipart and bootstrap script must be preserved.
- GPU-only output enforces IMDSv2, g4dn.xlarge, 100 GiB encrypted gp3, root DeleteOnTermination, terminate-on-shutdown, and instance tags `Task7=PR61` plus `Issue=60`. The template creates a fresh SG with TCP 8443 only, no SSH; its default `127.0.0.1/32` denies remote DCV. Supply a *currently verified trusted* /32 only if a real client login is required. Never open 0.0.0.0/0 for this test.
- The local account is created with a locked password; only set a temporary strong password through SSM after verifying the instance is managed. Do not embed secrets in user-data or GitHub logs.

### Fail-safe launch-and-schedule transaction (future, separately approved)

1. Before creating any instance, verify the Scheduler execution role `studio-task7-pr61-terminator` and policy. Its only termination permission requires both `Task7=PR61` and `Issue=60` tags on the instance. Verify scheduler service trust and source-account/schedule ARN restrictions.
2. Create one tagged EC2 instance through the reviewed CloudFormation template. **Treat the period between instance creation and schedule confirmation as a critical section**; no diagnostics or unattended waits in between. Capture the actual EC2 instance ID immediately (CloudFormation stack event/resource lookup as needed). Set a short bounded deadline, e.g. 90 minutes after launch.
3. Immediately call EventBridge Scheduler `CreateSchedule` with a unique name `studio-task7-pr61-<instance-id>`, `ScheduleExpression=at(<UTC deadline>)`, `ScheduleExpressionTimezone=UTC`, `FlexibleTimeWindow.Mode=OFF`, `ActionAfterCompletion=DELETE`, and `Target.Arn=arn:aws:scheduler:::aws-sdk:ec2:terminateInstances`, `Target.RoleArn=arn:aws:iam::401233741942:role/studio-task7-pr61-terminator`, `Target.Input={"InstanceIds":["<instance-id>"]}`. Use a bounded retry policy. **Do not** use the old Terraform/CodeBuild expiration role.
4. Read the schedule back with `GetSchedule` and verify exact instance ID, execution role, state ENABLED, UTC deadline, and action. Verify the instance tags, EBS DeleteOnTermination, and security group. If CreateSchedule or verification fails, **immediately TerminateInstances**, wait for terminated state, delete the CloudFormation stack and inspect any orphaned EBS/SG. Never leave the instance running without a verified independent schedule.
5. Only after schedule confirmation proceed with SSM, GPU, Xorg, DCV, PAM, OpenGL and reboot tests. Monitor the deadline; do not assume schedule execution is proof of termination. Terminate early after evidence capture, confirm instance terminated and root EBS deleted, delete CloudFormation stack and any leftover resources, then delete temporary archive objects only after evidence is preserved.

**Caveat:** CloudFormation stack creation may take time and is not itself an atomic instance+schedule transaction. The actual launcher must monitor EC2 creation promptly, fail closed on errors, and perform cleanup. The Scheduler role is scoped by tags, not a pre-known instance ID; the schedule itself must bind the actual ID. No independent schedule can yet be verified because the test instance does not exist.

## Safe-launch implementation checkpoint

- Immutable reviewed template: `s3://studio-workstation-releases-401233741942-us-east-1/releases/task7-pr61-c73e905/res-2026.06/gpu-template.json`, published by GitHub Actions run `41180351773` with successful S3 readback, S3 version `G8ptUdG7U0o1neh.B.Q32BjY0XQrn9j9`. Preserve this object version and hash for launch.
- Publisher IAM was extended to allow only this third exact object key, retaining the two previously authorized archive keys and branch-restricted OIDC trust.
- `runtime/task7_safe_launcher.py` implements a **dry-run default** and an explicit `--execute` opt-in. It reads and validates the reviewed S3 template, launches at most one g4dn.xlarge with a closed inbound SG, scoped tags, IMDSv2, encrypted delete-on-termination root EBS, and immediately creates and verifies an instance-ID-specific one-time Scheduler termination target. Schedule failure or mismatch triggers immediate `TerminateInstances`; launch failure removes unused SG.
- `tests/test_task7_safe_launcher.py` mocks successful scheduling, creation failure, readback mismatch, launch rollback and deadline bounds. These tests were executed successfully in GitHub Actions run `41180351773`; **no EC2 instance was launched**.
- Remaining execution concerns: this launcher is not yet validated on a live instance; no schedule exists before an instance ID exists; CloudFormation is not used for the direct EC2 path; SG cleanup and verification of eventual EBS deletion must be done after test. The test SG has zero ingress by design, so actual DCV login needs a separately reviewed temporary /32 ingress change. Ensure SSM connectivity and release downloads via egress before declaring host readiness. A launcher crash between EC2 creation and schedule creation is not covered by the mock tests; run under supervision and do not leave unattended.

## Live launch attempt and Scheduler trust correction

First attempt instance `i-0fc2743dfc745d901` was immediately terminated after Scheduler rejected its execution role. The original role trust restricted `aws:SourceArn` to a schedule ARN, but Scheduler requires the schedule **group** ARN. Dedicated group `studio-task7-pr61` now exists and role trust uses its exact ARN with account restriction. A second RunInstances attempt failed `VcpuLimitExceeded` while the first was still shutting down; its unused SG was deleted. Wait for termination and volume deletion before retry. No GPU/DCV tests completed yet.
