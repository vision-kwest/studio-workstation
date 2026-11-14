# Studio Workstation CLI — Quick Start and daily operations

**Current as of November 2026 (PRs #69, #73, #76, #79, #80, #85, #87, #89 and #91 merged).** Source of truth: [`scripts/studio_workstation_cli.py`](../scripts/studio_workstation_cli.py), [entry point](../studio), [GPU create guard](../runtime/pr65_gpu_create.py), [one-shot expiration helper](../runtime/pr65_one_shot_guard.py). Historical PR #65 gates and earlier CLI syntax are archived; see the [documentation index](INDEX.md).

The CLI uses the existing RES-derived CloudFormation renderer and AWS CLI. It does not install a control plane, change the working Image Builder pipeline, or expose SSH.

## Requirements

Use a repository checkout in AWS CloudShell or another machine with:

- Python 3, `jinja2` (from `requirements.txt`), AWS CLI v2, suitable credentials and access to `us-east-1`.
- The published immutable RES-derived release archives in `studio-workstation-releases-401233741942-us-east-1`.
- Existing EC2 instance profile `StudioWorkstationSSMRole` with SSM and permitted release reads; approved VPC `vpc-01bbe5d01838f70b2` / subnet `subnet-0986937ce71fb97cb`.
- Access to CloudFormation, EC2 and SSM. The GPU controlled-acceptance flow also requires its pre-established narrowly scoped Scheduler role and group.

```bash
cd ~/studio-workstation
git fetch origin
git switch main
git pull --ff-only
python3 -m pip install -r requirements.txt
python3 studio workstation --help
```

The command is **`python3 studio workstation`**. It is not an installed system-level `studio` binary.

## Command behavior — no redundant confirmations

| Command | Behavior |
|---|---|
| `create --stack NAME --profile cpu` | **Creates** a CPU CloudFormation stack and EC2 instance; incurs charges |
| `create --stack NAME --profile gpu` | **Creates** only if the exact GPU acceptance stack/token and guard checks pass |
| `create ... --dry-run` | Renders and calls CloudFormation ValidateTemplate; **no EC2 launch** |
| `status --stack NAME` | Reads EC2 state and DCV URL; for the exact GPU, verifies both Scheduler safeguards and displays termination/cleanup UTC, remaining minutes and expiry warnings |
| `check-ready --stack NAME` | Uses SSM Run Command for guest-side RES/DCV/GPU diagnostic checks; doesn't prove a human login |
| `connect --stack NAME` | For the legacy default or guarded GPU stack, verifies trusted EIP ownership; other CPU stacks can opt in with `--hostname dcv.kwesidavis.com`, which now verifies EIP owner, DNS and TLS; without `--hostname` they print the raw-IP URL |
| `connect --stack NAME --ssm` | Opens an administrative SSM Session Manager session |
| `stop --stack NAME` | Stops the resolved EC2 instance; waits for `stopped` |
| `start --stack NAME` | Starts the same instance; approved GPU **first** checks its exact PR #65 Scheduler/IAM safeguards and requires at least 80 minutes remaining before EC2 start, then checks RES/ACM/EIP and trusted HTTPS; approved CPU waits for trusted HTTPS |
| `extend-expiration --stack NAME --additional-minutes N` | Immediately extends the approved GPU's exact EC2 and EBS expiration schedules (no `--execute` or preview); validates IAM, ownership and bounded deadlines first |
| `destroy --stack NAME --delete-stack` | Guarded GPU teardown verifies exact expiration, IAM and EBS resources; CPU teardown verifies its CloudFormation ownership and requires the independent shared EIP to be assigned elsewhere or unassociated; original test CPU anchors have been deleted. Failures require operator reconciliation |

`--yes`, `--confirm-stack`, and `--confirm-instance` **were removed**. Running a command is authorization; you will not be asked to type the same stack or instance twice. `--delete-stack` remains mandatory for destructive teardown; `--dry-run` is how to preview creation. AWS subprocesses pass `--no-cli-pager`, so CloudShell should no longer stop at `(END)` after ValidateTemplate.

### CPU workstation — create and connect

First preview the exact inputs:

```bash
python3 studio workstation create \
  --stack studio-workstation-cpu-review \
  --profile cpu --version v0.1.3 \
  --client-cidr YOUR_PUBLIC_IP/32 --dry-run
```

When ready to create a billable instance, omit `--dry-run`:

```bash
python3 studio workstation create \
  --stack studio-workstation-cpu-review \
  --profile cpu --version v0.1.3 \
  --client-cidr YOUR_PUBLIC_IP/32
```

CPU defaults currently include `m7i.large`, encrypted 100 GiB gp3, CPU AMI `ami-0d6dd9e8265d48906`, the account-specific VPC/subnet/profile above, and **public** `ClientCidr=0.0.0.0/0` unless overridden. Supply a trusted CIDR rather than relying on this default. You may override CPU instance type, disk, AMI, subnet and instance profile; inspect the rendered template before deployment. A new `create` refuses an existing stack name instead of silently updating it.

Wait for RES bootstrap and reboots, then enroll a strong unique local password. Do not put passwords into user data or SSM Run Command parameters:

```bash
python3 studio workstation connect --stack studio-workstation-cpu-review --ssm
# In the SSM session:
sudo passwd workstation
# Leave the SSM session:
exit
python3 studio workstation connect --stack studio-workstation-cpu-review
```

DCV login uses local Linux username `workstation`, not AWS IAM credentials. The browser may show an untrusted/self-signed TLS warning; verify the certificate fingerprint through an administrative channel or provision trusted TLS.

### Independent trusted DCV hostname (Issue #77; merged PR #79)

The EIP allocation `eipalloc-0879a34aa1a1298dd`, IPv4 `54.159.180.104`, Route 53 `dcv.kwesidavis.com`, and ACM certificate are **not bound to a permanent CPU EC2 instance**. As of the November 13 cleanup, all old instances are terminated and the EIP remains allocated but unassociated.

Use a running, verified Studio CPU stack (or the independently guarded GPU) to claim the unassociated IP. `hostname-assign` checks stack, instance identity, current ownership, guest DCV and trusted certificate readiness before writing the association; `hostname-release` only detaches a verified stopped owner. Both preserve the allocation, DNS and ACM.

```bash
# Read-only AWS status:
aws ec2 describe-addresses --allocation-ids eipalloc-0879a34aa1a1298dd --region us-east-1
# For a stopped, verified previous owner, or when already unassociated:
python3 studio workstation hostname-release --execute
# After provisioning/starting a verified replacement:
python3 studio workstation hostname-assign --stack STACK_NAME --execute
# Verify trusted owner + DNS + TLS, then use the URL:
python3 studio workstation connect --stack STACK_NAME --hostname dcv.kwesidavis.com
```

The CLI's older `DEFAULT_STACK` still points to a deleted historical CPU; **always pass an explicit stack name** for actual workstations. A normal, newly created CPU stack may connect via raw IP until the shared EIP and trusted certificate have been configured and assigned. Do not assume `--hostname` alone installs a certificate.

For GPU handoff, use only the pinned `studio-gpu-cli-accept65` acceptance scope and its independently confirmed one-shot Scheduler guards. The historical handoff design and recovery records are archived in [Issue 43](issue43-shared-cpu-gpu-handoff.md); current ownership behavior lives in [independent EIP](independent-eip.md). EIP release is safe when no EC2 is attached; an unassociated address cannot serve DCV.

### Publisher profile (merged PR #76)

Publishing is performed by a disposable, on-demand EC2 publisher with `StudioEfsCiPublisher` IAM/TLS EFS identity and SSM, **not** by a GitHub Actions self-hosted runner.

```bash
python3 studio workstation publisher-create --security-group-id sg-004ad23682c7db931
python3 studio workstation publisher-status --instance-id INSTANCE_ID
python3 studio workstation publisher-run --instance-id INSTANCE_ID --operation check
python3 studio workstation publisher-run --instance-id INSTANCE_ID --operation build \
  --recipe-repo vision-kwest/studio-workstation --recipe-ref REVIEWED_40_CHAR_COMMIT \
  --recipe-path tests/fixtures/publisher_acceptance_build.sh
python3 studio workstation publisher-result --instance-id INSTANCE_ID --command-id SSM_COMMAND_ID
python3 studio workstation publisher-run --instance-id INSTANCE_ID --operation promote-test --name PACKAGE --version VERSION
python3 studio workstation publisher-run --instance-id INSTANCE_ID --operation promote-prod --name PACKAGE --version VERSION
python3 studio workstation publisher-stop --instance-id INSTANCE_ID
```

The publisher profile persists as reusable IAM infrastructure; the earlier acceptance instance was terminated. Every new publisher instance incurs costs and requires independently verified SSM/EFS access and cleanup. Production safety is incomplete for untrusted recipes: the publisher role has filesystem-wide write capability and build recipes currently run with elevated rights. See [publisher runbook](publisher.md).

### Optional software-enabled CPU/GPU provisioning (merged PRs #73/#80)

`create --rez-version-id VERSION_ID --rez-sha256 VERIFIED_SHA256` opts in to the immutable Rez 3.4.0 bundle and read-only `/opt/studio/packages` EFS. Both values must be supplied together and match the **actual published** S3 object version and checksum. The software setup is independent of DCV availability; workstation users resolve production packages only. Never invent a version ID or hash.

Tests for GPU DCV bootstrap, EFS installer staleness and hostname planning were subsequently strengthened in PRs #85, #87, #89 and #91. Historic acceptance reports are not substitutes for a fresh live test.

## GPU workstation: tested acceptance scope

**What is proven:** PR #65, now merged, was tested live using the exact scoped GPU CLI launch, user-confirmed DCV login, Tesla T4/OpenGL/RES readiness, EC2 stop/start, identical saved-file SHA-256 across reboot, and independent final cleanup. See [Issue #46](https://github.com/vision-kwest/studio-workstation/issues/46). That **does not** mean all arbitrary new stack names, regions or account setups are supported.

The **current implementation** only allows this account-specific GPU scope:

| Input | Required/validated value |
|---|---|
| Region | `us-east-1` |
| AMI | `ami-027c98f85b155c2ac` (separate from CPU) |
| Instance type | `g4dn.xlarge` (Tesla T4) |
| Stack name | `studio-gpu-cli-accept65` **only** |
| Cleanup token (ownership label, not credential) | `pr65-b23c8e5fdf67b683cd35` **only** |
| Root storage | Encrypted 100 GiB gp3, **retained** on termination |
| EC2 role | `StudioWorkstationSSMRole` |
| Scheduler group | `studio-task7-pr61` |
| Scheduler role | `studio-pr65-cli-expiration` (restricted to exact stack/token tags) |

The original acceptance stack and EC2 have been deleted, so this is an example of the **pinned, one-test configuration**, not a recommended recurring production workload. For a different GPU stack/token, the hardcoded scope and exact IAM policy must be deliberately updated and revalidated; **changing just `--stack` will fail**.

The GPU RES installer and application archives are fetched from **different S3 prefixes** and pinned to their own immutable object versions/SHA-256:

- Installer: `releases/pr65-pinned-download-084e704e/res-2026.06/installation-scripts.tar.gz`, VersionId `bN0To3QzfVqVsN5P_6YmTraGIQuHcrtB`.
- Application: `releases/task7-pr61-postreboot-a8ec326/res-2026.06/virtual-desktop.tar.gz`, VersionId `nzyYrLhYAQ.OTFNpFE_xSHPaK06T1V53`.

SHA-256 hashes and RES adaptations are recorded in [runtime/README.md](../runtime/README.md).

To review the currently pinned, **non-general** GPU acceptance launch without creating resources:

```bash
python3 studio workstation create \
  --profile gpu --stack studio-gpu-cli-accept65 \
  --cleanup-token pr65-b23c8e5fdf67b683cd35 \
  --client-cidr 0.0.0.0/0 --allow-public-dcv \
  --dry-run
```

To reproduce the **billable, time-limited acceptance test** with this exact scope, omit `--dry-run` after confirming the referenced resources, IAM policies, cost exposure and cleanup mechanism are appropriate:

```bash
python3 studio workstation create \
  --profile gpu --stack studio-gpu-cli-accept65 \
  --cleanup-token pr65-b23c8e5fdf67b683cd35 \
  --client-cidr 0.0.0.0/0 --allow-public-dcv \
  --expires-in 180m --volume-cleanup-delay 45m
```

`--expires-in` accepts 60–240 whole minutes (`150m` default) and
`--volume-cleanup-delay` accepts 30–120 whole minutes (`45m` default).
The existing exact PR #65 stack/token/instance/role safeguards remain mandatory.
Values are applied to both separately verified one-shot schedules, not to a
persistent workstation service.

**This example intentionally exposes TCP 8443 to any public IPv4 address.** `--allow-public-dcv` is the explicit opt-in; it is **not** a VPN, identity-aware gateway or a recommendation for permanent public access. Prefer a trusted public `/32` without `--allow-public-dcv` when feasible. The scoped GPU flow refuses broad public ingress unless the flag is supplied. SSH ingress remains closed.

The GPU create flow verifies the actual restricted Scheduler IAM role and group, issues an asynchronous CloudFormation `CreateStack`, identifies the tagged instance and attached root EBS within a bounded discovery period, then arms and reads back **two one-time EventBridge schedules**: EC2 termination after the configured duration (**150 minutes by default**) and an EBS deletion attempt after the configured delay (**45 minutes by default**). These are **fail-safe acceptance deadlines, not an always-on workstation service**. They remain active through stop/start. The EBS deletion attempt is **best-effort** and cannot be assumed to succeed if storage remains attached. A crash between `CreateStack` and schedule creation still requires manual supervision.

After creating, check GPU readiness, enroll the password through SSM, and connect:

```bash
python3 studio workstation check-ready --stack studio-gpu-cli-accept65
python3 studio workstation connect --stack studio-gpu-cli-accept65 --ssm
# Inside SSM: sudo passwd workstation
python3 studio workstation connect --stack studio-gpu-cli-accept65
```

A newly launched GPU instance can take multiple minutes and reboot during RES startup. Readiness means `linux_vdi_config_host_ready.lock`, GDM, DCV, console ownership, Xorg and GPU OpenGL all pass; `check-ready` does not replace an actual interactive login. A TLS certificate can still be untrusted.

## Daily operation and safe cleanup

From the **authorized management checkout**:

```bash
python3 studio workstation status --stack STACK_NAME
python3 studio workstation connect --stack STACK_NAME
python3 studio workstation stop --stack STACK_NAME
python3 studio workstation start --stack STACK_NAME
python3 studio workstation connect --stack STACK_NAME
python3 studio workstation destroy --stack STACK_NAME --delete-stack
```

### Explicit GPU expiration extension (merged PR #69)

On an **existing** GPU in the approved exact PR #65 stack, this single command
**writes both EventBridge schedules**; specifying `--additional-minutes` is the authorization:

```bash
python3 studio workstation status --stack studio-gpu-cli-accept65
python3 studio workstation extend-expiration \
  --stack studio-gpu-cli-accept65 \
  --additional-minutes 60
```

There is **no `--execute` flag and no preview workflow** for extension. One
request adds 1–180 minutes to both existing deadlines. The current termination
must still be at least 15 minutes away; the resulting termination must be 80–240
minutes from now. The existing 30–120-minute gap to retained-EBS cleanup is
preserved. The CLI verifies exact CloudFormation/EC2/EBS identity, original PR #65
IAM and both schedules before updating **EBS cleanup first**, then EC2 termination.
It reads back both updates. A verified partial failure attempts safe EBS rollback;
an ambiguous state fails closed and needs operator reconciliation. The operation
does **not** restart a stopped instance, change CPU/EIP/DNS/ACM resources, or
promise indefinite GPU operation. The functionality is merged; always verify the current schedules and approvals before a live extension.

For an approved GPU, `start` verifies both original independent expiration schedules and required restart headroom before billing. `extend-expiration` never changes hostname, DNS or ACM resources. The shared EIP may be unassociated or owned by a verified stopped CPU when starting a GPU; the former fixed original CPU anchor is no longer required by the new GPU source preflight.

The `destroy --delete-stack` operation is irreversible. It must fail closed on unexpected stack identities, foreign EIP ownership, attached or unverified EBS, unexpected CloudFormation resources, partial deletions or IAM failure. CPU teardown requires the EIP to be released first; it preserves the EIP allocation, ACM, DNS and shared EFS. GPU teardown retains independent expiry safeguards until exact-resource cleanup. Never treat a failed destroy as success.

**Current deployment:** There are no active workstation or publisher EC2 instances after the November 13 test-fleet retirement. New live acceptance should use disposable stacks, bounded costs and independent cleanup, not the former `cpu-v013` or `cpu-rez77` stacks.

## Other references

- [Quick Start](../README.md#quick-start) and [documentation index](INDEX.md)
- [GPU runtime, pinning and provenance](../runtime/README.md)
- [CPU acceptance history](cpu-acceptance-lifecycle-tls.md)
- [Architecture](visual-architecture.md) and [lifecycle diagram](workstation-lifecycle-visual.md)
- [Optional future secure access-from-anywhere](https://github.com/vision-kwest/studio-workstation/issues/62)
