# Studio Workstation CLI (CPU v0.1.3 + GPU RES 2026.06)

Thin Python standard-library wrapper over the **existing** RES-derived CloudFormation
renderer and AWS CLI. It does not replace RES bootstrap or introduce a control plane.

Run from a checkout in an authorized **AWS CloudShell** environment:

```bash
cd ~/studio-workstation
python3 studio workstation --help
```

Requires Python 3, `jinja2` for the existing renderer, AWS CLI v2,
account/region access, existing `StudioWorkstationSSMRole` instance profile,
and the already-published immutable v0.1.3 S3 release.

## Create a CPU workstation

```bash
# Render and validate only; no EC2 resources created
python3 studio workstation create --stack studio-workstation-cpu-test --profile cpu

# Explicitly authorize a new, billable CloudFormation stack
python3 studio workstation create --stack studio-workstation-cpu-test \
  --profile cpu --version v0.1.3 --yes \
  --confirm-stack studio-workstation-cpu-test
```

Defaults are the **tested account-specific** us-east-1 AMI, VPC, public
subnet, `m7i.large`, 100 GiB encrypted gp3, existing SSM profile, and
`ClientCidr=0.0.0.0/0`. Verify these values for your account and region.
Override using `--image-id`, `--vpc-id`, `--subnet-id`,
`--instance-profile`, `--instance-type`, `--disk-size`, and
`--client-cidr`. Public DCV exposes the login page to the internet;
use a strong unique password. No SSH ingress is created.

The CLI deliberately **refuses to create** if the stack name already exists,
so a create command cannot silently update or replace a working EC2 instance.
It renders/validates a temporary template before deployment.

After provisioning, wait for original RES bootstrap and reboots to complete.
Check `cloud-init status --long`, DCV `console`, SSM Online, and the
RES application readiness log. Set the fresh local user password with
`sudo passwd workstation` through SSM. This enrollment is not automated.

## Create a GPU workstation (Issue #46)

The GPU profile uses validated AMI `ami-027c98f85b155c2ac` in `us-east-1`,
`g4dn.xlarge` and the exact RES 2026.06 derived archive prefix
`releases/task7-pr61-postreboot-a8ec326/res-2026.06`. This is **not**
`releases/v0.1.11/res-2026.06`. The CLI pins the two S3 object version IDs
and verifies them with read-only AWS calls before deploying. The published
SHA-256 hashes and acceptance evidence are in [runtime/README.md](../runtime/README.md).

```bash
# Offline render + CloudFormation validation; does not create EC2
python3 studio workstation create --stack studio-gpu-review --profile gpu

# Billable deployment: explicit stack confirmation required
python3 studio workstation create --stack studio-gpu-review --profile gpu \
  --yes --confirm-stack studio-gpu-review

# After RES installation and reboots, verify readiness via SSM
python3 studio workstation check-ready --stack studio-gpu-review

# Administrative password enrollment, interactive (never in user-data)
python3 studio workstation connect --stack studio-gpu-review --ssm
# Within the SSM session: sudo passwd workstation
python3 studio workstation connect --stack studio-gpu-review
```

GPU create renders a **persistent** CloudFormation instance: OS shutdown
behavior `stop`, encrypted 100 GiB gp3, root EBS retained on termination
(`DeleteOnTermination=false`), no Task 7 expiration tags or schedule.
The original RES 2026.06 installer, Supervisor, Xorg and DCV console lifecycle
are unchanged. A retained root volume may incur storage costs after stack
deletion; audit and remove it explicitly when no longer needed.

`check-ready` sends a read-only diagnostic command through SSM and checks
RES READY semaphore, GDM/DCV services, console, Xorg and Tesla T4 OpenGL.
It can fail while bootstrap is still in progress; retry after the RES setup
reboots. It does not prove an actual interactive password login.

**Security:** The CLI's existing `--client-cidr` default is public
`0.0.0.0/0`, including for GPU. For a restricted deployment supply
`--client-cidr YOUR_IP/32`. SSH remains closed. Trusted TLS and access
from changing networks are tracked in issues #43 and #62.

**Acceptance remaining:** The new persistent GPU CLI create/stop/start/destroy
workflow has only offline regression coverage. Real GPU DCV reboot persistence
was validated on the baked AMI, but EC2 **stop/start** still requires a
separately approved live acceptance test. No new GPU deployment is implied
by this documentation.

## Daily operation

```bash
python3 studio workstation status --stack studio-workstation-cpu-v013
python3 studio workstation connect --stack studio-workstation-cpu-v013
# Prints the current DCV URL; no browser is launched.

python3 studio workstation connect --stack studio-workstation-cpu-v013 --ssm
# Opens an administrative SSM shell (requires Session Manager plugin).

# Save work before stopping.
python3 studio workstation stop --stack studio-workstation-cpu-v013 \
  --yes --confirm-instance i-0bbfbdc878d3a2320

python3 studio workstation start --stack studio-workstation-cpu-v013 \
  --yes --confirm-instance i-0bbfbdc878d3a2320
```

The stop/start commands resolve the instance ID from CloudFormation and
require it to match the explicit confirmation. They wait for EC2 state/
status checks, **not** DCV desktop readiness. After start, check SSM/DCV
and retrieve the new public IP using `connect`. Stopping saves compute
charges but EBS storage continues to be billed.

## Permanently destroy a workstation

```bash
python3 studio workstation destroy --stack studio-workstation-cpu-test \
  --yes --delete-stack \
  --confirm-stack studio-workstation-cpu-test \
  --confirm-instance INSTANCE_ID
```

**Destructive:** deletes the named CloudFormation stack and its instance
and security group after checking its resource types. It never deletes
the shared release bucket, IAM profile, VPC, or Image Builder resources.
The root EBS volume uses `DeleteOnTermination=false` and can remain
unattached and billable after stack deletion. Audit and explicitly delete
obsolete EBS volumes separately; this CLI intentionally never deletes them.

## Design constraints

- No AWS writes for `status`, default `connect`, or dry-run `create`
  (the dry run renders and validates through a read-only AWS API).
- No automatic EC2 mutations; each start/stop/create/destroy needs
  explicit confirmation.
- No SSH ingress, password storage, DNS, certificate or GPU provisioning.
- `connect` prints the URL; it does not assert that DCV is READY.
- This is a first CLI implementation; test with the offline regression
  suite and review its generated CloudFormation before production use.
