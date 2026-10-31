# Issue #46: independent GPU CLI acceptance cleanup — offline design

**NOT DEPLOYED.** This is a dedicated test-only cleanup component, not a production
workstation lifecycle service. No AWS resources are created by importing this module.

## Scope and deployment sequence

Choose a unique stack `studio-gpu-cli-<unique>` and a random, nonsecret
12–64 character lowercase token. Render the GPU CLI with
`--cleanup-token TOKEN`; it tags the CloudFormation stack, EC2 instance and
security group with both `StudioCleanupToken` and `StudioStack`.
The cleanup reconciler tags any root EBS volume it finds while the instance
still exists, **before** requesting stack deletion.

Before CLI create, separately deploy a dedicated Lambda execution role,
the code in `scripts/gpu_cli_test_cleanup.py`, and a recurring EventBridge
Scheduler trigger with `rate(5 minutes)`. Its Lambda environment must
pin `STUDIO_TEST_STACK`, `STUDIO_CLEANUP_TOKEN`, and
`STUDIO_NOT_BEFORE_UTC` to the approved UTC deadline. Check the schedule,
Lambda ARN, environment, role and invocations before launching the CLI.
Before the deadline the handler is inert. Afterward, it retries on every
invocation until cleanup is independently verified. **Do not delete the
schedule or Lambda until verification succeeds.**

Use an exact stack-scoped CloudFormation DeleteStack permission,
resource-tag-scoped EC2 TerminateInstances/DeleteVolume/DeleteSecurityGroup/
CreateTags permissions and read-only Describe* access; Lambda CloudWatch
logging only. A separate Scheduler invoke role must be restricted to the
single Lambda ARN and source schedule. Do not reuse or broaden the Task 7
termination role. Avoid IAM wildcard write permissions except where the AWS
service requires resource-star for read-only discovery.

The cleanup sequence is conservative: if CloudFormation is in a busy
CREATE/UPDATE/DELETE state, return pending and retry; if it is deletable,
request DeleteStack; then terminate any matching stopped/running EC2,
remove available retained EBS and the matching SG. If DeleteStack fails,
retry and surface the error. Never delete a resource without the exact
scope tags. After each operation, run another invocation to confirm absence.

## Known limits — blockers before live deployment

- A single invocation is not a guarantee. The scheduler/Lambda must be
  verified independently and given sufficient retries and failure alarms.
- A stack that remains indefinitely CREATE_IN_PROGRESS or a volume that
  remains attached will require operator intervention. This code deliberately
  does not force-delete unknown CloudFormation resources.
- If EC2 is terminated before the first EBS-tagging pass, the retained
  volume can lose its provenance. **Before launch, prove that the root
  volume receives cleanup tags at creation time or establish a separate
  independent inventory of the volume ID.** The present tagging-on-first-run
  design alone does not guarantee that failure case.
- Test the exact IAM policy, Scheduler and Lambda permissions, stack
  status transitions and rollback behavior before treating this as a
  fail-closed mechanism. The offline tests exercise mocks, not AWS behavior.
- Do not permit a persistent stack to become a permanent public TCP 8443
  endpoint by accident. Explicitly approve `--client-cidr` for the test.

## Read-only CloudFormation validation in CloudShell

From a checkout of `feature/issue-46-gpu-cli`:

```bash
python3 runtime/render_workstation.py \
  --profile gpu --lifecycle persistent \
  --bucket studio-workstation-releases-401233741942-us-east-1 \
  --prefix releases/task7-pr61-postreboot-a8ec326/res-2026.06 \
  --user workstation --output /tmp/studio-gpu-cli-review.json

python3 -m json.tool /tmp/studio-gpu-cli-review.json >/dev/null
aws cloudformation validate-template --region us-east-1 \
  --template-body file:///tmp/studio-gpu-cli-review.json
```

For the **scoped test** variant, add
`--cleanup-token <TOKEN> --test-stack studio-gpu-cli-<unique>`
to the renderer invocation and validate again. This only validates the
CloudFormation template, not the correctness of future IAM/Scheduler setup.

## Offline regression tests

```bash
python3 -m unittest discover -s tests -p test_gpu_cli_test_cleanup.py -v
python3 -m unittest discover -s tests -p test_gpu_profile_contract.py -v
python3 -m unittest discover -s tests -p test_studio_workstation_cli.py -v
```
