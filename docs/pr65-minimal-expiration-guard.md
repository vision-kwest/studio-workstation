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

## Task 2 — CLI wiring and bounded discovery (offline only)

- `scripts/studio_workstation_cli.py` now routes approved scoped GPU
  creation to `runtime/pr65_gpu_create.py`, not the synchronous
  `cloudformation deploy` waiter. CPU behavior is unchanged.
- **GPU live creation is still disabled** by
  `GPU_ACCEPTANCE_LIVE_ENABLED = False`. An attempted GPU `--yes`
  create fails before the renderer or any AWS operation. Unscoped GPU
  `--yes` is forbidden as well.
- Once separately enabled in Task 3, the helper checks that the existing
  Scheduler group is active, and that the new dedicated Scheduler role
  has the exact expected trust and *only* two tag-scoped IAM grants.
  Invalid role/schedule configuration blocks CloudFormation creation.
- CloudFormation `CreateStack` is asynchronous, with
  `--disable-rollback` so failures cannot silently remove resource
  identity evidence before the CLI can inspect it. Within a bounded
  120-second discovery window (three-second polling), the CLI verifies
  the newly created stack ID and tags, reads CloudFormation resource
  IDs, verifies the EC2 instance and its **attached** retained
  `/dev/sda1` EBS volume, and arms both one-time Scheduler actions.
  Each scheduled action is independently read back by the existing
  Task 1 helper.
- Failed or timed-out discovery requests **emergency instance termination
  only for a tag-verified instance**, then requests CFN stack deletion
  **only if stack ownership has been verified**. It always fails with an
  explicit retained-EBS/manual-cleanup warning. This is a best-effort
  fail-closed operation, not proof that retained storage has disappeared.
  If the caller is interrupted or the process dies between CreateStack
  and arming, there remains a short unscheduled exposure window.
- Task 3 must establish and verify the dedicated role
  `studio-pr65-cli-expiration` with its single inline policy named
  `StudioPr65ExactTaggedExpiry`, scoped to the chosen exact
  `StudioStack` and `StudioCleanupToken` tags. No role has been
  created or modified in Task 2.
- Relevant tests:
  `python3 -m unittest discover -s tests -p test_pr65_gpu_create.py -v`
  and `test_pr65_one_shot_guard.py`.
- **Do not activate the live-launch switch in Task 2.**
  No EC2, S3, Scheduler, IAM, CFN, or Image Builder resources are
  created or changed by this code commit.
