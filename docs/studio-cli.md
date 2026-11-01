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
git switch main
git pull --ff-only origin main
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
| `connect --stack NAME` | Prints the current `https://PUBLIC_IP:8443` URL; doesn't launch a browser |
| `connect --stack NAME --ssm` | Opens an administrative SSM Session Manager session |
| `stop --stack NAME` | Stops the resolved EC2 instance; waits for `stopped` |
| `start --stack NAME` | Starts the same instance; waits for EC2 health, **not DCV readiness** |
| `destroy --stack NAME --delete-stack` | Permanently deletes stack-managed resources; requires a **separate EBS/Scheduler audit** |

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

### Optional trusted hostname (Issue #43, NOT DEPLOYED)

The feature branch adds an opt-in `--hostname dcv.kwesidavis.com` flag
for `connect` and `status`. It only returns the trusted HTTPS URL after
confirming DNS resolves to this stack's current public IPv4 and that a
system-trusted TLS certificate validates for the hostname. A self-signed,
expired, mismatched or unreachable certificate fails closed. This does **not**
install certificates or alter the default IP-based connection:

```bash
python3 studio workstation connect --stack STACK --hostname dcv.kwesidavis.com
python3 studio workstation status --stack STACK --hostname dcv.kwesidavis.com
```

**Do not rely on this option in `main` until a feature PR is merged.**
The dynamic-DNS Lambda, on-host renewal and permissions are not deployed.
See [Issue #43 design and live approval gates](issue43-letsencrypt-dcv.md).

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

## Daily stop, start, status and reconnect

```bash
python3 studio workstation status --stack STACK_NAME
python3 studio workstation connect --stack STACK_NAME
python3 studio workstation stop --stack STACK_NAME
python3 studio workstation start --stack STACK_NAME
python3 studio workstation check-ready --stack STACK_NAME
python3 studio workstation connect --stack STACK_NAME
```

Save documents before stopping. **Stop** ends EC2 running-instance compute charges but keeps EBS billing. A GPU shutdown during acceptance produced an NVIDIA DRM warning in the serial console and an unusually slow EC2 stop transition; the test nevertheless reached `stopped` and recovered successfully. After **start**, check the refreshed public IP, SSM readiness and DCV console; the instance's public IPv4 can change. GPU expiration schedules do **not** pause when EC2 is stopped.

## Destroy and verify cleanup

```bash
python3 studio workstation destroy --stack STACK_NAME --delete-stack
```

This is **permanent**. The CLI checks CloudFormation resource types and deletes the stack; it does **not** automatically guarantee removal of a GPU root EBS volume retained with `DeleteOnTermination=false`, nor does it automatically remove the test expiration schedules. The 2026-11-01 acceptance test's retained EBS happened to be absent at the final independent AWS audit; **do not infer that every future deletion will do the same**.

For a GPU acceptance stack, record the exact instance/volume/SG/LaunchTemplate physical IDs **before** stack deletion, then independently verify all of the following in the same account/region:

1. CloudFormation reaches `DELETE_COMPLETE`; the original EC2 instance is `terminated`.
2. The recorded retained root EBS ID is absent (or, if `available`, inspect its two exact ownership tags and delete only that verified volume).
3. The recorded security group and launch template are absent.
4. Both one-time EventBridge schedules have been checked and removed **only after** no scoped instance/volume remains. Do not delete the schedules prematurely.
5. A tag-filtered inventory finds no remaining test volumes, security groups or launch templates. Verify any recorded IDs directly as well: tags can be missing on orphaned resources.
6. Recheck unexpected orphaned EBS, snapshots and public ingress; **never** delete an unverified volume. A leftover IAM Scheduler execution role is an access-control artifact and can be reviewed for later retirement, not an active EC2 cost.

The full pass, evidence, and final absence check are recorded in [Issue #46](https://github.com/vision-kwest/studio-workstation/issues/46). No acceptance instance or public security group was left running by that test.

## Other references

- [Quick Start](../README.md#quick-start) and [documentation index](INDEX.md)
- [GPU runtime, pinning and provenance](../runtime/README.md)
- [CPU acceptance history](cpu-acceptance-lifecycle-tls.md)
- [Architecture](visual-architecture.md) and [lifecycle diagram](workstation-lifecycle-visual.md)
- [Optional future secure access-from-anywhere](https://github.com/vision-kwest/studio-workstation/issues/62)
