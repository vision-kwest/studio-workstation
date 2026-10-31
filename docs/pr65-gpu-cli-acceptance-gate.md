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

1. Validate the **exact rendered** GPU template with original RES user-data, not a reconstructed placeholder.
2. Validate that the EC2 instance + LaunchTemplate + block-device properties are accepted at actual creation, and that root EBS is tagged at creation; `ValidateTemplate` alone is insufficient.
3. Verify least-privilege IAM effective permissions with simulation or equivalent controlled review. `cloudformation:DeleteStack` can cause downstream resource deletion; the stack must contain only approved resources.
4. Add independent Scheduler delivery-failure monitoring (e.g. DLQ and alarm) or explicitly approve a verified equivalent.
5. Confirm immutable archive SHA-256 and mitigate the preflight-to-launch race caused by unversioned RES bootstrap S3 URIs.
6. Verify IAM profile, VPC/subnet, exact operator client CIDR, alert email subscription, deadline and independent resource-ID inventory.
7. Confirm that cleanup can finish under rollback, DELETE_FAILED, stop/start, and orphaned EBS in AWS; mock tests do not prove service behavior.

**Stop here.** Task 8 is a separate approval gate. No AWS deployment, EC2 launch, PR merge or Image Builder change is authorized by this document.
