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
| `start --stack NAME` | Starts the same instance; approved GPU **first** checks its exact PR #65 Scheduler/IAM safeguards and requires at least 80 minutes remaining before EC2 start, then checks RES/ACM/EIP and trusted HTTPS; approved CPU waits for trusted HTTPS |
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

`dcv.kwesidavis.com` resolves through the existing Route 53 A record to retained EIP `54.159.180.104`. The certificate is ACM-managed and delivered to DCV by AWCP. The approved CPU and exact PR #65 GPU both achieved warning-free, user-confirmed Ubuntu GNOME browser login during the supervised Task 6 acceptance.

For the approved CPU and scoped GPU, normal `connect` checks EIP ownership tags, DNS, and system-trusted TLS and prints `https://dcv.kwesidavis.com:8443`. `status --hostname dcv.kwesidavis.com` also verifies HTTPS while the workstation is running. Stopped hosts, unexpected EIP owners, invalid DNS and untrusted certificates fail closed. The original CPU was returned to the stopped state with the EIP after final GPU cleanup.

```bash
python3 studio workstation connect --stack studio-workstation-cpu-v013
python3 studio workstation status --stack studio-workstation-cpu-v013 --hostname dcv.kwesidavis.com
```

Use the **PR #67 feature branch** from an authorized management environment (CloudShell/admin), not the restricted guest EC2 role. The guest role intentionally lacks CloudFormation discovery privileges; do not widen its permissions for management CLI access.

### CPU/GPU shared hostname — guarded CLI behavior

The exact accepted GPU scope is `studio-gpu-cli-accept65` with the pinned PR #65 cleanup token, immutable RES archives, and two independently verified expiry schedules. One at a time, the original stopped CPU or validated GPU may own the shared EIP. No automatic expiry extension is permitted.

```bash
# Read-only preflight/proposals
python3 studio workstation hostname-plan
python3 studio workstation hostname-iam --gpu-instance-id i-REPLACE
python3 studio workstation hostname-return-plan --gpu-instance-id i-REPLACE
```

`hostname-acm-prepare --execute`, `hostname-handoff --execute`, `hostname-return --gpu-instance-id ... --execute`, and `hostname-acm-revoke --gpu-instance-id ... --execute` are **privileged AWS-writing operations**, not previews. Normal GPU create/start/stop paths integrate the exact-instance certificate enrollment, guarded EIP transfer/return and IAM revocation. The supervised Task 6 test completed all of those except successful *automatic* EIP reacquisition after a late GPU restart.

**Task 6 acceptance and limitation:** CPU CLI start/status/connect/stop, GPU guarded create, manual guarded handoff, certificate setup, CPU/GPU browser logins, GPU stop/start, saved-file survival, and final scoped GPU destroy with an independent AWS absence audit all passed. During Task 6, a late GPU restart succeeded at EC2 and RES/T4 readiness, but the hostname handoff then refused because the expiration deadline was too near. **The draft CLI now checks the exact GPU identity, both PR #65 schedules, Scheduler IAM scope, tagged EBS, and at least 80 minutes of remaining time before `StartInstances`.** It fails without starting EC2 when the guard is missing, mismatched, expired, or too close (60-minute handoff safety margin plus 20-minute startup reserve). This fix passed offline tests but has **not** been live retested; configurable expiration remains a separate future item in [Issue #68](https://github.com/vision-kwest/studio-workstation/issues/68). The acceptance guard cannot simply be disabled or extended by current CLI commands. Recovery from partial deletion, unexpected schedule expiry, or mid-setup interruption may still need a supervised operator. **PR #67 is draft/unmerged and is not a general-purpose production GPU launcher.**

[Shared hostname safeguards and rollback](issue43-shared-cpu-gpu-handoff.md). Public TCP 8443 remains a separate security-hardening issue ([#62](https://github.com/vision-kwest/studio-workstation/issues/62)).

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

For the original CPU anchor and exact scoped GPU, connect verifies `dcv.kwesidavis.com` DNS, shared EIP owner and system CA TLS before printing the trusted URL. It does not open a browser; use the Linux `workstation` account. CPU start requires CPU EIP ownership and waits for trusted HTTPS. GPU start requires the original CPU stopped and owning the EIP, and reenrolls/reverifies RES, ACM, EIP and HTTPS after EC2 health; stop waits for EC2 stopped before returning EIP and revoking GPU-only ACM permission. **The GPU's PR #65 expiration deadlines continue while stopped.** If fewer than 80 minutes remain, or the original independent schedules/IAM/ownership checks fail, the CLI refuses a GPU restart **before** EC2 starts. Do not bypass or extend the schedules to restart this time-limited acceptance GPU; use the guarded cleanup path.

The `destroy --delete-stack` operation is irreversible. It **refuses the original shared-hostname CPU anchor**, which must be deliberately migrated before destruction. For the approved GPU, it independently verifies stack identity, EIP owner, expiration schedules and exact tagged resource inventory; returns EIP to the original stopped CPU; revokes GPU IAM; deletes the exact stack; verifies instance, launch template and security group removal; deletes only the independently verified detached retained EBS; and removes both schedules **only after cleanup**. Non-anchor CPU stacks use their own scoped stack and EBS cleanup. Unexpected resources, IAM denial, attached volumes or partially deleted stacks fail closed and may require operator reconciliation; do not treat CLI failure as cleanup success. Verify absence in AWS after any failed destroy.

Task 5 regressions used mocked AWS interfaces; the supervised Task 6 live acceptance subsequently passed scoped CLI create/connect/stop/destroy and preserved the GPU test file through stop/start. The pre-start 80-minute rejection above is **offline verified only**, not a claim of a second live GPU acceptance.

## Other references

- [Quick Start](../README.md#quick-start) and [documentation index](INDEX.md)
- [GPU runtime, pinning and provenance](../runtime/README.md)
- [CPU acceptance history](cpu-acceptance-lifecycle-tls.md)
- [Architecture](visual-architecture.md) and [lifecycle diagram](workstation-lifecycle-visual.md)
- [Optional future secure access-from-anywhere](https://github.com/vision-kwest/studio-workstation/issues/62)
