#!/usr/bin/env python3
"""Minimal standalone runtime coordinator for pinned AWS RES 2026.06.

No RES control-plane bootstrap, external authentication, or package installation.
Run only on an already provisioned Ubuntu 24.04 GPU workstation.
This module deliberately imports RES lazily, after validating environment.
"""
from __future__ import annotations

import configparser
import importlib
import logging
import os
from pathlib import Path
import pwd
import subprocess
import sys

LOG = logging.getLogger("studio-res-runtime")
DCV_CONF = Path("/etc/dcv/dcv.conf")
PERMISSIONS = Path("/etc/dcv/console/idea.perm")
EXPECTED_OWNER = "workstation"


class ReadinessError(RuntimeError):
    pass


def run_command(args, *, env=None, timeout=30):
    return subprocess.run(args, check=True, text=True, capture_output=True, env=env, timeout=timeout)


def assert_system_auth(path=DCV_CONF):
    cfg = configparser.ConfigParser()
    if not cfg.read(path):
        raise ReadinessError(f"Missing DCV configuration: {path}")
    if cfg.get("security", "authentication", fallback="").strip('"') != "system":
        raise ReadinessError("DCV system authentication missing; refusing to change security policy")
    if cfg.has_option("security", "auth-token-verifier"):
        raise ReadinessError("RES external auth verifier must not be configured")
    return cfg


def ensure_permissions(path=PERMISSIONS):
    # RES uses %owner% with its permission profiles. Standalone grants only
    # the console owner the DCV built-in permission set; no guest/admin aliases.
    content = "[permissions]\n%owner% allow builtin\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and path.read_text() != content:
        raise ReadinessError(f"Unexpected permissions file; review manually: {path}")
    if not path.exists():
        path.write_text(content)
    path.chmod(0o644)
    return str(path)


def validate_environment(environ=None):
    env = dict(os.environ if environ is None else environ)
    required = {
        "RES_BASE_OS": "ubuntu2404",
        "MACHINE": "x86_64",
        "INSTANCE_FAMILY": "g4dn",
        "SESSION_TYPE": "CONSOLE",
        "IDEA_SESSION_OWNER": EXPECTED_OWNER,
    }
    for name, expected in required.items():
        if env.get(name) != expected:
            raise ReadinessError(f"{name} must be {expected!r}")
    pwd.getpwnam(EXPECTED_OWNER)
    if env.get("IDEA_SESSION_ID") in (None, "", "NONE"):
        raise ReadinessError("IDEA_SESSION_ID must identify local console, not NONE")
    return env


def load_res_modules():
    """Import only the RES components we actually invoke, after env validation.

    Importing the RES modules still requires their upstream Python dependencies.
    This is an explicit packaging gate, not a shim or monkeypatch.
    """
    names = {
        "wayland": "ideabootstrap.dcv.linux.disable_wayland_protocol",
        "gl": "ideabootstrap.dcv.linux.gl",
        "xorg": "ideabootstrap.dcv.linux.x_server",
        "host": "ideabootstrap.dcv.linux.dcv_host",
        "service": "ideabootstrap.dcv.linux.start_dcv_service",
        "dcv": "ideabootstrap.dcv.dcv",
    }
    return {key: importlib.import_module(name) for key, name in names.items()}


def assert_xorg_ready(xorg):
    if not xorg._x_server_validated():
        raise ReadinessError("RES Xorg validation timed out or DCV X access missing")


def assert_gpu_gl():
    ps = run_command(["ps", "-eo", "args"])
    import re
    matches = re.findall(r"X(?:org)?\s+.*?-auth\s+([^\s]+)", ps.stdout)
    if not matches:
        raise ReadinessError("Xorg -auth file not found")
    auth = Path(matches[0])
    if not auth.is_file():
        raise ReadinessError("Xorg Xauthority file missing")
    env = dict(os.environ, DISPLAY=":0", XAUTHORITY=str(auth),
               __GLX_VENDOR_LIBRARY_NAME="nvidia")
    info = run_command(["glxinfo", "-B"], env=env).stdout
    renderer = next((s for s in info.splitlines() if "OpenGL renderer string:" in s), "")
    if "T4" not in renderer or any(x in renderer.lower() for x in ("llvmpipe", "softpipe")):
        raise ReadinessError(f"Unexpected GPU renderer: {renderer}")


def initialize(modules=None):
    validate_environment()
    run_command(["nvidia-smi", "-L"])
    assert_system_auth()
    m = load_res_modules() if modules is None else modules

    # Preserve the original RES lifecycle ordering, but deliberately omit
    # dcv_host.configure() (overwrites system auth with RES ALB verifier).
    m["wayland"].configure()
    # _configure_gl is the original 2026.06 implementation; calling it after
    # explicit GPU preflight avoids its cluster_settings/DynamoDB GPU-family lookup.
    # It creates rc-local.service and configures dcvgladmin for Ubuntu.
    if not run_command(["sh", "-c", "command -v dcvgladmin"]).stdout.strip():
        raise ReadinessError("dcvgladmin missing")
    m["gl"]._configure_gl("ubuntu2404")
    m["xorg"].configure()
    assert_xorg_ready(m["xorg"])
    assert_system_auth()

    storage = Path("/home") / EXPECTED_OWNER / "storage-root"
    storage.mkdir(parents=True, exist_ok=True)
    user = pwd.getpwnam(EXPECTED_OWNER)
    os.chown(storage, user.pw_uid, user.pw_gid)
    permissions = ensure_permissions()
    m["host"].apply_automatic_console_session_config(EXPECTED_OWNER, str(storage), permissions)
    assert_system_auth()

    if not m["service"].is_dcvserver_ready(300, 5):
        raise ReadinessError("DCV server did not become ready")
    if m["dcv"].poll_dcv_session_ready(300, 5) != "READY":
        raise ReadinessError("DCV console did not become ready")
    assert_gpu_gl()
    LOG.info("RES 2026.06 standalone GPU DCV console READY")


def main():
    logging.basicConfig(level=logging.INFO)
    try:
        initialize()
    except (Exception, SystemExit):
        LOG.exception("Standalone RES runtime initialization failed")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
