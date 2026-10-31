# Task 5 review: original RES 2026.06 runtime coordinator

**Status: DRAFT / NOT DEPLOYABLE.** This branch adds a minimal *alternative coordinator* and mocked tests, but does not replace the existing `runtime/overrides/` approach on `main`. **Do not enable the proposed systemd service or merge until the two approaches are reconciled.**

## Important pre-existing implementation

The repository already has `runtime/README.md` documenting source-derived RES overrides, archive reconstruction and a Supervisor-driven bootstrap candidate. That implementation preserves the original cloud-init and reboot lifecycle and includes unresolved dependency, package, IAM and DCV acceptance gates. This branch is intentionally additive and review-only; we must not accidentally deploy both bootstrap paths.

## Proposed minimal reuse

Original RES tag: [aws/res 2026.06](https://github.com/aws/res/tree/2026.06). This branch's `studio_res_runtime.py` calls original `disable_wayland_protocol.configure()`, `gl._configure_gl()`, `x_server.configure()` and `_x_server_validated()`, `dcv_host.apply_automatic_console_session_config()`, `start_dcv_service.is_dcvserver_ready()`, and `dcv.poll_dcv_session_ready()`. It avoids `ResApp`, `bootstrap.run()`, `post_reboot.run()` and RES external authentication.

## Provenance gate

Private S3 release manifest records derived `virtual-desktop.tar.gz` SHA-256 `4d9b89d149d2d984193e346ef0c48294a7a976dd32208a6e06ac365d4581c4a3` and derived `installation-scripts.tar.gz` SHA-256 `813259748335482b1877192a61a008b32e613038ee831305d9e4767c51c23ab7`. The extracted Python files have **not** yet been byte-compared against tag 2026.06. Neither this PR nor the source metadata alone establishes that the runtime packages are importable on the built AMI.

## Safety and testing boundaries

- No AWS mutation, build, deployment or instance launch.
- Preserve local DCV `authentication="system"`; reject external `auth-token-verifier`.
- Fail closed if NVIDIA, Xorg/DCV access, console or GPU OpenGL is unavailable.
- The proposed owner-only permissions file is a **candidate**; validate with the exact DCV release before use.
- Original `gl._configure_gl()` writes rc-local; idempotency and service ordering require live validation.
- The original RES modules may import RES cluster/database dependencies even if the called functions don't use them. Import isolation must be tested with the pinned archive.
- Existing `runtime/overrides/` + Supervisor design may be preferable to a second systemd bootstrap. **Task 6 should compare both paths and choose one**, not install both.
- Tests in `tests/test_studio_res_runtime.py` are mocked only; they are not GPU or DCV integration tests.

Run offline:

```bash
python3 -m unittest discover -s tests -p 'test_studio_res_runtime.py' -v
python3 -m py_compile runtime/studio_res_runtime.py
```

See [Issue #60](https://github.com/vision-kwest/studio-workstation/issues/60) for the ordered audit and review gates.
