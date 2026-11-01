# RES-aligned implementation update (v0.4.0)

> **Historical CPU Image Builder v0.4 draft:** This file records the earlier GDM-first installation research and failures; the validated CPU Image Builder process advanced to staged GDM-first recipe 0.5.0 and live CPU v0.1.3 DCV acceptance. GPU AMI 0.1.11/1 was also separately validated. The old `no workstation launcher yet`, `not deployed` and v0.4 rollout steps below describe the original historical checkpoint, **not current project status**. Use [Quick Start](../../README.md), [Studio CLI](../../docs/studio-cli.md), and [Image Builder visual](../../docs/gpu-image-builder-visual.md).

The Image Builder recipe now uses RES 2026.06's `ubuntu-desktop` + `gdm3`, its CPU-only `/etc/X11/xorg.conf` dummy configuration, and its DCV server + web viewer + `nice-xdcv` package selection. This is a *partial* source-based port, not a guarantee of parity: RES also runs Python bootstrap logic for DCV startup, permissions, and Xorg readiness, and its reboot staging differs from Image Builder.

**Before running another build:** confirm the earlier stalled Image Builder execution is cancelled and temporary EC2 builder has terminated. Update the existing CloudFormation stack with `infra/image-builder-cpu.yaml` from this feature branch; the recipe/components are now version `0.4.0`. Verify CloudFormation changes and the selected pipeline recipe version before Run pipeline. Do not run a new build against the old `0.3.x` recipe.

**What success means:** An Image Builder build passing is only a *service-level smoke test*. It does not prove that DCV graphical login works. Do not merge/promote or advertise a working AMI until a clean instance from that AMI has been started, a unique password securely provisioned for the locked `workstation` account, a real DCV console login performed, and a stop/start cycle tested.

**If it stalls again:** collect actual `apt`, `dpkg`, `systemd`, SSM and AWSTOE process/log evidence per [the investigation](../../docs/image-builder-hang-investigation.md) (currently on the documentation PR branch). Do not infer root cause from the last visible GDM warning.

---

# Automated CPU/DCV AMI build — v0.4 (draft)

This replaces the manual-instance build process proposed in PR #2.

## One-time deployment (no CloudShell, SSH or control EC2)
1. In **us-east-1**, open CloudFormation > Create stack > Upload a template and use `infra/image-builder-cpu.yaml`. If using GitHub, download the raw YAML to your computer first.
2. Check `BaseAmiId`: the default `ami-0fc0d6e8d70ab2d42` is the Ubuntu 24.04 AMI from the prior RES test. **Confirm owner 099720109477 (Canonical) and x86_64 in EC2 before deploying.**
3. Choose `m7i.xlarge` (or another allowed CPU build type). A default VPC/subnet with functional outbound internet access is required. The builder uses default Image Builder networking and may need configuration for your account; no inbound SSH is needed.
4. Acknowledge CloudFormation IAM creation and create the stack. It creates an Image Builder recipe, components, IAM instance profile and pipeline, **not a continuously running EC2 host**.
5. In **EC2 > Image Builder > Image pipelines**, select `studio-cpu-dcv-pipeline` and choose **Run pipeline**. The pipeline launches temporary paid build/test EC2 instances, configures Ubuntu and DCV, reboots as required by Image Builder, tests services, and creates an AMI only if its configured steps succeed.
6. Monitor **Image Builder > Images** for build/test failures and inspect the build logs. Do not assume a passing service smoke test proves graphical sign-in.
7. After successful AMI creation, launch a temporary test workstation using the AMI with restricted inbound TCP/UDP 8443, no SSH, SSM role, and a supported CPU EC2 instance. Set a **unique** password for the initially locked `workstation` account using an approved interactive SSM session. Confirm browser login and an actual GNOME desktop, then stop/start and repeat.
8. Mark the AMI **validated** only after the end-to-end test. Clean up unused test instances, volumes and AMI snapshots.

## Important limitations
- **No AWS resources have been deployed or tested by this PR.** The Image Builder component document schema, package names, Xorg dummy setup and session start must be validated by an actual pipeline run.
- Image Builder does not by itself test a browser login. The build smoke test checks GDM, Xorg, DCV, and session existence; human/automated remote connection validation remains a separate gate.
- The DCV package URL and SHA-256 sidecar URL come from AWS RES tag `2026.06`. They are not pinned to an immutable release; verify provenance and versions for production.
- The builder has no public-IP/subnet settings in this first template. It assumes AWS's default networking can reach SSM and external package sources. If not, add a subnet/security group explicitly.
- `workstation` is deliberately locked in the image. The AMI does **not** ship a usable password; first-login password provisioning remains a future automation milestone.
- `ImageRecipe.BlockDeviceMappings` uses `/dev/sda1`; verify against the selected AMI root device name before deploying.
- **No GPU variant, application profiles, or end-user workstation launcher yet.**
- This first version avoids automatic recurring image builds. Run the pipeline manually when an image update is needed.

## Reference
- [AWS RES 2026.06 DCV package metadata](https://github.com/aws/res/blob/2026.06/source/idea/idea-bootstrap/resources/package_config.yml)
- [Image Builder](https://docs.aws.amazon.com/imagebuilder/latest/userguide/what-is-image-builder.html)
