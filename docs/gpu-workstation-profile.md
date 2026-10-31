# GPU workstation profile — source-first implementation

> **Status update (2026-10-31):** The GPU AMI `ami-027c98f85b155c2ac` has passed Image Builder 0.1.11/1, real RES 2026.06 GPU/DCV initialization, local password login, OpenGL and reboot reconnection (Issue #60, merged PR #61). The CLI GPU profile is being integrated in Issue #46 with the exact pinned release prefix `releases/task7-pr61-postreboot-a8ec326/res-2026.06`. Earlier paragraphs below are historical pre-validation notes, not current instructions. The persistent CLI stop/start path requires a separate live test.

## What is implemented

The offline renderer now accepts `--profile gpu`, constrains
`InstanceType` to `g4dn.xlarge`, and passes `-g NVIDIA` to the
**existing RES 2026.06** `scripts/virtual-desktop-host/linux/install.sh`.
CPU stays unchanged (`-g NONE`, `m7i.large` default).

The published RES-derived application overlay already includes the
original `ideabootstrap.dcv.linux.gl` with a minimal standalone
`STUDIO_GPU_FAMILIES` lookup in place of DynamoDB cluster settings.
It retains upstream `dcv.configure()`, `gl.configure()`, Xorg,
DCV readiness and automatic-console session behavior.

Upstream RES references:
- `source/idea/idea-bootstrap/resources/scripts/virtual-desktop-host/linux/install.sh`
- `source/idea/idea-bootstrap/resources/scripts/dcv/linux/dcv_server.sh`
- `source/idea/idea-bootstrap/resources/scripts/dcv/linux/gpu_drivers.sh`
- `source/idea/idea-bootstrap/src/ideabootstrap/dcv/linux/gl.py`
- `source/idea/idea-bootstrap/src/ideabootstrap/dcv/linux/x_server.py`

**This is a profile renderer and contract-test milestone, NOT a
validated GPU workstation.** Do not reuse CPU AMI
`ami-0d6dd9e8265d48906` on `g4dn.xlarge` without first validating
its GPU driver, Xorg and DCV prerequisites.

## RES GPU driver path to preserve

Upstream `install.sh` accepts `-g NVIDIA`; the GPU path disables
Nouveau and routes through the RES DCV driver installation scripts.
For `g4dn`, RES `gpu_drivers.sh` selects NVIDIA GRID drivers.
It looks up driver package configuration through RES settings and
downloads from NVIDIA's S3 distribution path. **Standalone mode
does not currently provide that complete package configuration.**
Merely passing `-g NVIDIA` does not prove the GRID driver installer
will succeed without the RES control plane.

Prefer the existing RES Image Builder/prebake strategy: create a
**separate GPU AMI** using the upstream RES GPU/DCV install path and
verify `nvidia-smi`, `nvidia-xconfig`, Xorg, `dcvserver` and
OpenGL hardware rendering in Image Builder smoke tests. Retain the
RES installer and reboots; do not create a second hand-written driver
installation framework.

## Safe offline render

```bash
python3 runtime/render_workstation.py \
  --profile gpu \
  --bucket studio-workstation-releases-401233741942-us-east-1 \
  --prefix releases/v0.1.3/res-2026.06 \
  --user workstation \
  --output ~/studio-release/workstation-gpu-review.json

aws cloudformation validate-template \
  --template-body file://$HOME/studio-release/workstation-gpu-review.json \
  --region us-east-1
```

No EC2 deployment occurs. The GPU template requires an explicit
**validated GPU-capable AMI** at deployment time. Never deploy it
with the CPU AMI solely because the CloudFormation template validates.

## Remaining acceptance gates

1. Trace RES 2026.06 Image Builder GPU components, GRID driver
   distribution/configuration, and prebake `PREBAKING_AMI=true` path.
2. Produce a versioned GPU AMI; verify kernel module, driver, Xorg,
   OpenGL and DCV smoke tests **before** launching a workstation.
3. Confirm the standalone post-reboot runtime with `-g NVIDIA`
   doesn't depend on missing RES cluster settings.
4. Render, validate and review GPU CloudFormation using the GPU AMI.
5. Deploy a **new** test stack, keeping validated CPU v0.1.3 untouched.
6. Verify SSM, `nvidia-smi` T4, hardware `glxinfo -B` (not llvmpipe),
   DCV console READY, local login, and EC2 Stop → Start reconnection.
7. Only then expose `--profile gpu` in the Studio CLI and publish
   any changed RES-derived application archives as a new immutable
   release. GPU EC2 costs can be significant.

Do not alter the existing CPU stack, AMI or running instance.
