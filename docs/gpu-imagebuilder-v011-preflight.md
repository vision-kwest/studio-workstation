# GPU Image Builder 0.1.1 review gates

The 0.1.0 GPU component build versions are immutable. Regenerate the YAML
from the original CPU audit after merging this change, inspect it, and
register **new** 0.1.1 components. Never overwrite 0.1.0.

The NVIDIA component now installs `pciutils` and `binutils`, checks
the observed GRID object's size before calling the **unchanged upstream
RES installer**, and preserves the explicit reboot. This size check is
**not** cryptographic integrity validation: RES still reads the mutable
`latest/` prefix. Pinning the binary plus a SHA-256 check and matching
the exact file consumed by RES remains a blocker to reproducibility.

The smoke test preserves CPU DCV console readiness and adds Xorg
`-auth` discovery, `xhost` DCV permission validation, and NVIDIA
OpenGL rendering checks. These checks have not yet run on a GPU build
host; they may require further adjustments after reviewing build logs.

Before the first billable build:
1. Inspect generated 0.1.1 YAML for shell correctness.
2. Confirm original RES `gpu_drivers.sh` installer behavior and kernel
   compatibility on the actual Ubuntu 24.04 parent AMI.
3. Verify driver package pinning/integrity or explicitly accept the
   experimental mutable-distribution risk.
4. Register new component build versions and a new recipe; update only
   the separate GPU pipeline.
5. Keep the CPU pipeline and validated workstation unchanged.

No AWS resources are changed by this PR.
