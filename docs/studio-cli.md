# Studio Workstation CLI — Quick Start and daily operations

**Current after merged PR #65 (November 2026).** Source of truth: [`scripts/studio_workstation_cli.py`](../scripts/studio_workstation_cli.py), [entry point](../studio), [GPU create guard](../runtime/pr65_gpu_create.py), [one-shot expiration helper](../runtime/pr65_one_shot_guard.py). Historical PR #65 gates and earlier CLI syntax are archived; see the [documentation index](INDEX.md).

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
git switch feature/issue-43-letsencrypt-dynamic-dns
# PR #67 is draft; these commands are not yet available on main
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
| `status --stack NAME` | Reads the current EC2 instance ID, status, public IP and DCV URL |
| `check-ready --stack NAME` | Uses SSM Run Command for guest-side RES/DCV/GPU diagnostic checks; doesn't prove a human login |
| `connect --stack NAME` | For the approved CPU/GPU stack, verifies EIP ownership, DNS and system-trusted HTTPS and prints `https://dcv.kwesidavis.com:8443`; other stacks retain the raw-IP behavior |
| `connect --stack NAME --ssm` | Opens an administrative SSM Session Manager session |
| `stop --stack NAME` | Stops the resolved EC2 instance; waits for `stopped` |
| `start --stack NAME` | Starts the same instance; approved CPU waits for trusted DCV HTTPS and approved GPU rechecks RES/ACM/EIP before advertising HTTPS; other CPU stacks retain basic EC2 health wait |
| `destroy --stack NAME --delete-stack` | Approved GPU scope: verified stack, retained EBS, exact IAM and two expiration schedules cleaned in guarded order; non-anchor CPU stacks use scoped cleanup. Original shared-hostname CPU anchor is protected. Failures require operator reconciliation |

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

### Trusted DCV hostname — Issue #43 (draft PR #67)

`dcv.kwesidavis.com` resolves through the existing Route 53 A record to retained EIP `54.159.180.104`. The certificate is ACM-managed and delivered to DCV by AWCP. The user previously confirmed warning-free browser login on both the original CPU and a supervised GPU handoff. The CPU was stopped at the final live audit.

On **approved CPU and scoped GPU** stacks, ordinary `connect` now checks the expected EIP ownership tag, DNS, and system-CA-verified TLS and prints `https://dcv.kwesidavis.com:8443`; `status --hostname dcv.kwesidavis.com` also verifies TLS while running. A stopped host, DNS mismatch, untrusted certificate, or foreign EIP owner fails closed.

```bash
python3 studio workstation connect --stack studio-workstation-cpu-v013
python3 studio workstation status --stack studio-workstation-cpu-v013 --hostname dcv.kwesidavis.com
```

Run management commands from an **authorized CloudShell/administrator checkout**. The restricted guest EC2 role intentionally lacks CloudFormation discovery privileges. The end-to-end self-service CLI remains **offline-tested only until Task 6's supervised live acceptance**. Do not grant guest-role CloudFormation rights to work around this limit.

### CPU/GPU shared hostname — Task 5 draft CLI behavior

Use the **PR #67 feature branch** in an authorized management environment, not the restricted workstation guest role. The original shared-hostname CPU stack is `studio-workstation-cpu-v013`; only the exact PR #65 GPU stack/token is accepted. The GPU retains its independent time-limited termination and retained-EBS deletion safeguards.

```bash
python3 studio workstation hostname-plan
python3 studio workstation hostname-iam --gpu-instance-id i-REPLACE
python3 studio workstation hostname-return-plan --gpu-instance-id i-REPLACE
```

Normal approved CPU/GPU `start`, `stop`, and `connect` use guarded hostname ownership and verified trusted HTTPS. GPU create uses the original PR #65 guarded launcher first, then validates RES/DCV readiness, adds only exact-instance ACM access, and transfers the existing EIP. GPU stop returns that EIP to the original stopped CPU and revokes only GPU-scoped ACM access. Explicit `hostname-handoff --execute`, `hostname-return --execute`, `hostname-acm-prepare --execute`, and `hostname-acm-revoke --gpu-instance-id ... --execute` are privileged AWS-writing operations, not previews; do not run during offline testing.

**Live CLI acceptance is still pending Task 6.** An earlier supervised AWS API handoff proved trusted GPU browser login and manual resource cleanup, but did not exercise this entire self-service CLI unmocked. No claim of general GPU production support or unlimited runtime is made.

[Shared hostname security and rollback details](issue43-shared-cpu-gpu-handoff.md). The public 8443 exposure remains separate Issue #62.

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
  --client-cidr 0.0.0.0/0 --allow-public-dcv
```

**This example intentionally exposes TCP 8443 to any public IPv4 address.** `--allow-public-dcv` is the explicit opt-in; it is **not** a VPN, identity-aware gateway or a recommendation for permanent public access. Prefer a trusted public `/32` without `--allow-public-dcv` when feasible. The scoped GPU flow refuses broad public ingress unless the flag is supplied. SSH ingress remains closed.

The GPU create flow verifies the actual restricted Scheduler IAM role and group, issues an asynchronous CloudFormation `CreateStack`, identifies the tagged instance and attached root EBS within a bounded discovery period, then arms and reads back **two one-time EventBridge schedules**: EC2 termination at approximately **150 minutes** after provisioning, and an EBS deletion attempt approximately **45 minutes later**. These are **fail-safe acceptance deadlines, not an always-on workstation service**. They remain active through stop/start. The EBS deletion attempt is **best-effort** and cannot be assumed to succeed if storage remains attached. A crash between `CreateStack` and schedule creation still requires manual supervision.

After creating, check GPU readiness, enroll the password through SSM, and connect:

```bash
python3 studio workstation check-ready --stack studio-gpu-cli-accept65
python3 studio workstation connect --stack studio-gpu-cli-accept65 --ssm
# Inside SSM: sudo passwd workstation
python3 studio workstation connect --stack studio-gpu-cli-accept65
```

A newly launched GPU instance can take multiple minutes and reboot during RES startup. Readiness means `linux_vdi_config_host_ready.lock`, GDM, DCV, console ownership, Xorg and GPU OpenGL all pass; `check-ready` does not replace an actual interactive login. A TLS certificate can still be untrusted.

## Daily operation and safe cleanup (PR #67 draft)

From the **authorized management checkout**:

```bash
python3 studio workstation status --stack STACK_NAME
python3 studio workstation connect --stack STACK_NAME
python3 studio workstation stop --stack STACK_NAME
python3 studio workstation start --stack STACK_NAME
python3 studio workstation connect --stack STACK_NAME
python3 studio workstation destroy --stack STACK_NAME --delete-stack
```

For the original CPU anchor and exact scoped GPU, connect verifies `dcv.kwesidavis.com` DNS, shared EIP owner and system CA TLS before printing the trusted URL. It does not open a browser; use the Linux `workstation` account. CPU start requires CPU EIP ownership and waits for trusted HTTPS. GPU start requires the original CPU stopped and owning the EIP, and reenrolls/reverifies RES, ACM, EIP and HTTPS after EC2 health; stop waits for EC2 stopped before returning EIP and revoking GPU-only ACM permission. **The GPU's PR #65 expiration deadlines continue while stopped.** A restarted GPU may have insufficient time remaining for a safe handoff; the guard must still pass.

The `destroy --delete-stack` operation is irreversible. It **refuses the original shared-hostname CPU anchor**, which must be deliberately migrated before destruction. For the approved GPU, it independently verifies stack identity, EIP owner, expiration schedules and exact tagged resource inventory; returns EIP to the original stopped CPU; revokes GPU IAM; deletes the exact stack; verifies instance, launch template and security group removal; deletes only the independently verified detached retained EBS; and removes both schedules **only after cleanup**. Non-anchor CPU stacks use their own scoped stack and EBS cleanup. Unexpected resources, IAM denial, attached volumes or partially deleted stacks fail closed and may require operator reconciliation; do not treat CLI failure as cleanup success. Verify absence in AWS after any failed destroy.

Task 5 regressions run offline with mocked AWS interfaces. **One supervised unmocked CLI create/connect/stop/resume/destroy acceptance is Task 6 and still pending**.

## Other references

- [Quick Start](../README.md#quick-start) and [documentation index](INDEX.md)
- [GPU runtime, pinning and provenance](../runtime/README.md)
- [CPU acceptance history](cpu-acceptance-lifecycle-tls.md)
- [Architecture](visual-architecture.md) and [lifecycle diagram](workstation-lifecycle-visual.md)
- [Optional future secure access-from-anywhere](https://github.com/vision-kwest/studio-workstation/issues/62)
