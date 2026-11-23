# Studio CLI: named workstations

## Evidence and branch status

Merged PR #116 implements Issue #98's named lifecycle, replacing the fixed PR #65
acceptance name and CPU-return hostname model. Its browser acceptance and measured
test evidence are recorded in PR #116 and Issue #98. Issue #68 changes ordinary GPU
expiration to stop EC2 while preserving its stack and disk, with user-selected
durations. Issue #125 adds automatic assignment of a free hostname. Each change's
implementation PR records its own offline/live validation.
Historical acceptance records describe older commands and test-only cleanup.
Existing deployments are not automatically converted by updating the CLI.

## Setup and profiles

Run in AWS CloudShell in account `401233741942`, region `us-east-1`:

```bash
git clone https://github.com/vision-kwest/studio-workstation.git
cd studio-workstation
git checkout main
python3 -m pip install -r requirements.txt
python3 studio workstation profiles
python3 studio workstation profile gpu
```

`cpu` supports `m7i.large`/`m7i.xlarge`; `gpu` supports `g4dn.xlarge`/`g4dn.2xlarge` with pinned
Ubuntu 24.04/RES 2026.06 releases. `publisher` uses `m7i.large`, its dedicated
`StudioEfsCiPublisher` instance profile, existing EFS client group and SSM; it has
no desktop or trusted-hostname claim. Discovery does not start resources or fetch
live prices. The supported region, subnet and IAM profiles remain validated inputs.

GPU defaults to `g4dn.xlarge`. Select `--instance-type g4dn.2xlarge` during creation
for the larger size within the same profile. Existing workstations are not resized.
Use the updated CLI for all lifecycle operations on the larger size; `start`,
`stop`, `status` and expiration commands discover its size from AWS.

## Studio environment on the desktop

After the [shared command installation](shared-applications.md#studio-shell-and-application-commands), open a terminal in the DCV desktop and run:

```bash
studio env
CODE /studio/projects
exit
```

`studio env` opens a child Bash shell with `/studio/bin` and the available production Rez environment. Uppercase `BLENDER`, `CODE`, `COMFY`, `OPENCODE` and `OLLAMA` apply the established shared settings and forward application arguments. `exit` restores the original terminal environment. On an existing desktop, `/studio/bin/studio env` or `python3 studio env` from the repository works before an administrator adds `/usr/local/bin/studio`. This requires no desktop-user sudo. The commands below remain the repository's AWS lifecycle interface.

## Create, list and connect

```bash
python3 studio workstation create --profile gpu --name art --dry-run
python3 studio workstation create --profile gpu --name art --expires-in 8h
python3 studio workstation create --profile gpu --name resolve --instance-type g4dn.2xlarge --expires-in 8h
python3 studio workstation create --profile cpu --name ayo --expires-in 4h
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
There is no public-DCV acknowledgment flag. Future CPU/GPU desktops also open
SSH TCP/22 from `0.0.0.0/0`. `--client-cidr` controls only DCV; its restricted CIDR
must include the network from which endpoint checks/browser connections run.

CPU/GPU create/start claims the shared hostname automatically when unassigned,
then prepares trusted TLS. Create/start preserves an existing association; the
shared stopped-owner cleanup releases it asynchronously after a stop. Publishers
and dry runs never claim it. Plain `connect NAME` selects the endpoint
automatically. `--hostname dcv.kwesidavis.com` requires verified ownership and trusted
TLS; it does not assign the hostname and is unnecessary for ordinary connections.
Every running desktop must have a current, reachable HTTPS login page. A non-owner uses
`https://PUBLIC_IPV4:8443`, with an expected certificate/identity warning. The owner
uses `https://dcv.kwesidavis.com:8443` only after strict DNS/TLS/ownership validation.
An HTTP HTML response proves reachability, not authenticated desktop login. Browsers
or organizational policies can block bypassing certificate warnings.

Set the Linux `workstation` password privately, once per new desktop:

```bash
python3 studio workstation connect art --ssm
# In the guest, interactively; never put the password in user data or a command log:
sudo passwd workstation
```

Then open the URL from `connect art` in your browser and log in as `workstation`.
SSM is administrative access, not a substitute for a working DCV login page.
CPU/GPU create mounts the existing EFS read/write at `/opt/studio/packages` and
exposes its user-owned `shared` directory as `/studio`. New desktop accounts use
UID/GID 2000 consistently. Files saved there survive workstation destruction.
Use `--no-studio-software` to skip Rez and the Ollama startup service while retaining shared storage. Complete explicit
`--rez-version-id`/`--rez-sha256` pairs remain compatible; partial pairs and opt-out
conflicts are rejected. Software setup completes asynchronously after DCV; a helper
failure reports incomplete software setup while the desktop remains available.

### SSH access

[Issue #70](https://github.com/vision-kwest/studio-workstation/issues/70) makes SSH
available by default on new CPU/GPU desktops. After DCV readiness, the existing
setup service configures and enables Ubuntu's native `ssh.service` on each boot,
before attempting the EFS mount. This also applies with `--no-studio-software`.
The service permits only the UID 2000 desktop account (`workstation` in CLI creates),
requires public-key authentication, disables password, keyboard-interactive and
root login, and permits TCP forwarding with `GatewayPorts no`.

SSHD retains the local `.ssh/authorized_keys` and `.ssh/authorized_keys2` files
and also reads the local `.ssh/authorized_keys.studio`. After EFS is ready, setup
copies `/studio/config/ssh/authorized_keys` into that separate local file. Seed
the shared source once with the approved **public** key so replacement desktops
can accept the same client. The private key stays on the Mac. Keep the local key
path owned by root or the desktop account and not writable by group or others.
SSHD does not read through the shared mount: its existing root ownership/mode is
incompatible with OpenSSH `StrictModes` and remains unchanged.

Connect from the Mac using the current public IP shown by `status`, or the stable
hostname only when that workstation owns it:

```bash
ssh workstation@PUBLIC_IPV4
```

Use the existing SSH agent or `-i` with your local private-key path. A fresh
workstation needs its first successful EFS copy before the shared key works;
copied keys then survive EFS outages. Setup/reboot refreshes the copy when EFS is
available. To revoke a key, remove its line from the shared source, refresh setup
on each workstation, and remove any duplicate from the other local authorized-key
files. Editing EFS alone does not revoke existing local copies. SSM remains the
administrative recovery path.

Updating source changes future creates, not existing instance security groups or
their installed setup scripts. The publisher keeps its existing supplied security
group and SSM access. Native SSH login, forwarding and reboot persistence must be
verified on the deployed change; a listening port alone is not authentication proof.

### Desktop administrator access

New CPU/GPU workstations add the selected desktop account to Ubuntu's `sudo` group.
Commands such as `sudo apt-get install ripgrep` use the same Linux password as DCV
login. Initial password enrollment remains required; AWS credentials are separate.

For an existing running workstation, open its administrative SSM session:

```bash
python3 studio workstation connect NAME --ssm
```

In that guest session, run the following (substitute a custom desktop username if used):

```bash
sudo usermod -aG sudo workstation
sudo -l -U workstation
```

Log out of the GNOME desktop and log back in to refresh group membership;
disconnecting the DCV viewer alone keeps the existing desktop session. In the
fresh desktop terminal, `sudo -k` followed by `sudo -v` checks your password, and
`sudo id -u` should print `0`. Updating the repository or restarting an existing
workstation does not rerun initial account setup. Roll back only this grant from
the administrative SSM session with `sudo gpasswd -d workstation sudo`, then log
in again. Publisher accounts and shared EFS permissions are unchanged.

### Ubuntu update prompts

New CPU/GPU desktop accounts disable Ubuntu's automatic update-notifier windows,
including reboot notices, and its separate release-upgrade notification service.
These local user preferences are installed before the first desktop session.
Manual Software Updater/APT and background security updates keep their existing
behavior. Suppressing dialogs does not prevent unattended kernel updates or repair
an existing NVIDIA/kernel mismatch. Publisher instances have no desktop setting.

For an existing workstation, run this in its terminal as `workstation`, not in
CloudShell. From an administrative SSM shell, first run `sudo -iu workstation`:

```bash
mkdir -p "$HOME/.config/autostart" "$HOME/.config/systemd/user"
cat > "$HOME/.config/autostart/update-notifier.desktop" <<'DESKTOP'
[Desktop Entry]
Type=Application
Name=Update Notifier
Exec=update-notifier
Hidden=true
DESKTOP
ln -sf /dev/null "$HOME/.config/systemd/user/update-notifier-release.path"
ln -sf /dev/null "$HOME/.config/systemd/user/update-notifier-release.service"
```

For existing accounts, the next normal workstation stop/start reloads both GNOME
and the systemd user manager. Disconnecting the DCV viewer keeps the session alive.
To restore the prompts, remove only the three files above before the next boot.
New-account setup does not reapply them on later boots.

## Stop, restart and explicit destruction

```bash
python3 studio workstation stop art
python3 studio workstation start art --expires-in 1d
python3 studio workstation check-ready art
python3 studio workstation destroy art --delete-stack
```

Manual stop and expiration preserve the same EC2 instance, CloudFormation
stack and attached local EBS. Timed workstation start verifies and rearms a fresh stop timer
before starting compute; `--expires-in` can select a new duration. Saved EBS files
survive stopping, while unsaved RAM and instance-store data do not.

Explicit destroy deletes the workstation through CloudFormation. Local EBS uses
`DeleteOnTermination=true`: it is removed on termination, not on stop. There is no
separate disk retention interval, disk-cleanup timer or normal-path DeleteVolume
call. Shared EFS, EIP allocation, DNS, ACM certificate and existing profiles/SGs
are independent of an individual workstation.

`--delete-stack` explicitly authorizes irreversible local-data deletion. Teardown
waits for native CloudFormation and checks resource absence. A surviving resource
is an error, not success and not an instruction to force-delete or silently retain.
Retry destroy with the same immutable ARN after a partial failure. Inventory shows
interrupted safety-only stacks; `destroy NAME --delete-stack` reconciles them
only after exact-scope absence checks. Legacy untagged stacks are diagnostic inventory,
not automatically adopted or deleted by the new lifecycle.

### Failed initial creation

When initial CloudFormation creation fails, the CLI reports the resource's AWS
failure reason and observed stack state. Native rollback can delete the incomplete
workstation while leaving its separate expiration-role stack. After confirming
`DELETE_COMPLETE` and no remaining workstation, use `destroy NAME --delete-stack`
to remove those safeguards before reusing the name. This explicit cleanup checks
resource ownership and absence; it does not run automatically on a create error.

GPU sizes share the regional **Running On-Demand G and VT instances** vCPU quota.
A running `g4dn.xlarge` uses 4 vCPUs and `g4dn.2xlarge` needs 8, so running both
requires at least 12. Free sufficient quota by stopping an unused GPU workstation,
or request a quota increase before retrying. Optional failure diagnostics query
CloudFormation events after the existing ownership-checked stop attempt.

### Progress during waits

Commands print flushed progress to **stderr**, including the operation, selected
scope, UTC timestamp, elapsed seconds, active stage and last observed AWS state.
A heartbeat appears every **10 seconds** while blocked, including inside native
AWS waiters, bootstrap/reboot retries, EFS helper setup and publisher SSM checks.
State observations reuse existing responses; reporting makes no extra AWS calls.
Progress reporting does not alter AWS operations or their retry limits.

For example, a stop can show the following (illustrative elapsed times):

```text
[2026-11-17T21:10:00+00:00] stop scope=art elapsed=0.0s stage=Validating request last=not observed last_age=unknown | started
[2026-11-17T21:10:01+00:00] stop scope=art elapsed=1.0s stage=Stopping EC2 last=ec2:stopping last_age=0.0s | observed
[2026-11-17T21:10:10+00:00] stop scope=art elapsed=10.0s stage=Waiting for ec2 instance-stopped last=ec2:stopping last_age=9.0s | heartbeat
[2026-11-17T21:10:20+00:00] stop scope=art elapsed=20.0s stage=Waiting for ec2 instance-stopped last=ec2:stopping last_age=19.0s | heartbeat
[2026-11-17T21:10:22+00:00] stop scope=art elapsed=22.0s stage=AWS waiter confirmed: ec2 instance-stopped last=ec2:stopping last_age=21.0s | progress
```

The command then reports EIP/certificate cleanup and finishes with total elapsed
time. `last` is an observation, not a predicted current state: native waiters do
not expose their intermediate responses. A confirmed waiter is one completed
stage; the command can still fail later. `last_age` shows time since that AWS state
was observed, including repeated observations of the same state. Heartbeats alone
do not refresh it. A heartbeat proves the CLI is alive, not that AWS is advancing.

Readiness retries include the attempt number and the existing 15-second retry
delay. `last_check` retains the last reported checkpoint and result during the
next wait/probe. Separate fixed labels identify the RES marker, GDM/DCV services,
DCV console, NVIDIA T4, Xorg and NVIDIA OpenGL. For example:

```text
[2026-11-21T21:26:28+00:00] start scope=art elapsed=1820.0s stage=Waiting to retry readiness after attempt 70/96 (15s delay) last=ssm:Failed last_age=10.0s last_check=Failed: NVIDIA T4 unchanged_for=480.0s checked_age=10.0s | heartbeat
```

`checked_age` measures time since the readiness observation was recorded;
`unchanged_for` measures time since that same result was first observed. A repeat
refreshes only `checked_age`. Neither field proves continuous guest failure or
estimates completion. The checkpoint names the last check started by that probe,
not the underlying OS cause. Missing checkpoint output is reported as `not reported`;
SSM unavailability and local polling timeout have their own fixed descriptions.
A successful probe clears the previous failure. Labels come only from exact
known markers, never arbitrary guest stdout/stderr; a terminal SSM failure
includes the instance and command IDs for inspecting detailed output in SSM.
No additional AWS calls, percentages or credentials are introduced by reporting.

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

## Automatic stop for every profile

`--expires-in` accepts a positive whole duration with `m`, `h` or `d` units,
such as `150m`, `8h` or `1d`, with CPU, GPU, and publisher profiles. GPU create defaults to `150m`. There is no
four-hour ceiling; the duration must produce a valid future AWS schedule date.
CPU and publisher create opt in with `--expires-in`; omitting it keeps their manual stop lifecycle.
Publisher still requires its existing `--security-group-id` and dedicated instance profile.

One native EventBridge Scheduler resource belongs to the workstation's
CloudFormation stack and invokes `StopInstances` for its exact instance.
Its execution role permits scoped stopping, without stack deletion or instance
termination rights. CloudFormation provisions the timer with the workstation;
the timer remains after firing so it can be rearmed. No Lambda, database,
second termination timer or cleanup daemon is required.

The requested duration starts after CloudFormation creates the infrastructure
and includes desktop bootstrap time. During creation, the same schedule has a
temporary ten-minute stop deadline; the CLI replaces and verifies that deadline
once the stack is ready. An interrupted CLI therefore leaves a bounded stop
timer in place. Initial CloudFormation creation has a five-minute timeout and
normal failed-create rollback, which can remove incomplete new resources.

For every timed profile, `start` updates and independently verifies a fresh deadline before starting
EC2. Without an override, it reuses the duration selected at the most recent
create/start. A failed timer verification prevents the start. An expired timer
and a stopped instance are normal states; local saved work remains available.

```bash
python3 studio workstation status art
python3 studio workstation extend-expiration art --additional-minutes 480
```

An extension immediately moves the current deadline by a positive whole number
of minutes and verifies the result. It does not change the duration remembered
for later starts. There is no 180-minute addition cap, 80–240-minute result window
or final-15-minute blackout.

A very late update can race with an invocation already dispatched by AWS.
Schedule readback cannot retract an in-flight stop. If the workstation is already
stopping, let it finish and start it again with a fresh timer; its EBS files remain.
Automatic stopping ends compute use, but retained EBS and applicable public IPv4
charges continue. Stop never needs healthy optional hostname infrastructure.

### Existing workstations

An existing untimed CPU or publisher remains untimed. `start --expires-in` cannot
add a timer to it; create a new workstation with `--expires-in` to opt in.

Updating the CLI does not disable, convert or re-enable old external destructive
timers. `status` identifies a legacy deployment instead of presenting it as a
verified auto-stop workstation. `start` and `extend-expiration` require the new
timer model and reject legacy deployments without changing their schedules.
`list`, `connect`, `stop` and explicit `destroy` remain available.

Review an existing deployment's timers before using it: old enabled timers can
still destroy a manually stopped workstation. If those timers were deliberately
disabled, they remain disabled and provide no automatic cost cutoff. Conversion
is a separate deployment operation tracked in Issue #68; there is no implicit
migration of a running workstation or its active installation.

## Explicit trusted hostname

```bash
python3 studio workstation hostname status
python3 studio workstation hostname assign art
python3 studio workstation connect art
python3 studio workstation hostname release
```

Use `hostname assign NAME` to intentionally transfer an occupied hostname or retry
after an automatic-setup warning. Create/start explains whether it claimed the
hostname or preserved its current owner. A failed automatic attempt prints a
warning and retry command, then rechecks the current endpoint: a working public-IP
desktop remains usable; failed endpoint recovery is still an error.

These assignment/release commands write AWS resources immediately. Serialize them;
there is no distributed lock and AWS EIP association has no compare-and-swap API.
They preserve the existing allocation and DNS record. No additional permanent EIP,
second ENI or proxy is created. The hostname owner does not need two public addresses.

Explicit assignment verifies the managed target, primary-only ENI topology, DCV and certificate,
rechecks ownership, moves the EIP, then verifies both the destination and former owner.
Native public IPv4 reacquisition is attempted first. Bounded previous-owner stop/start
is a fallback, with temporary interruption; a timed desktop rearms and verifies its stop
timer before restarting. Assignment does not impose the old acceptance-test
headroom limits. Unsupported networking, unknown ownership or failed recovery
is an error, not a successful handoff.

Certificate renewal/export authorization uses exact-session grants for the current
owner and pending destination. The existing pinned AWCP installer and activation
are reused; a completed CLI transfer or manual release revokes previous-owner export
access. Cached certificates may remain on a former owner after that cleanup, but
it has no ongoing export permission.
Stale grants from terminated owners are reconciled against actual EC2 identity.
Rollback never steals the EIP from an unexpected new owner. CLI stop/destroy releases
the selected owner's association; it does not choose a CPU to receive it. With the
[shared cleanup stack](independent-eip.md#automatic-release-after-stopping) deployed,
native automatic stop also releases the address once its owner is verified stopped.
Events trigger cleanup, with a one-minute reconciliation fallback; release is
asynchronous. Automatic release retains the stopped former owner's exact-session
certificate grant and metadata until the next CLI claim reconciles them before
assigning the free address. Start claims a free hostname;
explicit assignment remains available for an intentional transfer or retry.

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

Offline tests exercise isolation, creation/arming failures, verified extensions,
automatic stop/rearm, incomplete native deletion, hostname failures and recovery.
Live acceptance separately checks native Scheduler execution, IAM scope, actual
CPU/GPU/DCV, public-IP reacquisition, persistence and cleanup. User-authenticated
browser login is a final gate. Consult Issues #68, #98 and #125 for observed evidence
and limits. Roll back source by reverting the affected change; source rollback does
not migrate deployed timers. Destructive live-test
safeguards may be created separately for explicitly selected disposable test resources;
they are not a mode or default of the ordinary Studio CLI.
