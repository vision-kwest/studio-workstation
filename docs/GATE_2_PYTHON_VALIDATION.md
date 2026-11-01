# Gate 2 — RES 2026.06 Python import and dependency validation specification

> **Historical Gate 2 validation specification:** Its test approvals, import questions and `pending` reports reflect an early review checkpoint. The RES-derived runtime was implemented and tested after this document; the GPU CLI acceptance completed in merged PR #65. For current behavior and user commands see [runtime provenance](../runtime/README.md) and [Studio CLI](studio-cli.md). Retain this document as traceability for safe import/testing boundaries.

**Status: TEST SPECIFICATION ONLY.** No tests have been executed; no provisioning changes, EC2 launches, AWS role modifications or implementation are authorized. This is a reviewable non-deploying test contract.

## Pinned evidence and inputs

- Upstream source: [aws/res at `2026.06`](https://github.com/aws/res/tree/2026.06), resolved commit `1f526a241fb293a29fb018f08ccf81c100c05a42`.
- Original application artifact: `s3://research-engineering-studio-us-east-1/releases/2026.06/idea-virtual-desktop-2026.06.tar.gz`
- Observed SHA-256: `f5d2297537bda746fe74411c18140bcd0f2d4322852fa9d32ce7606a93aed7a8`.
- Original installation archive SHA-256: `2e3b0e4b198582ea03b1926aaaf76da21688f2e6809ad4d153b95a9417c72658`.
- Artifact provenance/access and caveats: [Gate 1](GATE_1_RES_ARTIFACTS.md).
- Outer application archive has `idea-bootstrap-lib.tar.gz`, `idea-virtual-desktop-lib.tar.gz`, `library-lib.tar.gz`, `idea-sdk-lib.tar.gz`, `idea-data-model-lib.tar.gz`, `datamodel-lib.tar.gz`, and `requirements.txt`.

**Scope:** Ubuntu 24.04 / Python runtime package imports and dependency behavior. Import success is not a DCV login test. Source review alone cannot prove independent importability.

## Source-verified entry points

| Source path under `source/idea` | Function(s) | Observable behavior / dependency |
|---|---|---|
| `idea-virtual-desktop/src/ideavirtualdesktop/app/linux/bootstrap.py` | `run()` | Local host setup plus RES-only storage, directory, tags and Cognito; calls GNOME/DCV when `IDEA_SESSION_ID != NONE`; uses configuration lock and `post_reboot.run()` |
| `idea-virtual-desktop/src/ideavirtualdesktop/app/linux/post_reboot.py` | `run()` | Writes host-ready lock, creates RES idle-check cron, updates RES session host/state, configures automatic DCV console and polls readiness |
| `idea-bootstrap/src/ideabootstrap/dcv/dcv.py` | `configure()`, `configure_automatic_console_session()`, `poll_dcv_session_ready()` | Orchestrates Wayland, DCV host, GL, Xorg, DCV service; console config calls RES settings, permissions generator and session reporting |
| `idea-bootstrap/src/ideabootstrap/dcv/linux/dcv_host.py` | `configure()`, `_configure_dcv_conf()` | Local storage root; `dcv.conf` uses internal ALB endpoint and DynamoDB idle timeout settings |
| `idea-bootstrap/src/ideabootstrap/dcv/linux/gl.py` | `configure()`, `_is_gpu_instance_type()` | **Hidden RES lookup**: `cluster_settings.get_setting("global-settings.gpu_settings.instance_families")` before GL configuration |
| `idea-bootstrap/src/ideabootstrap/dcv/linux/x_server.py` | `configure()` | Skips for `SESSION_TYPE=VIRTUAL`; otherwise isolates graphical target and checks `SI:localuser:dcv` X authorization |
| `idea-bootstrap/src/ideabootstrap/dcv/linux/start_dcv_service.py` | `configure()`, `is_dcvserver_ready()` | Writes original DCV service unit, reloads systemd, restarts DCV; readiness checks local service and CLI |
| `idea-virtual-desktop/src/ideavirtualdesktop/app/app_main.py` | `configure_environment()` | Calls RES cluster settings for cluster name/home/proxy before `ResApp` lifecycle |
| `idea-virtual-desktop/src/ideavirtualdesktop/app/virtual_desktop_app.py` | `app_initialize()` | DynamoDB SSSD subscription, then `linux_bootstrap.run()` |

## Non-deploying validation sequence

### T0 — Integrity and safe inspection

**Inputs:** original archive bytes from Gate 1, no credentials in test files.

- Recompute SHA-256 and require exact match with recorded Gate 1 hash; stop on mismatch.
- List archive members without execution. Check for traversal (`..`), absolute paths, symlink targets, unexpected file types and size expansion before extracting.
- Inspect nested archives and metadata for Python version, packaging format, entry points, declared requirements and license notices.
- Never execute `setup.py` or install dependencies just to list contents. Avoid downloading unpinned dependencies.
- **Pass:** artifact identity confirmed and all required modules and package metadata identified. **Fail:** mismatch, missing files or unsafe archive paths.

### T1 — Static import closure

Without importing application modules, build a module dependency map from Python AST and package metadata for:

- `ideavirtualdesktop.app.linux.bootstrap`
- `ideavirtualdesktop.app.linux.post_reboot`
- `ideabootstrap.dcv.dcv`
- `ideabootstrap.dcv.linux.dcv_host`
- `ideabootstrap.dcv.linux.gl`
- `ideabootstrap.dcv.linux.x_server`
- `ideabootstrap.dcv.linux.start_dcv_service`
- `res.resources.cluster_settings`, `res.resources.dcv.session_permissions`, `res.utils.logging_utils`

Classify each dependency: Python standard library, packaged RES library, third-party requirement, local executable, environment/file, AWS API/RES service. Flag module-import-time effects separately from function-call-time effects.

**Pass:** every import resolved to a packaged module or pinned declared requirement, or explicitly classified as unresolved. **Fail:** silent implicit import dependencies or claims of isolation without evidence.

### T2 — Import-only isolated smoke test (requires separate test-run approval)

Use an ephemeral non-AWS, non-privileged Python environment with exact supported Python version and pinned dependencies, no AWS credentials, no instance metadata access, no network access, no writable system paths. Capture imports, errors and module-import-time side effects. Do not call `run()`, `configure()`, or shell commands.

- **Pass:** imports complete without contacting AWS/RES or mutating host state; all imported package versions recorded.
- **Fail:** missing dependencies, RES service calls, side effects or required ambient credentials. Document the precise failing import.

This is a **test plan**, not a test already executed.

### T3 — Pure dependency-injection / mocked behavior test (requires separate test-run approval)

In an isolated test harness only, stub all external effects: `subprocess.run/check_output`, `os.system`, `os.chown`, `os.makedirs`, file writes, time/sleep, boto3 clients, metadata endpoints, S3/DynamoDB calls, credential broker, Supervisor, cron and systemd. Use temporary paths. Never run the original `bootstrap.run()` unguarded on a real host.

- Record the exact order of DCV configuration: disable Wayland → DCV host → GL → Xorg → start DCV service.
- Record first-run vs subsequent-run semaphore paths without creating `/root/bootstrap` on the host.
- Check the local-user/session-owner and `SESSION_TYPE` contracts for CPU and GPU scenarios.
- Prove which RES cluster settings and session updates are requested. Check that a proposed substitution is restricted to D-001 through D-005.
- Capture logged exceptions and require explicit assertions for the expected service and session state; a successful function return alone is not enough.
- **Pass:** original order and behavior demonstrated under mocks, with precise RES-only edges isolated. **Fail:** hidden AWS calls, changed ordering, unhandled imports, or readiness falsely inferred from a lock.

### T4 — Negative cases

Exercise: missing `IDEA_SESSION_ID`, nonexistent `IDEA_SESSION_OWNER`, `SESSION_TYPE=VIRTUAL` vs console, absent `RES_BASE_OS`, missing cluster-settings table, unavailable DCV CLI, unavailable Xorg, failing service restart, malformed permissions file, and readiness timeout. Include GPU-family detection without DynamoDB.

**Pass:** failure modes explicitly observed and classified; no false-positive readiness, no host modification. **Fail:** silent failures incorrectly treated as success.

## Environment and filesystem contract to review

| Input | Source use | Standalone proposal / validation |
|---|---|---|
| `RES_BASE_OS` | DCV branch selection | `ubuntu2404`, not just `24.04` |
| `IDEA_SESSION_ID` | Enables DCV bootstrap, DCV auth/session | Non-`NONE` deterministic local identifier |
| `IDEA_SESSION_OWNER` | DCV storage root and console owner | Existing local Linux account |
| `SESSION_TYPE` | Xorg branch | `CONSOLE` for physical/console desktop; validate actual upstream enum |
| `INSTANCE_FAMILY`, `MACHINE` | GL GPU detection | Preserve original GPU/CPU branch; remove only DynamoDB list lookup |
| `IDEA_APP_DEPLOY_DIR` | Logging config | Writable test-only location |
| `AWS_REGION`, `AWS_DEFAULT_REGION` | Retained AWS calls | No live AWS calls during tests |
| `IDEA_CLUSTER_HOME`, `ON_VDI_CONFIGURED_COMMANDS`, `RERUN_ON_REBOOT` | Optional custom scripts and reentry | Verify required defaults and no unintended execution |
| `/etc/launch_script_environment` | Sourced after config | Preserve interface; no real writes in tests |
| `/root/bootstrap/reboot_required.txt` | Reboot state | Preserve exact semantics; test in temp filesystem |
| `/root/bootstrap/semaphore/linux_vdi_config.lock` | Config finished | Preserve exact semantics |
| `/root/bootstrap/semaphore/linux_vdi_config_host_ready.lock` | Host-ready | Not equivalent to successful DCV login |

## Security and acceptance constraints

- No direct `bootstrap.run()` invocation on CloudShell, an EC2 workstation or developer machine.
- No AWS policy changes, infrastructure, secrets, login credential generation, or external service requests.
- Original archive packages remain unmodified; a later adaptation must be a separate, reviewable minimal diff.
- Source-level audit cannot establish TLS, PAM, browser login, Xorg acceleration or NVIDIA behavior; these remain later runtime acceptance gates.
- A Python import pass is **necessary but insufficient** for Gate 2 closure.

## Expected Gate 2 report

1. Exact package inventory and hash checks.
2. Import graph and classification, including import-time side effects.
3. Environment and filesystem contract table, with exact source line references.
4. Mocked call-order traces for first and subsequent boot, CPU/GPU, and console/virtual branches.
5. Failure matrix and unresolved dependencies.
6. Proposed smallest upstream file/function changes mapped to D-001 through D-005; D-006 and D-007 unchanged.
7. Explicit decision: source-audit complete / tests approved / tests executed / runtime acceptance pending. Never conflate these.

**Current disposition:** specification drafted; import-only and mocked tests have **not** run. Gate 2 is still open. Implementation authorization remains withheld.
