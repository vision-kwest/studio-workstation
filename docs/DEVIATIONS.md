# RES 2026.06 Deviation Register

> **Historical register with continuing relevance:** The numbered RES deviation reviews here preserve their original design and review chronology. Some early items described proposed changes that were subsequently implemented and validated. For the accepted live runtime and current operator commands, refer to [runtime provenance](../runtime/README.md) and [Studio CLI](studio-cli.md); do not use old approval notes as current deployment gates.

**Status: design choices recorded, including approved Option A manual SSM credential enrollment. No approved standalone code patch set yet.**

Every change from the pinned upstream RES commit must be recorded here *before* implementation. Do not treat a proposed removal as proven safe.

| ID | Upstream path / behavior | Proposed change | Necessity | Evidence required | Status |
|---|---|---|---|---|---|
| D-001 | RES Cognito/AD/SSSD and DCV external auth | Replace with Linux account + DCV `authentication=system` | No RES directory or broker for one machine | Trace auth config generation, PAM behavior, session permissions and successful DCV login | **Proposed** |
| D-002 | RES connection gateway, broker, ALB and session URLs | Direct restricted DCV port 8443 | Eliminate RES management infrastructure | Trace DCV server settings, TLS and session lookup requirements | **Proposed** |
| D-003 | RES EFS/FSx/NFS/S3 mount integration | Encrypted local EBS only | Single-machine storage | Confirm installer and Python bootstrap do not require mounts | **Proposed** |
| D-004 | RES cluster/session agent lifecycle | Retain minimal console startup/readiness behavior; remove RES-only state reporting | No RES control plane | Full Python and systemd dependency graph | **Needs investigation** |
| D-005 | RES CloudFormation/cluster orchestration | One EC2 instance, role, SG and EBS | Reduce cost and complexity | Confirm all bootstrap inputs and instance metadata requirements | **Proposed** |
| D-006 | RES GPU/DCV installation | Preserve upstream code and configuration | Fidelity to working demo | Test NVIDIA T4 and CPU headless variants | **No deviation intended** |
| D-007 | RES runtime reboot/continuation | Preserve original logic; do not invent new systemd state machine | Avoid prior ordering-cycle regression | Trace exact user-data/systemd/cron handoff | **No deviation intended** |

## Gate 3 and Gate 4 review references

- [Gate 3 security review](GATE_3_SECURITY_REVIEW.md): Option A first-login activation is approved as a design; exact historical DCV version, PAM, TLS and runtime authorization remain open.
- [Gate 4 minimal patch specification](GATE_4_MINIMAL_PATCH_SPEC.md): file/function-level proposed adaptations for D-001–D-005; D-006 and D-007 must remain unchanged. Exact unified diffs and explicit implementation approval are still required.

## Entry template

For every additional deviation, document:

- **ID and upstream file/function/line range:**
- **Exact original behavior:**
- **Minimal replacement and patch diff:**
- **Standalone dependency requiring the change:**
- **Alternatives rejected and why:**
- **Evidence and tests:**
- **Security and cost effects:**
- **Reviewer approval and date:**

## Shared Comfy default path (Issue #131)

The shipped Comfy template uses name `StudioComfyUI` and path `/studio/apps/comfyui/main/StudioComfyUI`. Deployment uses the existing `--set-default` selector to capture actual registration or manifest metadata. This is a name/path change; the existing helper behavior and RES 2026.06 remain unchanged.

## Desktop administrator access (Issue #144)

The renderer adds the selected CPU/GPU desktop account to Ubuntu's native `sudo` group after account creation, including an existing-account bootstrap retry. Initial password locking/enrollment and other group memberships are preserved. Ubuntu's password-required sudo policy is reused. RES 2026.06 commit `1f526a241fb293a29fb018f08ccf81c100c05a42` and its standalone bootstrap, driver handling, DCV and reboot continuation remain unchanged; the standalone path already skips directory-service sudoers configuration. Live desktop password authentication remains a separate acceptance check.

## Default desktop SSH access (Issue #70)

The owner approved public IPv4 SSH for all future CPU/GPU desktops. The existing
workstation security group adds TCP/22 from `0.0.0.0/0`; `ClientCidr` remains the
DCV-only selector. Publisher retains its supplied security group and SSM bootstrap.

RES 2026.06 commit `1f526a241fb293a29fb018f08ccf81c100c05a42` calls
[`restrict_ssh.configure(session_owner=True)`](https://github.com/aws/res/blob/1f526a241fb293a29fb018f08ccf81c100c05a42/source/idea/idea-bootstrap/src/ideabootstrap/ssh/restrict_ssh.py)
then [`disable_ssh.configure()`](https://github.com/aws/res/blob/1f526a241fb293a29fb018f08ccf81c100c05a42/source/idea/idea-bootstrap/src/ideabootstrap/ssh/disable_ssh.py).
The latter attempts to disable/stop `sshd` socket/service aliases and catches errors.
Those archive contents remain unchanged. After DCV readiness, the existing
`scripts/setup_workstation_software.sh` applies the approved native sshd settings
and enables Ubuntu's `ssh.service` on each boot before its EFS mount attempt.
This reuses the existing setup service without a new RES archive or service.

The effective policy requires public keys for the UID 2000 desktop account,
disables password, keyboard-interactive and root login, enables TCP forwarding
with `GatewayPorts no`, and retains local authorized-key files alongside
the separate local `.ssh/authorized_keys.studio`. After EFS is ready, setup copies
the public-key source `/studio/config/ssh/authorized_keys` into that local file.
Live inspection found the EFS mount root owned by UID/GID `1000:1000`, mode `2775`,
while its shared subtree is `2000:2000`, mode `2755`. Direct sshd reads through that
mount would violate native `StrictModes`; the local copy preserves shared ownership
and permissions. SSH starts before EFS, and a previously copied key survives an EFS
outage. Copies refresh during setup/reboot when the source is available; revocation
requires refreshing every workstation and removing any duplicate local key.
Only approved public keys are shared; private keys stay on the client.
DCV authentication, GPU initialization and native reboot continuation are preserved.
The implementation PR records offline checks and actual login/forwarding/reboot
acceptance separately; this entry does not claim those tests have passed.

## Larger GPU workstation size (Issue #149)

The existing persistent GPU profile also accepts `g4dn.2xlarge` in the CLI,
CloudFormation parameters and inventory ownership checks, including inventory's
legacy profile inference. `g4dn.xlarge` remains the default; the historical Task 7
temporary renderer and launcher retain their original size restriction. Both
sizes use the same RES 2026.06 commit `1f526a241fb293a29fb018f08ccf81c100c05a42`
GPU-family path, pinned archives, AMI, storage and native stop schedules. No
upstream provisioning code changes. Issue #149 records validation results and
remaining live acceptance.

## Creation failure diagnostics (Issue #151)

The guarded-create error path reports CloudFormation resource failure reasons and
the observed workload state, including native rollback to `DELETE_COMPLETE` and
the existing explicit orphan-safeguard cleanup command. Best-effort diagnostics
follow the ownership-checked stop attempt and retain the original failure if
event lookup fails. RES 2026.06 commit `1f526a241fb293a29fb018f08ccf81c100c05a42`,
bootstrap, native rollback, stop permissions and cleanup behavior are unchanged.

## Hostname release after timed stops (Issue #156)

A shared EC2 stopped-event handler, with periodic reconciliation, detaches only the
verified stopped owner's exact EIP association. Existing native stop schedules and
IAM scopes remain unchanged; certificate reconciliation stays in the existing CLI
claim path. Manual release accepts the same stopped-owner association already
detached by background cleanup. RES 2026.06 commit
`1f526a241fb293a29fb018f08ccf81c100c05a42`, bootstrap, DCV, EFS and disk lifecycle
remain unchanged. The implementation PR records offline and live acceptance.

## Historical warning

Earlier custom Image Builder and v0.6.0 systemd-bootstrap implementations diverged substantially from RES and encountered repeated build failures, including a custom service ordering cycle. These are *not* approved deviations and should not be reused as the canonical implementation.

The register is a planning artifact. No AWS resource or provisioning script is changed by this documentation PR.

## Issue #100 — restoring RES runtime GPU installation (draft)

Against upstream `aws/res` **2026.06** commit `1f526a241fb293a29fb018f08ccf81c100c05a42`: retain `scripts/dcv/linux/gpu_drivers.sh`, `virtual-desktop-host/linux/install.sh` and `install_post_reboot.sh` stage order. Replace only the Studio CLI’s custom baked GPU AMI selection with the existing verified Canonical 24.04 parent AMI. Retain upstream RES's signed `aws s3 cp --quiet --recursive` command unchanged. The missing role dependency was restored through IAM inline policy `StudioRes202606NvidiaGridRead`: `s3:ListBucket` for the distribution's `latest/` prefix and `s3:GetObject` for the exact 595.91.07 file; unrelated prefixes/objects are denied. One minimal archive adaptation adds SHA-256 verification (`cae8d82b8407c6371805485b148aa667550abf1775ea7999be69da81cf593156`) immediately after the upstream download, returning nonzero on missing/bad content. A standalone NVIDIA post-reboot check requires the driver executable and `nvidia` module for the active kernel before RES writes its ready marker. The 2026-11-15 guest test confirmed signed S3 List/HEAD was denied and unsigned read succeeded; the old archive failed in `gpu_drivers.sh` at 01:35:37 UTC. **Published candidate, not live accepted:** installation script at `releases/issue100-res-native-20261115/res-2026.06/installation-scripts.tar.gz`, VersionId `sgj5wZfqMTRUImWDDbxR08emK755aU7A`, SHA-256 `6d3187049ecee202167cc462492431e3cb9a2a22d78300a8189db3fa2924ee7d` (42,994 bytes), produced reproducibly from pinned RES sources by the existing CloudShell release tool. The old PR #65 installer and application archives remain unchanged.

## Issue #100 follow-up — versioned GRID source (November 25, 2026)

RES 2026.06 commit `1f526a241fb293a29fb018f08ccf81c100c05a42` downloads GRID
from `package_config.yml`'s mutable `latest/` prefix. Our fixed 595.91.07 checksum
failed on `art5` when that prefix advanced to 620.70.06. Change only the NVIDIA
source scalar to `grid-20.2/`, and scope IAM listing/object reads to that release.
Retain the signed recursive copy, checksum, DKMS, package order and reboot flow.
A new immutable installer must be pinned separately from the unchanged app archive;
see [release provenance and deployment gates](../runtime/README.md#versioned-grid-source--november-25-2026).

## Comfy helper argument validation (Issue #142)

The existing Studio Comfy helper now strictly parses its own options before configuration access or app launch. Native AppImage arguments follow an explicit `--` separator. This prevents a mistyped option from starting Desktop and retains normal launch, default selection and update behavior. RES 2026.06 commit `1f526a241fb293a29fb018f08ccf81c100c05a42`, provisioning, Comfy Desktop, shared templates and local-state handling are unchanged.

## Ubuntu desktop update prompts (Issue #137)

The renderer creates a native per-user XDG `Hidden=true` override for
`update-notifier.desktop` and masks Ubuntu Noble's `update-notifier-release.path`
and `.service` for each new CPU/GPU desktop account. The masks affect the GUI
release notification, not manual upgrades or APT security-update policy. These
preferences are local and precede the first GNOME session. RES 2026.06 commit
`1f526a241fb293a29fb018f08ccf81c100c05a42` installers, driver handling and reboot
continuation remain unchanged; this does not resolve later kernel/driver mismatch.

## Studio application shell (Issue #140)

The optional software layer adds `studio env`, a child Bash shell, and small uppercase wrappers for the existing shared application paths. The existing Comfy helper and its registration/default/update behavior are reused unchanged. OpenCode seeds only an individual global configuration link; application databases and service state remain local. OLLAMA resolves the existing Rez package and shared models without starting a server.

The post-DCV setup creates the user-owned shared command/project directories, adds the local `studio` symlink and installs Ubuntu's `ripgrep` alongside the existing menu utilities. These precede optional Rez. RES 2026.06 commit `1f526a241fb293a29fb018f08ccf81c100c05a42` installers, DCV/GPU behavior, reboot continuation and workstation lifecycle remain unchanged. Source verification and offline checks do not establish live desktop acceptance; the implementation PR records that evidence separately.

## Desktop idle defaults (Issue #165)

The owner requested automatic blanking and screen locking disabled on future
CPU/GPU workstations. The renderer installs a native GSettings vendor override
before RES desktop installation: session `idle-delay=uint32 0` and screensaver
`lock-enabled=false`. Package schema triggers compile it on initial installation;
bootstrap reentry also compiles it when desktop schemas are present. User choices
remain editable and take precedence. RES 2026.06 commit
`1f526a241fb293a29fb018f08ccf81c100c05a42` desktop prerequisites, GNOME/DCV order,
reboot continuation and authentication remain unchanged. The upstream
`gnome_utils.configure()` animation workaround applies only to AL2023.
The implementation PR distinguishes native schema checks from fresh-login proof.

## Ollama startup service (Issue #92)

The owner now requests an Ollama server on every normal workstation boot. The optional software setup enables a native systemd service after verifying Rez, running the existing uppercase wrapper as the desktop user with a localhost listener and the shared model store. systemd retries failures without an EFS readiness dependency or a custom startup manager; no model is downloaded or preloaded. The deliberate `--no-studio-software` opt-out also skips this service. RES 2026.06 commit `1f526a241fb293a29fb018f08ccf81c100c05a42`, desktop readiness, GPU setup and shared model/context configuration are unchanged. Real boot and GPU acceptance remain separate from offline validation.

## Autonomous EFS helper provisioning (Issue #86)

The CPU/GPU stack owns a native State Manager association and Command document
for the pinned official `AmazonEFSUtils` package, replacing the local CLI command.
Native steps preserve an existing helper and recover the official package's
interrupted-install false success before the existing guest service mounts storage.
The dispatch role permits only the exact workstation and this fixed document.
RES 2026.06 commit `1f526a241fb293a29fb018f08ccf81c100c05a42`, driver/DCV stages,
EFS permissions and shared-data handling remain unchanged. Source evidence and
remaining live gates are in [EFS helper provisioning](efs-helper-install.md).

## NVIDIA kernel updates (Issue #168)

RES 2026.06 commit `1f526a241fb293a29fb018f08ccf81c100c05a42` installs Ubuntu's
`dkms` in DCV USB setup, after the NVIDIA installer has already run. The overlay
installs DKMS and the AWS header metapackage in a separate, checked APT step within
the existing GPU prerequisites, then requests `--dkms` on the full GRID installation.
The first live test showed that the legacy batch can fail because the older AMI's
`linux-modules-extra-7.0.0-1014-aws` package is unavailable, preventing every package
in that transaction from installing. The dedicated step exits on its own failure;
the original RES package selection and diagnostics remain intact.
The standalone GPU post-install check requires the installed
driver's DKMS registration, `AUTOINSTALL="yes"`, and matching NVIDIA modules for
the running and staged newer AWS kernels before RES can mark setup ready.
Native DKMS performs builds; there is no additional driver manager or boot service.
The pinned NVIDIA payload/checksum, module flavor, RES reboot sequence, CPU path,
DCV setup, and APT security-update policy are preserved. Existing workstations
require the separately verified repair; a new CLI release does not retrofit them.
Offline contracts and fresh GPU/kernel-transition acceptance are reported separately
in the implementation PR and [runtime provenance](../runtime/README.md).

## Readiness-based workstation lifetime (Issue #68)

The original CLI starts the selected lifetime after CloudFormation completes,
before RES and Studio setup. New timed CPU/GPU stacks retain the native stop
schedule but initially allow 60 minutes for setup. A stack-owned SSM Automation
document, scoped execution/dispatch role and initial association move that same
deadline to readiness plus the selected duration. Start submits the durable
Automation before starting EC2. Existing RES 2026.06 commit
`1f526a241fb293a29fb018f08ccf81c100c05a42`, GPU installation and guest setup service
are reused unchanged; the observer verifies their actual completion and writable
shared-storage access. Native SSM waits, Run Command and bounded loop actions
handle reboots, with generation/deadline guards on the short schedule update.
No Lambda, database, guest Scheduler permissions or replacement installer is added.
Existing stacks retain explicitly labelled legacy timing; no automatic migration
is performed. Source/offline validation and live readiness acceptance are separate.

The readiness role uses the exact schedule ARN assembled from its group and name.
CloudFormation's schedule `GetAtt` omits the custom group during lookup
([AWS issue #1726](https://github.com/aws-cloudformation/cloudformation-coverage-roadmap/issues/1726)),
which caused `art4` creation to roll back. An explicit dependency on
`AutoStopSchedule` preserves creation order before the readiness role and association.
The IAM scope, timer behavior and RES provisioning are unchanged by this correction.
