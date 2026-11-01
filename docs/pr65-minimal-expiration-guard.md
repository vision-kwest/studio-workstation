# PR #65 — one-off GPU CLI expiration safeguard (Task 1)

**Offline only. No IAM changes, schedules, EC2, or stack deployments.**
The current GPU CLI launch block remains in place. The existing Lambda/DynamoDB
cleanup subsystem is preserved in GitHub but deferred for this supervised test.

Reuse the one-time EventBridge Scheduler pattern from `runtime/task7_safe_launcher.py`:
create, read back, verify and fail closed. The existing `studio-task7-pr61`
Scheduler group exists, but the Task 7 execution role is **not compatible**
with the PR #65 workstation ownership tags.

`runtime/pr65_one_shot_guard.py` verifies one exact scoped stack/token,
instance ID, root EBS volume ID, two ownership tags on each, retained
`/dev/sda1` block mapping and its actual attachment. It then plans:

1. Exact-instance `ec2:terminateInstances` after 60–240 minutes (default 150).
2. Exact-root-volume `ec2:deleteVolume` 30–120 minutes later (default 45).

Each one-time schedule uses `at(...)`, UTC, no flexible window, 3 retries,
and Scheduler readback verification. If arming either schedule fails, the
helper requests immediate EC2 termination and raises. This is **not**
evidence that the EBS volume was deleted.

A small **dedicated** Scheduler execution role
`studio-pr65-cli-expiration` is needed later, trusting
`scheduler.amazonaws.com` in the existing group and granting only the
two operations above, conditioned on both matching `StudioStack` and
`StudioCleanupToken` EC2 resource tags. **Do not change/reuse** the
existing Task 7 termination role. The new role is NOT created in Task 1.

**Task 2 prerequisite:** The current CLI `cloudformation deploy` is
blocking. It must discover the instance and EBS promptly, arm and verify
both schedules, and fail closed instead of leaving an instance unattended
during a long CloudFormation create or rollback. No CLI launch-block
removal is authorized in Task 1.

**Task 3 prerequisite:** Approve creation and verification of the narrowly
scoped Scheduler role and both independent schedules before a live test.

**Teardown:** After test file persistence through stop/start, delete the
exact stack; verify instance termination, retained volume detachment,
and delete only the known tag-verified volume. Independently confirm
no EC2 instance, EBS volume, SG, LaunchTemplate, or active expiry
schedule remains. Do not cancel schedules until cleanup is confirmed.
If the timed EBS deletion encounters an attached volume or API failure,
manual intervention is mandatory: the schedule is a best-effort attempt,
not a no-orphan guarantee.

Focus test: `python3 -m unittest discover -s tests -p test_pr65_one_shot_guard.py -v`.
