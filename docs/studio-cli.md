# Studio Workstation CLI (CPU v0.1.3)

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
