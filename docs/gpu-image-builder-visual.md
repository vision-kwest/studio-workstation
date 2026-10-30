# CPU and GPU image building — visual guide

The **CPU Image Builder pipeline** is our proven reference. The **GPU pipeline** follows its staged GDM-first/reboot pattern, with the original RES NVIDIA GRID path.

```mermaid
flowchart TD
  Parent["Canonical Ubuntu 24.04 parent AMI"] --> GDM["GDM-first 0.5.0 / reboot"]
  GDM --> Desktop{"CPU or GPU recipe?"}
  Desktop -->|CPU| CPUD["CPU desktop + dummy Xorg / reboot"]
  Desktop -->|GPU| GPUD["GPU desktop, no dummy Xorg / reboot"]
  GPUD --> NVIDIA["Original RES NVIDIA GRID installer / reboot"]
  CPUD --> CPUDCV["DCV server / reboot"]
  NVIDIA --> GPUDCV["DCV + nice-dcv-gl / reboot"]
  CPUDCV --> CPUTEST["CPU console smoke test"]
  GPUDCV --> GPUTEST["T4 + Xorg + hardware OpenGL + DCV tests"]
  CPUTEST --> CPUAMI["Validated CPU AMI"]
  GPUTEST --> GPUAMI["GPU AMI only if tests pass"]
```

## Inputs and outputs

| Input | Role |
|---|---|
| Parent Ubuntu AMI | OS baseline; a stale base triggered the intentional exit-40 package upgrade guard |
| Image recipe | Ordered, versioned Image Builder components |
| Infrastructure configuration | Temporary build instance type and instance profile |
| Distribution configuration | AMI naming and distribution region |
| RES installation archive | Original bootstrap, GPU driver installer and DCV integration |
| Pinned NVIDIA GRID dependency | Exact S3 object version and expected SHA-256 |

| Output | Meaning |
|---|---|
| Image Builder execution | Build and test status, component logs |
| Custom AMI | Created only after build stages complete |
| GPU workstation | Separate CloudFormation deployment and interactive DCV validation required |

## Status of the first GPU builds

- GPU 0.1.2/1 failed in `InstallDesktop` with intentional exit 40 (core OS package upgrade guard).
- GPU 0.1.3/1 was started with refreshed Canonical Ubuntu 24.04 AMI `ami-0d6dd9e8265d48906`. **Its outcome is not yet confirmed in this documentation.**
- No successful GPU AMI or GPU DCV login should be inferred from a pipeline's `BUILDING` state.

See [RES GPU source trace](res-gpu-imagebuilder-audit.md) and [GPU profile](gpu-workstation-profile.md).
