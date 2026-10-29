# RES 2026.06 GPU AMI source trace and Image Builder reproduction

## Confirmed upstream code (tag `2026.06`)

- `source/idea/idea-bootstrap/resources/scripts/virtual-desktop-host/linux/install.sh`:
  validates `-g NVIDIA`; disables Nouveau, installs shared prerequisites,
  writes `install_finished.lock`, and handles the first reboot.
- `source/idea/idea-bootstrap/resources/scripts/virtual-desktop-host/linux/install_post_reboot.sh`:
  invokes `scripts/dcv/linux/dcv_server.sh` for a console session,
  then handles the second reboot and application install.
- `source/idea/idea-bootstrap/resources/scripts/dcv/linux/dcv_server.sh`:
  installs GPU driver prerequisites, invokes the existing GPU installer,
  then installs DCV if absent.
- `source/idea/idea-bootstrap/resources/scripts/dcv/linux/gpu_drivers.sh`:
  `g4dn` selects `install_nvidia_grid_drivers`; checks for
  `nvidia-smi`, downloads GRID packages, runs NVIDIA's installer,
  configures `nvidia-xconfig`, and requests a reboot.
- `source/idea/idea-bootstrap/resources/scripts/dcv/linux/debian/dcv_server.sh`:
  Ubuntu 24.04 prerequisites, DCV server/web viewer/Xdcv and
  **nice-dcv-gl** for GPU instances; skips dummy Xorg configuration
  for GPU.
- `source/idea/idea-bootstrap/resources/package_config.yml`:
  `package_config.gpu_driver.nvidia.s3_bucket_url` =
  `ec2-linux-nvidia-drivers.s3.amazonaws.com`;
  `s3_bucket_path` = `s3://ec2-linux-nvidia-drivers/latest/`.
  Ubuntu prerequisites: `mesa-utils`, `gcc`, `make`,
  `linux-headers-$(uname -r)`, `linux-modules-extra-$(uname -r)`.
  The `nvidia_public_driver_versions.g4dn` setting is for the
  **public/Tesla** driver branch, *not* the GRID branch selected for g4dn.
- `source/idea/idea-bootstrap/src/ideabootstrap/dcv/linux/gl.py`:
  GPU-family lookup; the existing standalone override uses
  `STUDIO_GPU_FAMILIES` instead of cluster-settings DynamoDB.
- `source/idea/idea-bootstrap/src/ideabootstrap/dcv/linux/x_server.py`:
  Xorg validation and graphical target startup.

**Important:** The GRID installer path uses S3 reads from a separate
AWS-owned driver bucket; confirm the Image Builder instance profile
has the necessary access. Do not assume the existing workstation role
or a successful CPU build proves GPU driver access.

## Exact Image Builder components — evidence still required

The `studio-workstation` repository contains the runtime CloudFormation
template, renderer, and RES overlay, but does **not** currently contain
a canonical versioned definition of the existing CPU Image Builder
pipeline and its component YAML. Reproducing the GPU build without
retrieving those exact live definitions would be guesswork.

Run the following **read-only** commands in administrator CloudShell:

```bash
aws imagebuilder list-image-pipelines --region us-east-1 \
  --query 'imagePipelineList[].{Name:name,Arn:arn}' --output table

python3 scripts/audit_imagebuilder_pipeline.py \
  --pipeline-arn EXACT_PIPELINE_ARN_FROM_LIST \
  --region us-east-1 \
  --output ~/studio-release/imagebuilder-cpu-audit.json
```

The export includes the live pipeline, recipe, ordered component
documents, build infrastructure and distribution settings. Review
sensitive IAM/network details before sharing. In particular, verify
whether the pipeline is the validated `studio-cpu-dcv-pipeline`
and whether the GPU instance type is allowed by its infrastructure
configuration. The pipeline may reference component versions that
differ from those in the working AMI.

## Reproduction plan after audit

1. Pin the exact CPU Image Builder component versions and parent AMI.
2. Preserve the known-working GDM-first, desktop, DCV and smoke-test
   ordering. Do not repeat the historical live-systemd-upgrade failure.
3. Create a **separate GPU image recipe/pipeline** rather than changing
   the CPU pipeline or its working AMI.
4. Use `g4dn.xlarge` as the GPU build instance and retain RES's
   original `-g NVIDIA` path, GRID driver installer, DCV GL packages,
   Xorg configuration and reboot sequencing. For prebake,
   use `PREBAKING_AMI=true` as the original Image Builder path does.
5. Verify GPU driver installation with `nvidia-smi`, kernel module,
   `nvidia-xconfig`, `glxinfo -B` showing NVIDIA T4 (not llvmpipe),
   `dcvserver`, and console readiness. Do not claim DCV console
   readiness from the AMI bake alone if a user session has not been
   launched.
6. Publish a new versioned GPU AMI only after smoke tests. Deploy
   with the existing `--profile gpu` renderer and a new stack,
   preserving the validated CPU workstation.
7. Validate stop/start, DCV login, GPU renderer and package release
   reproducibility. Only then enable GPU in the end-user CLI.

**No GPU Image Builder build or EC2 deployment is authorized by this
document.** The next step is to obtain the exact existing pipeline
definitions and produce a reviewable GPU recipe delta.
