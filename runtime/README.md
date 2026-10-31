# Standalone RES 2026.06 GPU/DCV runtime — validated implementation

**Status (2026-10-31): LIVE VALIDATED on baked GPU AMI `ami-027c98f85b155c2ac` in `us-east-1`.** The release path is the **original RES cloud-init installer, reboot continuation, Supervisor and `ideavirtualdesktop` bootstrap** with narrow standalone guards. No parallel systemd runtime coordinator is installed or enabled. This validation does not mean the PR is merged or that the GPU Image Builder pipeline has been updated.

## Architecture and provenance

Pinned upstream: `aws/res` tag `2026.06`, commit `1f526a241fb293a29fb018f08ccf81c100c05a42`. The `runtime/overrides/` tree preserves original upstream modules with targeted `STUDIO_STANDALONE=true` branches, avoiding full RES Cognito/AD, credential broker and DynamoDB cluster configuration. The original `install.sh` and `install_post_reboot.sh` preserve reboot semaphores and Supervisor startup. The post-reboot app retains original RES Xorg and automatic DCV console creation and readiness checks. The production-proven console permissions are `root:dcv` with mode `0640`; authentication is DCV `system` with local `workstation` account.

The original RES installation/application source archives are hash-verified before reconstruction by `runtime/build_res_overlay.py`. The pinned derived archives tested live are:

| Artifact | SHA-256 | S3 object version |
|---|---|---|
| `installation-scripts.tar.gz` | `7ca921aa8a493d9399918f032b62f7e0e37ce7754fa3f1a2c805047507ecd7a1` | `dlOKXdRAEJqpeNZNBotj87q0bdNlI5VC` |
| `virtual-desktop.tar.gz` | `e7686d6240db9062b5ea62f94859bd657de8c1231e8a3ba097704f9faa45d147` | `nzyYrLhYAQ.OTFNpFE_xSHPaK06T1V53` |

S3 prefix: `s3://studio-workstation-releases-401233741942-us-east-1/releases/task7-pr61-postreboot-a8ec326/res-2026.06/`.

The reviewed GPU launch template is `gpu-template.json` at the same prefix, S3 VersionId `SA7FH5Yp5FSVmFRyih2Fr3vqvi5FKECi`. `runtime/task7_safe_launcher.py` pins this version and AMI, requires IMDSv2, launches a temporary encrypted delete-on-termination gp3 volume and verifies an independent one-time EventBridge termination schedule immediately after instance creation. It is a **supervised Task 7 test launcher**, not a general production lifecycle manager. The launcher starts with zero ingress; public DCV TCP 8443 was separately authorized for the temporary login test and removed with cleanup.

## Image Builder and acceptance

Recipe `studio-gpu-ubuntu2404-dcv/0.1.11` contains original GDM and desktop stages, AWS CLI, pinned NVIDIA stage `0.1.7/1`, corrected DCV stage `0.1.4/1`, and bake-time smoke `0.1.2/1`. AWS Image Builder image `0.1.11/1` is **AVAILABLE**, AMI `ami-027c98f85b155c2ac`. The existing GPU pipeline remains **DISABLED and points to recipe 0.1.10**; the CPU pipeline was not changed.

Live Task 7/8 acceptance confirmed:
- RES installation and both setup reboots, cloud-init completion without errors, Supervisor/bootstrap, `linux_vdi_config_host_ready.lock` and automatic DCV `console` READY.
- NVIDIA Tesla T4, direct OpenGL 4.6 renderer, Xorg :0, GDM3 and DCV server; DCV local system authentication and owner `workstation`.
- Real user-confirmed password login through DCV, graphical Ubuntu/Blender desktop, remote audio, and user-confirmed reconnect after an additional explicit reboot.
- Original RES/DCV display resolution is automatic (commonly 2560×1600); do not force 1920×1080.
- Temporary EC2, root EBS, security group and EventBridge schedule cleaned up. See [Issue #60](https://github.com/vision-kwest/studio-workstation/issues/60) for command IDs, acceptance evidence and cleanup.

## Release limitations and deferred work

- The source AMI snapshot is **unencrypted**. Temporary test instances explicitly used encrypted EBS; production image/snapshot encryption needs a separate release-hardening decision.
- Access from changing client networks and optional IP restrictions: [Issue #62](https://github.com/vision-kwest/studio-workstation/issues/62). Public DCV ingress must never be enabled implicitly.
- Slight YouTube audio/video synchronization offset observed by the user; latency and browser/client performance not yet measured.
- The Task 7 launcher does not automatically reconcile every orphaned SG/schedule after abnormal process failure; always supervise cleanup.
- PR #61 remains draft until final source review and CI pass. Do not merge or update pipelines without explicit approval.

## Offline verification

Run `python3 -m unittest discover -s tests -v` and review the focused GitHub Actions contracts. Only the original RES/Supervisor path is part of the validated runtime. The abandoned alternative systemd coordinator and its mocked-only tests were retired from this release PR.
