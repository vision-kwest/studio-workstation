# Studio Workstation

A standalone AWS Ubuntu 24.04 workstation using **EC2**, **CloudFormation**, **Amazon DCV**, **Systems Manager**, and the adapted **AWS Research and Engineering Studio (RES) 2026.06** bootstrap. CPU and GPU workstation images are separate; the full RES control plane is not required.

![Studio Workstation architecture](docs/images/system-architecture.svg)

## Current architecture and validation (November 2026)

| Capability | Current state |
|---|---|
| CPU / GPU images | Ubuntu 24.04 + RES 2026.06 + Amazon DCV; CPU and guarded Tesla T4 GPU live-tested |
| Workstation software | Rez 3.4.0, read-only EFS production packages, pinned S3 software bundle; CPU acceptance completed |
| Publisher | On-demand EC2 profile through SSM and dedicated `StudioEfsCiPublisher` IAM/TLS EFS credentials, not a GitHub Actions runner |
| DCV address | `dcv.kwesidavis.com` → account-owned EIP `54.159.180.104`, assignable to approved workstations and safe to leave unassociated |
| GPU safeguards | Exact `studio-gpu-cli-accept65` scope, independent expiration/EBS cleanup schedules and bounded extension; not a general GPU fleet service |
| Live acceptance | Previous CPU, GPU/DCV and publisher tests passed; a full OpenUSD rebuild through the generic publisher is not yet proven |

**Current AWS inventory (2026-11-13):** All disposable EC2 instances have been terminated; the EIP is allocated but unassociated, and the original CPU anchors no longer exist. Keep `--stack` explicit when managing newly created workstations. Historical acceptance and dates are retained in linked reports.

## Quick start

Run from AWS CloudShell in the authorized account and `us-east-1`:

```bash
git clone https://github.com/vision-kwest/studio-workstation.git
cd studio-workstation
python3 -m pip install -r requirements.txt
python3 studio workstation --help
```

The repository command is **`python3 studio workstation ...`**; `studio` is a local Python entry point, not a separately installed executable. You also need AWS CLI v2, AWS permissions, and the existing AMI, release bucket, VPC/subnet and workstation instance profile.

**Preview a CPU workstation without deploying anything:**

```bash
python3 studio workstation create \
  --stack studio-workstation-cpu-review \
  --profile cpu --dry-run
```

**Create a CPU workstation** (billable; select a trusted DCV client IP):

```bash
python3 studio workstation create \
  --stack studio-workstation-cpu-review \
  --profile cpu --client-cidr YOUR_PUBLIC_IP/32
```

**GPU acceptance example:** The validated GPU path is limited to the exact PR #65 acceptance stack/token and time-bounded cleanup safeguards. Merged PR #69 adds `create --profile gpu` options `--expires-in 180m` (60–240m, default 150m) and `--volume-cleanup-delay 45m` (30–120m, default 45m). Do **not** treat this as a general production GPU launcher. See [Studio CLI — GPU workstation](docs/studio-cli.md#gpu-workstation-tested-acceptance-scope) for required parameters and cleanup limitations.

For an **existing, exact scoped GPU workstation only**, merged PR #69 supports direct extension of both independently verified expiration schedules:

```bash
python3 studio workstation extend-expiration \
  --stack studio-gpu-cli-accept65 \
  --additional-minutes 60
```

Supplying `--additional-minutes` is authorization; this command **modifies AWS schedules immediately**, with no `--execute` or preview step. The CLI requires at least 15 minutes before the existing termination deadline and limits the new termination to 80–240 minutes from the current time. [Full operating rules](docs/studio-cli.md#gpu-workstation-tested-acceptance-scope).

To inspect, connect, or stop an existing stack:

```bash
python3 studio workstation status --stack STACK_NAME
python3 studio workstation connect --stack STACK_NAME
python3 studio workstation stop --stack STACK_NAME
python3 studio workstation start --stack STACK_NAME
```

The CLI **does not require** `--yes`, `--confirm-stack`, or `--confirm-instance`. `create` performs the action unless `--dry-run` is specified. `destroy --stack STACK_NAME --delete-stack` performs guarded cleanup of approved GPU or verified CPU stacks, provided the shared EIP has first been released or assigned elsewhere. Unexpected partial failures require operator review. See [Studio CLI daily operations](docs/studio-cli.md#daily-operation-and-safe-cleanup).

After provisioning, set a unique local `workstation` password interactively via SSM (`sudo passwd workstation`); DCV uses **Linux system credentials**, not AWS console credentials. Login through the DCV URL printed by `connect`.

## Shared trusted DCV hostname — independent Elastic IP

The permanent Route 53 name `dcv.kwesidavis.com` points to the account-owned Elastic IP `54.159.180.104`. Its EIP allocation, exportable ACM certificate, DNS and IAM profiles **do not depend on keeping an old CPU EC2 instance**. No workstation is currently attached to the EIP.

The current `runtime/independent_eip.py` allows a stopped recognized owner to release the address and a running, verified Studio workstation to claim the unassociated allocation, after checking DCV/ACM/TLS readiness:

```bash
python3 studio workstation hostname-release --execute
python3 studio workstation hostname-assign --stack STACK_NAME --execute
python3 studio workstation connect --stack STACK_NAME --hostname dcv.kwesidavis.com
```

These are **live AWS operations** except `connect`. `hostname-release` is a verified no-op when already safely unassociated; `hostname-assign` requires a running workstation and proven local trusted TLS. A stopped workstation cannot provide a live DCV connection. The IP remains allocated and billable when unassociated.

For the tightly scoped GPU acceptance launcher, independent EventBridge Scheduler guards and EBS cleanup remain mandatory. See [current Studio CLI](docs/studio-cli.md), [independent EIP](docs/independent-eip.md), [publisher](docs/publisher.md), and [historical shared-hostname acceptance](docs/issue43-shared-cpu-gpu-handoff.md).

## On-demand software publisher

The merged Studio CLI exposes `publisher-create`, `publisher-start`, `publisher-status`, `publisher-run`, `publisher-result`, and `publisher-stop`. The publisher uses SSM (no DCV/EIP) and an isolated IAM profile for EFS writes. Builds run pinned, reviewed shell recipes; promotion is explicit `dev → test → prod` using `studio-rez` reference promotion.

**Security limitation:** Generic publisher recipes currently run with elevated rights and the profile can write all stages. Run only trusted recipes; the proposed untrusted-recipe sandbox and EFS anonymous-access denial require separate hardening. The previous publisher test EC2 was retired after acceptance. See [publisher runbook](docs/publisher.md).

## Security and operating costs

- No inbound SSH is needed. Use Systems Manager for maintenance and Amazon DCV for the graphical desktop.
- The CPU CLI's historical default is public TCP 8443 (`0.0.0.0/0`); **supply `--client-cidr` to restrict it**. In the scoped GPU CLI, public DCV is permitted only with explicit `--allow-public-dcv`. Public exposure is **not** a private network; prefer a trusted CIDR or future identity-aware private access for long-lived deployments.
- Treat the self-signed DCV TLS certificate as untrusted until you verify its fingerprint or install a trusted certificate. Use a strong, unique local password. Do not put passwords, private keys, tokens or NVIDIA driver binaries in the repository or CloudFormation user data.
- Stopping an instance stops running-instance compute charges, **not EBS charges**. Without an assigned Elastic IP, restarting an instance can change its public IP; the shared Studio EIP allocation persists independently of workstations. Retained GPU root EBS volumes use `DeleteOnTermination=false`.
- The tested GPU acceptance schedules were one-time safeguards (instance termination and a subsequent **best-effort** EBS deletion attempt), not a reusable production lifecycle service. The completed acceptance test's instance, volume, SG, launch template and schedules were verified removed; the scoped expiration IAM role remains.
- The GPU source AMI snapshot is unencrypted; the validated GPU test launched with an explicitly encrypted root EBS volume. Re-evaluate AMI/snapshot encryption before wider use.

## Documentation and historical reports

Start with the [documentation index](docs/INDEX.md), [Studio CLI / daily operations](docs/studio-cli.md), [GPU profile](docs/gpu-workstation-profile.md), [runtime/release provenance](runtime/README.md), and [lifecycle diagram](docs/workstation-lifecycle-visual.md). Earlier Gate 1–4, Image Builder and PR #65 preflight documents preserve historical observations rather than today's deployment instructions.

The original RES runtime sources remain pinned to [`aws/res` tag `2026.06`](https://github.com/aws/res/tree/2026.06); see [compatibility evidence](docs/RES_COMPATIBILITY.md) and [deviation register](docs/DEVIATIONS.md).
