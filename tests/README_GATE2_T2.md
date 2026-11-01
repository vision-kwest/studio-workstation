# Gate 2 T2 — Import-only harness

> **Historical isolated-import validation procedure:** This is an intentionally non-deploying Gate 2 research tool and does not describe the current GPU workstation CLI. The standalone RES/DCV path, GPU user login and GPU CLI stop/start persistence were subsequently validated. Preserve the warning against running the isolated import harness on a live workstation. Current operation: [Studio CLI](../docs/studio-cli.md) and [runtime provenance](../runtime/README.md).

**Experimental test harness; not a standalone workstation implementation.** This is a separate step from the merged [Gate 2 specification](../docs/GATE_2_PYTHON_VALIDATION.md).

`gate2_import_only.py` verifies the recorded SHA-256 of the original `idea-virtual-desktop-2026.06.tar.gz`, safely unpacks its six nested source archives to a temporary directory, and attempts imports of seven original RES Python modules in a separate Python process. It reports each failure instead of masking it. The temporary files are deleted on exit. It never invokes `bootstrap.run()` or DCV configuration.

## Safety boundary

Run only in a disposable, network-isolated environment with no AWS credentials, no mounted AWS instance role, no SSM agent, no host root filesystem mounts, and no privileged access. The Python audit hook blocks socket creation and process execution as defense in depth, but is **not a replacement for OS-level network/process isolation**. Do not run this on a live workstation or AWS CloudShell.

## Invocation (after placing the original archive in the isolated runner)

```bash
python3 -B tests/gate2_import_only.py \
  --archive /path/to/idea-virtual-desktop-2026.06.tar.gz \
  --report gate2-t2-report.json
```

The harness does **not** install third-party requirements. Missing dependencies are recorded as failures, not considered evidence that RES requires a live control plane. Before a conclusive T2 pass, separately provision an isolated environment with the original pinned Python dependencies and confirm the package's Python version support. Never run unreviewed `setup.py` on a privileged host.

The result must be inspected for missing packages, import-time AWS/network/process side effects, and configuration dependencies. Even a passing T2 import-only run does **not** prove standalone initialization, DCV login, or GPU readiness. T3/T4 remain pending.

**Current state:** harness committed for review; no T2 execution claimed.
