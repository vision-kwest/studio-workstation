# Standalone RES 2026.06 GPU/DCV runtime — validated implementation

**Current after merged PR #65 (November 2026): GPU runtime and persistent CLI acceptance validated.** On baked GPU AMI `ami-027c98f85b155c2ac` in `us-east-1`, the original RES cloud-init installer, reboot continuation, Supervisor and `ideavirtualdesktop` bootstrap (with narrow standalone guards) passed live testing. PR #61 and PR #65 are merged; the GPU Image Builder **pipeline** remains disabled and points to recipe 0.1.10.

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
- PR #61 and PR #65 have been merged. The GPU Image Builder pipeline was **not** upgraded; updating it remains separate work.

## Offline verification

Run `python3 -m unittest discover -s tests -v` and review the focused GitHub Actions contracts. Only the original RES/Supervisor path is part of the validated runtime. The abandoned alternative systemd coordinator and its mocked-only tests were retired from this release PR.

## PR #65 — separately published installer (November 1, 2026)

The Task 7 release above is historical and remains intact. For the **completed** persistent-GPU CLI acceptance test, the **new, verified installation archive** was published to a separate S3 key through an authorized CloudShell session:

- Bucket: `studio-workstation-releases-401233741942-us-east-1`
- Key: `releases/pr65-pinned-download-084e704e/res-2026.06/installation-scripts.tar.gz`
- VersionId: `bN0To3QzfVqVsN5P_6YmTraGIQuHcrtB`
- SHA-256: `084e704e5c3e79152b0324664f153a826ced40b878c1a87e31cf62e4f9551bb5`
- Size: 42,816 bytes; SSE-S3 AES256; read-only HeadObject and ListObjectVersions verified exactly one current version and no delete marker.

The **application archive** continues to use the previously verified Task 7 S3 key `releases/task7-pr61-postreboot-a8ec326/res-2026.06/virtual-desktop.tar.gz`, VersionId `nzyYrLhYAQ.OTFNpFE_xSHPaK06T1V53` and SHA-256 `e7686d6240db9062b5ea62f94859bd657de8c1231e8a3ba097704f9faa45d147`.

The GPU renderer deliberately uses two distinct S3 prefixes, embedding the new bootstrap VersionId/digest in the original RES user data and the existing app VersionId/digest in `/etc/studio-standalone.env`. CPU and other non-pinned rendering remains unchanged.

**Resolved in the accepted test:** The workstation EC2 instance profile received an approved narrow `s3:GetObjectVersion` grant for the two exact archive keys, and the live instance successfully downloaded and executed RES runtime. The approved acceptance safeguard was **not** the proposed Lambda/DynamoDB system: two independently verified one-time EventBridge Scheduler actions used the dedicated tag-scoped `studio-pr65-cli-expiration` role. The test's instance, retained EBS, SG, launch template and schedules were verified removed; the role remains. See [Issue #46](https://github.com/vision-kwest/studio-workstation/issues/46) and [current CLI instructions](../docs/studio-cli.md).

## Final PR #65 GPU CLI acceptance — November 1, 2026

The accepted GPU CLI launched the exact scoped `studio-gpu-cli-accept65` test stack. The user confirmed interactive DCV password login. EC2 was stopped and restarted with the **same instance and 100 GiB retained root EBS**; a file under `/home/workstation/` retained the exact original SHA-256. After restart, RES semaphore readiness, GDM, Xorg, Tesla T4 hardware OpenGL, DCV console and local HTTPS 200 were verified. The public IPv4 changed on stop/start; a second browser login after restart was not separately confirmed.

CloudFormation then reached `DELETE_COMPLETE`; AWS returned not found for the test EBS volume, SG and launch template, and both one-shot Scheduler actions were subsequently removed and independently checked absent. The dedicated IAM role remains. The GPU CLI currently accepts only the exact approved stack/token with one-shot expiration—not arbitrary GPU workstations or a permanent production control plane.
