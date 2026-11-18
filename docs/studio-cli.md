# Studio CLI: named disposable workstations

## Evidence and branch status

Issue #98 replaces the fixed PR #65 acceptance name and CPU-return hostname model.
The implementation is under review in draft PR #116 against `main`, from
`feature/issue98-unified-lifecycle`. PRs #113–#115 have merged into their respective
base branches; #116 includes their changes. Browser acceptance status, exact test
evidence and known limitations belong in the implementation
PR and Issue #98. Historical acceptance records describe older commands.

## Setup and profiles

Run in AWS CloudShell in account `401233741942`, region `us-east-1`:

```bash
git clone https://github.com/vision-kwest/studio-workstation.git
cd studio-workstation
git fetch origin feature/issue98-unified-lifecycle
git checkout feature/issue98-unified-lifecycle
python3 -m pip install -r requirements.txt
python3 studio workstation profiles
python3 studio workstation profile gpu
```

`cpu` supports `m7i.large`/`m7i.xlarge`; `gpu` supports `g4dn.xlarge` with pinned
Ubuntu 24.04/RES 2026.06 releases. `publisher` uses `m7i.large`, its dedicated
`StudioEfsCiPublisher` instance profile, existing EFS client group and SSM; it has
no desktop or trusted-hostname claim. Discovery does not start resources or fetch
live prices. The supported region, subnet and IAM profiles remain validated inputs.

## Create, list and connect

```bash
python3 studio workstation create --profile gpu --name art --dry-run
python3 studio workstation create --profile gpu --name art --expires-in 150m
python3 studio workstation create --profile cpu --name edit
python3 studio workstation list
python3 studio workstation status art
python3 studio workstation connect art
```

Omit `--name` to generate a unique `studio-PROFILE-ID` name. The workstation name is
its CloudFormation stack name, not a template name or a separate alias. `--stack`
remains an alternate selector; subsequent operations also accept the immutable
CloudFormation stack ARN. A conflicting positional name and option is rejected.
Existing names are rejected on create, never updated implicitly. Reused names get
a new native deployment identity; a stale safety stack must first be reconciled.

Create is billable unless `--dry-run` is present. The default DCV TCP/8443 CIDR is
`0.0.0.0/0` with a warning; use `--client-cidr YOUR_PUBLIC_IP/32` to restrict it.
There is no public-DCV acknowledgment flag and no SSH ingress. A restricted CIDR
must include the network from which endpoint checks/browser connections run.

Create/start do not acquire the shared hostname or certificate permissions. Every
running desktop must have a current, reachable HTTPS login page. A non-owner uses
`https://PUBLIC_IPV4:8443`, with an expected certificate/identity warning. The owner
uses `https://dcv.kwesidavis.com:8443` only after strict DNS/TLS/ownership validation.
An HTTP HTML response proves reachability, not authenticated desktop login. Browsers
or organizational policies can block bypassing certificate warnings.

Set the Linux `workstation` password privately, once per new disposable desktop:

```bash
python3 studio workstation connect art --ssm
# In the guest, interactively; never put the password in user data or a command log:
sudo passwd workstation
```

Then open the URL from `connect art` in your browser and log in as `workstation`.
SSM is administrative access, not a substitute for a working DCV login page.
Optional pinned Rez/EFS arguments remain available on desktop create. Their failure
does not turn successful DCV provisioning into a hidden inaccessible desktop.

## One disposable lifecycle

```bash
python3 studio workstation stop art
python3 studio workstation start art
python3 studio workstation check-ready art
python3 studio workstation destroy art --delete-stack
```

Stop/start preserves the same EC2 instance and attached local EBS. Destroy and
expiration delete the workstation through CloudFormation. All local EBS is created
with the instance and `DeleteOnTermination=true`; it is disposable with the instance.
Native tag propagation replaces the special volume-tagging launch template.
There is no separate disk retention interval, cleanup timer, DeleteVolume grant,
or normal-path DeleteVolume call. Durable work belongs on separately managed shared
storage. Shared EFS, EIP allocation, DNS, ACM certificate and existing profiles/SGs
are not owned by an individual workstation.

`--delete-stack` explicitly authorizes irreversible local-data deletion. Teardown
waits for native CloudFormation and checks resource absence. A surviving resource
is an error, not success and not an instruction to force-delete or silently retain.
Retry destroy with the same immutable ARN after a partial failure. Inventory shows
interrupted GPU safety-only stacks; `destroy NAME --delete-stack` reconciles them
only after exact-scope absence checks. Legacy untagged stacks are diagnostic inventory,
not automatically adopted or deleted by the new lifecycle.

### Progress during waits

Commands print flushed progress to **stderr**, including the operation, selected
scope, UTC timestamp, elapsed seconds, active stage and last observed AWS state.
A heartbeat appears every **10 seconds** while blocked, including inside native
AWS waiters, bootstrap/reboot retries, EFS helper setup and publisher SSM checks.
State observations reuse existing responses; reporting makes no extra AWS calls.
Native waiter retry limits, timeouts and expiration/cleanup safeguards are unchanged.

For example, a stop can show the following (illustrative elapsed times):

```text
[2026-11-17T21:10:00+00:00] stop scope=art elapsed=0.0s stage=Validating request last=not observed | started
[2026-11-17T21:10:01+00:00] stop scope=art elapsed=1.0s stage=Stopping EC2 last=ec2:stopping | observed
[2026-11-17T21:10:10+00:00] stop scope=art elapsed=10.0s stage=Waiting for ec2 instance-stopped last=ec2:stopping | heartbeat
[2026-11-17T21:10:20+00:00] stop scope=art elapsed=20.0s stage=Waiting for ec2 instance-stopped last=ec2:stopping | heartbeat
[2026-11-17T21:10:22+00:00] stop scope=art elapsed=22.0s stage=AWS waiter confirmed: ec2 instance-stopped last=ec2:stopping | progress
```

The command then reports EIP/certificate cleanup and finishes with total elapsed
time. `last` is an observation, not a predicted current state: native waiters do
not expose their intermediate responses. A confirmed waiter is one completed
stage; the command can still fail later. A heartbeat proves the CLI is alive,
not that AWS is advancing. No percentages or credentials are printed by the reporter.

```bash
python3 studio workstation stop art > stop.json
python3 studio workstation stop art --no-progress > stop.json
python3 studio workstation --quiet stop art > stop.json
```

Successful create/start/stop/destroy/readiness commands keep their final JSON on
stdout. `--quiet` and `--no-progress` suppress progress, while warnings and errors
remain on stderr. Dry-run previews and `connect` retain their existing text results.
Interactive `connect --ssm` suppresses progress so it does not write into the guest
terminal. Publisher build submission remains asynchronous: `command finished`
with JSON status `submitted` means the request was sent; use `result` to check the build.

## GPU expiration: the workstation, never a separate disk

`--expires-in` accepts 60–240 whole minutes (`150m` default). A native independent
CloudFormation safety stack creates a scoped Scheduler execution role and internal
identity. Users do not supply a cleanup token or manage a second deployment name.
The primary one-shot Scheduler target calls native CloudFormation `DeleteStack`.
It is verified before compute creation, then pinned to the returned immutable ARN.
A second narrowly scoped native target terminates only the verified EC2 instance
five minutes after the primary deadline if still present. This is a compute-failure
fallback, not a storage retention period. No Lambda, database or cleanup daemon.

A successful DeleteStack submission is not completed deletion. Failure keeps the
independent safety role/schedules until actual teardown is verified. One-shot
schedules remove themselves after their invocation/retries. A failed or expired
workload can leave its IAM-only safety stack for explicit `destroy` reconciliation;
there is no hidden autonomous orphan-cleanup service.

```bash
python3 studio workstation extend-expiration art --additional-minutes 60
```

An extension is an immediate authorized schedule change. It requires at least
15 minutes before the current deadline and a resulting 80–240 minutes remaining.
The fallback is moved first; both targets/roles are read back and failures are
reconciled without changing the selected deployment. Start requires 80 minutes
remaining before billing. Stop never needs healthy optional hostname infrastructure.

## Explicit trusted hostname

```bash
python3 studio workstation hostname status
python3 studio workstation hostname assign art
python3 studio workstation hostname assign edit
python3 studio workstation hostname release
```

These assignment/release commands write AWS resources immediately. Serialize them;
there is no distributed lock and AWS EIP association has no compare-and-swap API.
They preserve the existing allocation and DNS record. No additional permanent EIP,
second ENI or proxy is created. The hostname owner does not need two public addresses.

Assignment verifies the managed target, primary-only ENI topology, DCV and certificate,
rechecks ownership, moves the EIP, then verifies both the destination and former owner.
Native public IPv4 reacquisition is attempted first. Bounded previous-owner stop/start
is a fallback, with temporary interruption; a GPU must still have restart headroom.
Assignment to a running GPU needs at least five minutes of verified expiry headroom.
No deadline is extended automatically. Unsupported networking, unknown ownership or
failed recovery is an error, not a successful handoff.

Certificate renewal/export authorization is exact-session scoped to current owner
and pending destination. The existing pinned AWCP installer and activation are reused;
only after successful transfer is previous-owner export access revoked. Cached
certificates may remain on a former owner, but it has no ongoing export permission.
Stale grants from terminated owners are reconciled against actual EC2 identity.
Rollback never steals the EIP from an unexpected new owner. Stop/destroy releases
the selected owner's association; it does not choose a CPU to receive it.

## Publisher

```bash
python3 studio workstation create --profile publisher --name publish \
  --security-group-id sg-004ad23682c7db931
python3 studio workstation check-ready publish
python3 studio workstation run publish --operation check
python3 studio workstation run publish --operation build \
  --recipe-repo vision-kwest/RECIPE_REPOSITORY \
  --recipe-ref FULL_40_CHARACTER_COMMIT_SHA --recipe-path recipes/BUILD.sh
python3 studio workstation result publish --command-id COMMAND_UUID
python3 studio workstation run publish --operation promote-test --package usd --version 26.05
python3 studio workstation run publish --operation promote-prod --package usd --version 26.05
python3 studio workstation stop publish
python3 studio workstation destroy publish --delete-stack
```

The existing publisher SG is an input, not a newly created/deleted resource.
Creation reports infrastructure state; `check-ready` verifies guest bootstrap and
writable dev access before publishing. Build submits SSM work; retrieve its result.
Promotion remains explicit and uses existing studio-rez tooling, not a new publisher.
The legacy raw-instance `publisher-*` command family is retired. No live legacy
publisher existed at the preflight inventory; unexpected old instances require
explicit migration rather than name-tag-based adoption.

## Costs, acceptance and rollback

EC2 compute is charged while running; EBS and public IPv4 have separate allocation
charges, including while stopped where applicable. No dynamic pricing engine or
extra EIP is added. A shared idle EIP remains allocated deliberately. Quota/capacity
may prevent two simultaneous GPUs; that is distinct from naming support.

Offline tests exercise isolation, creation/arming failures, extension rollback,
expiry, stop/start, incomplete native deletion, hostname failures and recovery.
Live acceptance separately checks native Scheduler execution, IAM scope, actual
CPU/GPU/DCV, public-IP reacquisition, persistence and cleanup. User-authenticated
browser login is a final gate. Consult Issue #98 for observed evidence and limits.
No merge or production acceptance is implied. Roll back source by reverting the
integration; remove disposable test stacks only after verifying their exact ownership.
