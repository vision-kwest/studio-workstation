# Studio Workstation

Named, disposable Ubuntu 24.04 workstations on AWS: CPU, NVIDIA GPU and an isolated
software publisher. Uses CloudFormation, EC2, Amazon DCV, Systems Manager and the
pinned RES 2026.06 standalone bootstrap—not the full RES control plane.

## Current development status

Merged PR #116 integrates multiple named workstations, shared hostname ownership,
and one native compute/disk lifecycle. See PR #116 and Issue #98 for its measured
acceptance evidence; Issue #125 tracks automatic assignment of an available hostname.
Source/offline checks for subsequent changes are not authenticated browser acceptance.
The previously accepted single-GPU RES/T4/DCV behavior remains the provisioning
baseline; no new installer or GPU bootstrap architecture is introduced.

## Quick start

```bash
git clone https://github.com/vision-kwest/studio-workstation.git
cd studio-workstation
git checkout main
python3 -m pip install -r requirements.txt
python3 studio workstation profiles
python3 studio workstation create --profile gpu --name art --dry-run
python3 studio workstation create --profile gpu --name art --expires-in 150m
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
Use `--no-studio-software` to skip Rez; shared storage still follows DCV setup.
The normal `workstation` account can install and save files under `/studio` without sudo.
Shared application launchers use MenuLibre (**Menu Editor**); see the
[shared application guide](docs/shared-applications.md) for setup, GUI saves and refresh.
Default DCV TCP/8443 is public, with a warning; restrict it using
`--client-cidr YOUR_PUBLIC_IP/32`. No SSH ingress is needed. Run CLI endpoint checks
from a network allowed by that CIDR.

New desktops need a unique Linux password set interactively:

```bash
python3 studio workstation connect art --ssm
sudo passwd workstation
```

Open the URL from `connect art` and log in as `workstation`, not with AWS credentials.
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

## Daily operations

```bash
python3 studio workstation status art
python3 studio workstation stop art
python3 studio workstation start art
python3 studio workstation connect art
python3 studio workstation destroy art --delete-stack
```

Stop/destroy releases only the selected owner's hostname. Start claims it again
if still free. Explicit assignment moves the existing shared EIP and prepares
trusted TLS, checking both affected desktops. A former owner gets
an auto-assigned public IP, with bounded stop/start recovery if needed. No extra EIP
or CPU-return subsystem. The owner needs only one reachable endpoint.

EC2 and all attached local EBS are one disposable workstation. Stop/start preserves
them; CloudFormation destroy or expiration terminates compute and natively deletes
the disks. Durable data belongs outside the workstation. Shared EFS, EIP, DNS, ACM
certificate and pre-existing profiles/client security groups remain independent.
There is no volume-only schedule or deletion controller. Native workstation expiry
uses a scoped stack-deletion schedule and an exact-instance termination fallback.

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
