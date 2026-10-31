# PR #65 — GPU CLI controlled acceptance: final predeployment gate

**Status: NO-GO. Offline/read-only only.** This runbook is not authorization to deploy. The draft PR must remain unmerged until separately approved.

## Pinned inputs (us-east-1)

- GPU AMI: `ami-027c98f85b155c2ac`, owner `401233741942`, type `g4dn.xlarge`
- Release bucket: `studio-workstation-releases-401233741942-us-east-1`
- Prefix: `releases/task7-pr61-postreboot-a8ec326/res-2026.06`
- Installation scripts version: `dlOKXdRAEJqpeNZNBotj87q0bdNlI5VC`
- Virtual desktop version: `nzyYrLhYAQ.OTFNpFE_xSHPaK06T1V53`
- Workstation user: `workstation`; root disk: encrypted gp3, 100 GiB; `DeleteOnTermination=false`; OS shutdown stops the instance
- Acceptance stack: unique `studio-gpu-cli-<suffix>` satisfying the guard's maximum 25-character suffix; cleanup token: unique random lowercase 12–64 characters
- DCV ingress: explicitly approved **restricted** IPv4 client CIDR, preferably a /32, TCP 8443 only. Public `0.0.0.0/0` is forbidden for scoped acceptance. SSH remains closed.

## Independent safeguard — deploy and verify before workstation

The independent guard is a **separate** CloudFormation stack defined by `infra/gpu-cli-test-cleanup.json`. It schedules the Lambda every five minutes, with a UTC deadline, scoped IAM, an error alarm and SNS notification. Deploying it requires a versioned Lambda ZIP and `CAPABILITY_NAMED_IAM` or equivalent required capability as returned by AWS validation. Verify Scheduler enabled, Lambda invocation, SNS subscription confirmation, IAM trust and exact scope, alarm and delivery failure monitoring. Do not deploy the workstation if any guard component is unverified. Do not remove the guard until an independent post-cleanup inventory is empty.

## Read-only verification already performed

AWS `DescribeImages` confirmed the pinned AMI available with expected owner. Four S3 `HeadObject` checks confirmed both versioned objects and both latest keys point to pinned versions. AWS `ValidateTemplate` passed for the exact guard JSON and a **reconstructed workstation structure with placeholder user-data**. The latter does **not** validate the actual GPU renderer output.

GitHub Actions [Studio CLI tests #41251399545](https://github.com/vision-kwest/studio-workstation/actions/runs/41251399545) passed 64 offline tests (20 CLI, 8 GPU renderer, 28 cleanup, 8 guard). All other observed workflows for commit `a84f84c528c42cac927d1f3e052e86e74797708a` concluded success.

## Exact workstation render and read-only CloudFormation ValidateTemplate

Use a checkout of the reviewed PR head and a non-production local test token. This does not create resources.

```bash
set -euo pipefail
python3 runtime/render_workstation.py \
  --profile gpu --lifecycle persistent \
  --bucket studio-workstation-releases-401233741942-us-east-1 \
  --prefix releases/task7-pr61-postreboot-a8ec326/res-2026.06 \
  --user workstation \
  --cleanup-token unique-review-token-123 \
  --test-stack studio-gpu-cli-review1 \
  --output /tmp/studio-gpu-cli-review.json
python3 -m json.tool /tmp/studio-gpu-cli-review.json >/dev/null
aws cloudformation validate-template --region us-east-1 \
  --template-body file:///tmp/studio-gpu-cli-review.json
aws cloudformation validate-template --region us-east-1 \
  --template-body file://infra/gpu-cli-test-cleanup.json
```

The guard's `ValidateTemplate` output must include IAM capability requirements. These commands validate templates, **not** EC2 runtime compatibility, IAM effective permissions, or the release URI race.

## Exact CLI launch-input review — NOT a deployment command

For the acceptance test, choose a **new** stack and token, explicitly approve the client CIDR, and run the CLI **without** `--yes`:

```bash
python3 studio workstation create \
  --stack studio-gpu-cli-review1 --profile gpu \
  --cleanup-token unique-review-token-123 \
  --client-cidr 127.0.0.1/32
```

The CLI's dry-run validates the template through AWS and prints deployment inputs but does not deploy. The example loopback CIDR is only a safety placeholder; it is not a usable remote DCV client address. Never use `--yes` until a separate approval and verified guard deployment.

## Explicit NO-GO blockers

1. **RESOLVED 2026-10-31:** AWS ValidateTemplate passed for the exact rendered scoped GPU template including original RES user-data. This is a syntax/structure validation, not a runtime launch guarantee.
2. Validate that the EC2 instance + LaunchTemplate + block-device properties are accepted at actual creation, and that root EBS is tagged at creation; `ValidateTemplate` alone is insufficient.
3. Verify least-privilege IAM effective permissions with simulation or equivalent controlled review. `cloudformation:DeleteStack` can cause downstream resource deletion; the stack must contain only approved resources.
4. Add independent Scheduler delivery-failure monitoring (e.g. DLQ and alarm) or explicitly approve a verified equivalent.
5. Confirm immutable archive SHA-256 and mitigate the preflight-to-launch race caused by unversioned RES bootstrap S3 URIs.
6. Verify IAM profile, VPC/subnet, exact operator client CIDR, alert email subscription, deadline and independent resource-ID inventory.
7. Confirm that cleanup can finish under rollback, DELETE_FAILED, stop/start, and orphaned EBS in AWS; mock tests do not prove service behavior.

**Stop here.** Task 8 is a separate approval gate. No AWS deployment, EC2 launch, PR merge or Image Builder change is authorized by this document.

## Blocker-resolution phase — Task 1 evidence (2026-10-31)

Executed `runtime/render_workstation.py` in GitHub Actions [run #41251808985](https://github.com/vision-kwest/studio-workstation/actions/runs/41251808985) with `--profile gpu --lifecycle persistent --cleanup-token unique-review-token-123 --test-stack studio-gpu-cli-review1`, the pinned release bucket/prefix and local user `workstation`. The exact generated JSON was transferred from the CI output to AWS CloudFormation `ValidateTemplate` in `us-east-1` without changing its content. **AWS returned success** and recognized seven template parameters, with `InstanceType=g4dn.xlarge` and `ClientCidr=127.0.0.1/32` defaults.

Verified rendered resources: `Workstation`, `WorkstationSG`, `CleanupVolumeTagLaunchTemplate`. User-data contains 5,036 characters from the RES template. Root EBS has encrypted gp3, `DeleteOnTermination=false`, default 100 GiB; instance shutdown is `stop`. Launch Template volume `TagSpecifications` include exact test stack/token tags. SG ingress is TCP 8443 via `ClientCidr`. No EC2 instance or CloudFormation stack was created. CI now retains offline exact rendering and JSON parsing without dumping full template contents to logs.

**Still NO-GO:** AWS ValidateTemplate does not prove EC2 launch-time behavior or effective IAM authorization. Continue with IAM simulation, Scheduler delivery-failure monitoring, archive integrity and EBS ownership checks, each behind separate approval.

## Blocker-resolution phase — Task 2 IAM policy simulation (2026-10-31)

Executed read-only AWS IAM `SimulateCustomPolicy` on the proposed cleanup Lambda's resolved inline policy, using account `401233741942`, region `us-east-1`, example stack `studio-gpu-cli-test1` and a synthetic token. **15/15 expected decisions:** five correctly scoped operations allowed; wrong-region, wrong-stack, or missing-resource-tag variants implicitly denied. Actions tested: `cloudformation:DeleteStack`, `ec2:TerminateInstances`, `ec2:DeleteVolume`, `ec2:DeleteSecurityGroup`, and `ec2:DeleteLaunchTemplate`. Simulated Scheduler inline invoke policy: **3/3 expected decisions**, allowing the exact Lambda ARN and denying another function or region. These tests did not create IAM roles or resources. Added negative offline policy-contract tests.

**Limits:** `SimulateCustomPolicy` does not simulate an actual deployed principal, CloudFormation's downstream stack-resource deletion, service trust-role assumption, permission boundaries, SCPs, or the complete lifecycle of a Lambda/Scheduler deployment. IAM tag conditions rely on tags being present and correct. The policy intentionally allows read-only inventory discovery with `Resource: *`. Effective permissions must be rechecked when roles exist. Continue **NO-GO** until remaining independent delivery monitoring, archive integrity and EBS ownership blockers are resolved.

## Blocker-resolution phase — Task 3 Scheduler delivery monitoring (offline)

The guard CloudFormation template now defines a dedicated SQS dead-letter queue with SQS-managed encryption and 14-day message retention, attached to `AWS::Scheduler::Schedule.Target.DeadLetterConfig`. Its Scheduler execution role can send messages only to that queue, while retaining permission to invoke only the guard Lambda. A queue-depth alarm and a Scheduler `TargetErrorCount` alarm notify the existing SNS topic. These are independent of the Lambda Errors alarm. This is a *proposed* configuration, not deployed infrastructure.

**Before deployment:** verify the exact `AWS/Scheduler` metric dimensions and alarm signal against AWS documentation and live telemetry; queue alarms do not prove scheduler delivery when no DLQ messages exist. Confirm SNS subscription, queue access policy/service behavior, and run a controlled failure-injection test. Preserve the queue and its contents until all failures are investigated. Ensure alarm coverage and the guard's continued operation through workstation stop/start and CloudFormation failure states. The guard template must pass AWS read-only `ValidateTemplate`; this alone cannot prove Scheduler delivery or Lambda execution.
