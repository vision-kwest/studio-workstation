# Studio EFS mount helper provisioning

Every CPU/GPU create installs official `AmazonEFSUtils` through SSM Distributor after DCV readiness. The existing setup service completes asynchronously; `--no-studio-software` skips Rez and the Ollama startup service. The optional immutable Rez 3.4.0 bundle contains no EFS `.deb`.

CPU/GPU `start` also checks for `mount.efs` after desktop readiness and installs the
official helper only when a successful SSM probe reports it missing. This recovers
a create interrupted before helper installation without reinstalling a working
helper. Failed or inconclusive checks report incomplete software setup and leave
DCV available. The existing service then completes `/studio` setup asynchronously;
helper installation alone does not prove the shared drive is ready. See [Issue #86](https://github.com/vision-kwest/studio-workstation/issues/86).

The guest `studio-software-setup.service` waits for DCV readiness and requires `mount.efs`. If the helper is absent, it retries after 30 seconds without blocking DCV. It mounts `fs-054fd0b72429f4569` read/write using `tls,iam`, then exposes `/opt/studio/packages/shared` at `/studio`. New desktop accounts and the shared directory use UID/GID 2000. Only after storage succeeds does optional, checksum-verified Rez installation begin.

The script migrates the exact previous read-only fstab entry, accepts its own existing `/studio` link, and reports unrelated conflicts. It creates only the new shared directory; it does not recursively change old package ownership. The publisher also exposes `/studio`, but ordinary desktop writes do not require a publisher or Rez.

`StudioWorkstationSSMRole` uses inline policy `StudioEfsSharedWrite`, recorded in `infra/workstation-efs-write-policy.json`, for mount/write access to this filesystem over TLS through its mount targets. It replaces `StudioEfsConsumerMountRead`. The existing default EFS filesystem policy remains in place; this change does not deploy the historical review-only resource policy or claim a new filesystem-wide access boundary.

November 18, 2026 preflight: EFS had no custom filesystem policy or access points, and no workstations were running. The review-only publisher/consumer policy was not deployed. [Issue #96](https://github.com/vision-kwest/studio-workstation/issues/96) tracks the scope; the acceptance evidence below records this run's verification and publication limits.

A clean software setup requires SSM connectivity, `AWS-ConfigureAWSPackage`, and Distributor package `AmazonEFSUtils`. Helper failures report incomplete software setup while DCV remains available and GPU expiration safeguards remain armed.

## Task #1 acceptance — November 18, 2026

**Source/publication:** At verification time, commit `8e6249f48c7ac596b712ddcdd88ad3944b2e5ff4` and this evidence were local and unpublished after automatic approval review blocked GitHub push. This acceptance run did not include GitHub CI or merge. The AWS write policy was applied, its readback matched the repository file, and the old `StudioEfsConsumerMountRead` policy was absent. **Offline:** 135 tests passed.
**Live delivery:** A rendered CPU template without Rez was launched through native CloudFormation APIs as `studio-shared96-a`, instance `i-01d15bc67346251e4`. Official `AWS-ConfigureAWSPackage` installed `AmazonEFSUtils` (`amazon-efs-utils 3.4.0-1`). Guest setup SHA-256 matched `c510717fd2098b7d13df93461c4ca80b2660bc9d0ac1df22ab310269de788125`.

| Evidence | Observed result |
|---|---|
| Initial, 20:56:40 UTC; SSM `185e311c-b8c9-4da9-b009-b388b1d017b7` | UID/GID 2000; `/studio` → `/opt/studio/packages/shared`; actual `nfs4` read/write mount with `tls,iam` fstab; shared owner `2000:2000`, mode `2755`; ordinary-user create/edit/rename/delete/execute passed; no Rez environment or installation. |
| Stop/start, 21:00:47 UTC; SSM `a45e9782-ebf9-40bf-a1bc-7ed4b75c4367` | Marker checksum unchanged; new ordinary-user create/delete passed; one fstab entry. Boot ID changed from `42b72db0-f6e7-4843-9c33-3253498599fe` to `7378734e-58b2-4957-90b2-896beef2d1a6`. |
| Fresh B, 21:21:31 UTC; SSM `401d70f5-d1f2-4cb1-b2e6-ddf47e5fb9ba` | `studio-shared96-b`, `i-0a82ba51be2bfa203`, `m7i.xlarge`, same source with default Rez enabled. UID/GID, script hash, mount and alias matched; A's marker checksum matched after A was terminated/deleted. Ordinary-user append/create/delete and removal of the temporary test directory passed. The command then ended `Failed(2)` solely because the trailing auxiliary `rez-env --version` probe used an invalid flag. |
| Corrected probe, 21:22:40 UTC; SSM `c67d4eb8-7897-4495-b3e2-27b805e37cf9` | Succeeded: `rez -V` reported 3.4.0, setup service active/exited with `ExecMainStatus=0`, helper 3.4.0-1, and ordinary-user confirmation that temporary test data was absent. No code change was needed. |
| Final cleanup audit, 21:25:42 UTC | Both workstation and both safety stacks `DELETE_COMPLETE`; both instances terminated; A volume `vol-0dec62a147b86b5ec` / SG `sg-088959642e6d80bb4` and B volume `vol-08b248e94acb0f8e8` / SG `sg-0119cc8c0f7f60074` absent. All four schedules absent by `ListSchedules`; both expiration roles returned `NoSuchEntity`; zero nonterminated issue96 test instances. EFS and all five mount targets available, no access points, default filesystem policy unchanged (`PolicyNotFound`). |

**Test marker SHA-256 before B appended:** `9c135eba42106e447cb7bdc0000ec9b55a57443624ac266a720a3a39c91043ea`. This is CPU shared-storage acceptance only; no live GPU, authenticated desktop login, or application acceptance is claimed.

## Historical acceptance

Live acceptance (2026-11-11): On GPU `i-04b5259a90dc8bf50`, SSM Distributor installed `amazon-efs-utils 3.3.5-1`. The setup service then succeeded, the read-only EFS mount resolved production `ollama-0.40`, and `qwen2.5:0.5b` produced `SUCCESS` while the Tesla T4 showed the Ollama `llama-server` process consuming 580 MiB VRAM. At that time, the permanent automatic provisioning path had CI tests but had not yet been tested on a newly launched workstation.
