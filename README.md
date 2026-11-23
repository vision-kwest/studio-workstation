# Studio Workstation

Named Ubuntu 24.04 workstations on AWS: CPU, NVIDIA GPU and an isolated
software publisher. Uses CloudFormation, EC2, Amazon DCV, Systems Manager and the
pinned RES 2026.06 standalone bootstrap—not the full RES control plane.

## Current development status

Merged PR #116 integrates multiple named workstations, shared hostname ownership,
and one native compute/disk lifecycle. Issue #68 changes ordinary GPU expiration
to automatic stop, preserving the stack and disk, with user-selected durations.
Issue #125 adds automatic assignment of an available hostname. See the implementation
PRs for measured offline and live evidence; source checks are not authenticated
browser acceptance. Updating the CLI does not
convert existing destructive timers; see the [legacy workstation note](docs/studio-cli.md#existing-workstations).

## Quick start

```bash
git clone https://github.com/vision-kwest/studio-workstation.git
cd studio-workstation
git checkout main
python3 -m pip install -r requirements.txt
python3 studio workstation profiles
python3 studio workstation create --profile gpu --name art --dry-run
python3 studio workstation create --profile gpu --name art --expires-in 8h
python3 studio workstation list
python3 studio workstation connect art
```

Use the authorized account and `us-east-1`. Create is billable without `--dry-run`.
Omit `--name` for a generated name. No user cleanup token or disk-cleanup delay.
CPU/GPU create/start automatically uses `dcv.kwesidavis.com` when it is unassigned.
An existing owner keeps it, even if stopped. `connect art` automatically returns
the verified trusted hostname for its owner, or the current public-IP URL otherwise;
you do not need `--hostname`. Publishers and dry runs do not claim the hostname.
CPU/GPU creates provide writable shared EFS at `/studio` and optional Rez setup.
All profiles accept `--expires-in` and stop the instance while preserving saved local files.
For example: `python3 studio workstation create --profile cpu --name ayo --expires-in 4h`.
Choose a positive whole duration in minutes, hours or days. GPU defaults to `150m`;
CPU and publisher remain untimed when the option is omitted.
Use `--no-studio-software` to skip Rez and the Ollama startup service; shared storage still follows DCV setup.
The normal `workstation` account can install and save files under `/studio` without sudo.
Shared application launchers use MenuLibre (**Menu Editor**); see the
[shared application guide](docs/shared-applications.md) for setup, GUI saves and refresh.
Default DCV TCP/8443 is public, with a warning; restrict it using
`--client-cidr YOUR_PUBLIC_IP/32`. Future CPU/GPU desktops also expose SSH TCP/22
from `0.0.0.0/0`, using public-key authentication for `workstation`; that CIDR
option restricts only DCV. Run CLI endpoint checks from a network allowed by the
DCV CIDR. See [SSH access](docs/studio-cli.md#ssh-access) for the shared public-key
source, local copies and refresh timing. DCV login remains independent of SSH.

New desktops need a unique Linux password set interactively:

```bash
python3 studio workstation connect art --ssm
sudo passwd workstation
```

Open the URL from `connect art` and log in as `workstation`, not with AWS credentials.
New CPU/GPU desktop accounts can use `sudo` with that same Linux password for
system administration. See [administrator access](docs/studio-cli.md#desktop-administrator-access)
for the one-time change on an existing workstation.
Raw-IP HTTPS has an expected certificate warning; unreachable DCV is not success.

To inspect ownership, intentionally transfer the hostname, or retry a failed
automatic assignment:

```bash
python3 studio workstation hostname status
python3 studio workstation hostname assign art
python3 studio workstation connect art
```

Assignment intentionally transfers the hostname from its current Studio owner. Automatic setup
failures print a warning and retry command; a checked public-IP endpoint remains
usable. If endpoint recovery fails, the CLI reports failure.

## Studio application shell

After the [one-time shared command installation](docs/shared-applications.md#studio-shell-and-application-commands), run `studio env` in a desktop terminal. The child shell exposes `BLENDER`, `CODE`, `COMFY`, `OPENCODE` and `OLLAMA`, with shared application settings and optional installed Rez. Run `exit` to return to the original terminal environment.

On an existing workstation without the local command link, use `/studio/bin/studio env` or `python3 studio env` from this repository. Shared applications must already be installed; entering the shell does not start applications, model downloads or services. Administrative lifecycle commands remain `python3 studio workstation ...`.

## Daily operations

```bash
python3 studio workstation status art
python3 studio workstation stop art
python3 studio workstation start art --expires-in 1d
python3 studio workstation connect art
python3 studio workstation extend-expiration art --additional-minutes 480
python3 studio workstation destroy art --delete-stack
```

CLI stop/destroy releases only the selected owner's hostname. With the shared
[stopped-owner cleanup](docs/independent-eip.md#automatic-release-after-stopping) deployed,
timer-driven stops also release the association after EC2 reaches `stopped`.
Start claims the hostname if free. Explicit assignment moves
the existing shared EIP and prepares
trusted TLS, checking both affected desktops. A former owner gets
an auto-assigned public IP, with bounded stop/start recovery if needed. No extra EIP
or CPU-return subsystem. The owner needs only one reachable endpoint.

Manual stop and expiration preserve the workstation stack and local EBS.
Each timed workstation start verifies a fresh stop timer before starting EC2. The
default is the duration selected at the most recent create/start; `--expires-in`
overrides it. An extension changes the current deadline without a four-hour cap.
Saved EBS files survive stopping; RAM and instance-store data do not.

Explicit `destroy --delete-stack` deletes the workstation and local disks.
Shared EFS, EIP, DNS, ACM certificate and pre-existing profiles/client security
groups remain independent. Stopped workstations still incur storage and applicable
public IPv4 charges. The native timer can only stop its scoped instance; destructive
test cleanup is separate from the ordinary workstation lifecycle.

Publisher uses the same named lifecycle, its dedicated IAM profile and existing
EFS/Rez build/promotion workflow. It has no DCV desktop. See the full
[Studio CLI guide](docs/studio-cli.md) for publisher creation, readiness, run/result,
explicit promotion, expiry extension, recovery, security and cost implications.

## Engineering and historical records

[Documentation index](docs/INDEX.md), [engineering charter](AGENTS.md),
[minimum-change playbook](ENGINEERING_PLAYBOOK.md),
[RES compatibility](docs/RES_COMPATIBILITY.md), and
[deviations](docs/DEVIATIONS.md) distinguish current operations from dated evidence.
Original upstream sources stay pinned to `aws/res` tag `2026.06`.
Historical fixed-name CPU/GPU handoff and Lambda/DynamoDB cleanup proposals are
retired, not parallel supported implementations. Their previous source remains in
Git history. GPU Image Builder work is unchanged by this lifecycle integration.
