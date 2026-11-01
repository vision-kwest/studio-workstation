# GPU Image Builder component generation (review only)

> **Historical GPU component generator milestone:** This document shows the original candidate generator and 0.5.0 CPU-derived proposal, **not** the exact final baked GPU recipe. Subsequently GPU Image Builder 0.1.11/1 produced validated AMI `ami-027c98f85b155c2ac`, and the GPU CLI/RES desktop was tested live. The GPU Image Builder pipeline is still disabled and set to recipe 0.1.10. For current outcomes and commands use [GPU profile](gpu-workstation-profile.md), [GPU build diagram](gpu-image-builder-visual.md), and [Studio CLI](studio-cli.md).

This milestone generates **four separate GPU component documents** from the
exact working CPU 0.5.0 Image Builder audit. It does not create an AWS
pipeline or alter the CPU pipeline.

Run from AWS CloudShell:

```bash
python3 -m pip install --user PyYAML
python3 scripts/generate_gpu_imagebuilder_components.py \
  --audit ~/studio-release/imagebuilder-cpu-audit.json \
  --output-dir ~/studio-release/gpu-components
```

The generator verifies the ordered CPU components and versions, then
creates:

1. `gpu_desktop.yml` — preserves GDM-first and Ubuntu desktop installation,
   removes the CPU dummy Xorg configuration, keeps the desktop reboot.
2. `gpu_nvidia.yml` — downloads the SHA-256-verified immutable
   `installation-scripts.tar.gz` from v0.1.3 and **executes the original
   RES `scripts/dcv/linux/gpu_drivers.sh -g NVIDIA`** on a real g4dn
   build host. Installs RES-listed Ubuntu prerequisites and reboots.
3. `gpu_dcv.yml` — derives the working CPU DCV component, adding the
   upstream `nice-dcv-gl` DEB to the same checksum-verified DCV bundle.
4. `gpu_smoke.yml` — extends the CPU DCV smoke tests with NVIDIA T4,
   driver module, GPU Xorg config, `nice-dcv-gl`, and `glxinfo -B`
   verification that rejects llvmpipe.

Reuse `studio-cpu-gdm-first:0.5.0` unchanged. Build instance type
must be `g4dn.xlarge`, with a separate image recipe, distribution
and pipeline. Do not change the CPU 0.5.0 pipeline.

## Pre-deployment gates

- **IAM:** The builder role
  `studio-workstation-stack-BuilderRole-Oz1YUBy0vPYr` currently has
  `implicitDeny` for signed `s3:GetObject` on the NVIDIA GRID driver.
  Anonymous `HeadObject` succeeds, but the original RES script uses
  **signed** `aws s3 cp --recursive`. Before building, authorize the
  minimum required reads for the driver bucket and our private v0.1.3
  installation archive, then verify from the actual builder identity.
  Do not alter the upstream installer to work around this.
- **Driver pinning:** The original RES GRID branch uses the mutable
  `latest/` distribution. The observed package was
  `NVIDIA-Linux-x86_64-595.91.07-grid-aws.run` (429294370 bytes).
  Confirm compatibility with the selected Ubuntu 24.04 kernel and
  obtain a stable version/hash policy before claiming reproducible GPU AMIs.
- **Build prerequisites:** The upstream NVIDIA script assumes RES
  `bootstrap_common.sh`, `config_common.sh`, `package_config.yml`,
  GPU device and Xorg/driver prerequisites. Validate those against the
  archive and g4dn builder environment.
- **Reboot and smoke tests:** Image Builder reboot checkpoints must
  remain in place. The GL test may need the GDM Xauthority path from
  the actual build host. Failure must abort rather than produce an
  unverified AMI.
- **Packaging:** No new release has been published. This stage
  uses existing v0.1.3 RES installation scripts; if source changes are
  required, publish a new immutable release first.

**No AWS writes occur from the generator.** Generated YAML is a
review artifact, not an approved live pipeline. Review the output
and IAM policy before executing any Image Builder creation/build.
