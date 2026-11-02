#!/usr/bin/env python3
"""Issue #43: activate one AWCP-exported certificate generation for Amazon DCV.

This hook is OFFLINE-REVIEWED ONLY: it is not installed or executed on EC2.
It requires a separately supervised enrollment that prepares exact two DCV
symlinks pointing through the common versioned-generation pointer.

AWCP should write to /var/lib/studio/dcv-acm/ (NOT directly into /etc/dcv).
This script is an AWCP refresh_command, invoked through its exact sudoers rule.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import pwd
import secrets
import shutil
import subprocess
import tempfile
import time

HOST = "dcv.kwesidavis.com"
SOURCE = Path("/var/lib/studio/dcv-acm")
DCV = Path("/etc/dcv")
STORE_NAME = "studio-tls-generations"
ACTIVE_NAME = "studio-tls-current"
LINKS = {"dcv.pem": f"{ACTIVE_NAME}/dcv.pem",
         "dcv.key": f"{ACTIVE_NAME}/dcv.key"}


def command(argv):
    return subprocess.run(argv, capture_output=True, text=True, check=True).stdout.strip()


def verify_material(cert: Path, key: Path):
    """Require nonexpired server identity and a matching public/private key."""
    command(["openssl", "x509", "-in", str(cert), "-noout", "-checkend", "172800"])
    match = command(["openssl", "x509", "-in", str(cert), "-noout", "-checkhost", HOST])
    if f"Hostname {HOST} does match certificate" not in match:
        raise ValueError("Certificate hostname mismatch")
    cert_pub = command(["openssl", "x509", "-in", str(cert), "-pubkey", "-noout"])
    key_pub = command(["openssl", "pkey", "-in", str(key), "-pubout"])
    if hashlib.sha256(cert_pub.encode()).digest() != hashlib.sha256(key_pub.encode()).digest():
        raise ValueError("Certificate/key mismatch")


def check_enrolled(dcv: Path):
    """Refuse to modify the RES/DCV default certificate files before manual enrollment."""
    if dcv.is_symlink() or not dcv.is_dir():
        raise RuntimeError("Invalid DCV configuration directory")
    for name, expected in LINKS.items():
        link = dcv / name
        if not link.is_symlink() or os.readlink(link) != expected:
            raise RuntimeError(f"{name} is not enrolled; supervised backup/enrollment required")
    active = dcv / ACTIVE_NAME
    store = dcv / STORE_NAME
    if not store.is_dir() or store.is_symlink() or not active.is_symlink():
        raise RuntimeError("Versioned DCV TLS store not enrolled")
    target = Path(os.readlink(active))
    if target.is_absolute() or len(target.parts) != 2 or target.parts[0] != STORE_NAME or target.parts[1] in (".", ".."):
        raise RuntimeError("Invalid active certificate generation")
    return store, target


def assert_local_file(path: Path):
    if path.is_symlink() or not path.is_file():
        raise RuntimeError(f"Expected a regular non-symlink PEM file: {path.name}")


def write_private(src: Path, dst: Path, uid: int, gid: int):
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    fd = os.open(dst, flags, 0o600)
    try:
        os.fchmod(fd, 0o600)
        os.fchown(fd, uid, gid)
        with src.open("rb") as handle, os.fdopen(fd, "wb", closefd=False) as out:
            shutil.copyfileobj(handle, out, length=64 * 1024)
            out.flush()
            os.fsync(fd)
    finally:
        os.close(fd)


def restart_and_check():
    """A deliberate controlled DCV restart, not a RES bootstrap operation."""
    command(["systemctl", "restart", "dcvserver"])
    for _ in range(12):
        try:
            if command(["systemctl", "is-active", "dcvserver"]) == "active":
                session = command(["dcv", "describe-session", "console", "--json"])
                details = json.loads(session)
                if isinstance(details, dict) and details.get("status") == "running":
                    return
        except subprocess.CalledProcessError:
            pass
        time.sleep(2)
    raise RuntimeError("DCV service or automatic console did not become ready after certificate update")


def switch(dcv: Path, new_target: str):
    tmp = dcv / (".studio-tls-link-" + secrets.token_hex(12))
    try:
        tmp.symlink_to(new_target)
        os.replace(tmp, dcv / ACTIVE_NAME)
    finally:
        if tmp.is_symlink():
            tmp.unlink()


def activate(source: Path = SOURCE, dcv: Path = DCV):
    if os.geteuid() != 0:
        raise PermissionError("DCV TLS activation requires root via exact AWCP refresh-command sudoers")
    store, previous = check_enrolled(dcv)
    cert, key = source / "fullchain.pem", source / "privkey.pem"
    assert_local_file(cert)
    assert_local_file(key)
    verify_material(cert, key)  # Before any DCV file or pointer changes.
    dcvu = pwd.getpwnam("dcv")
    # The store was created by supervised enrollment, not by the AWS agent.
    generation = Path(tempfile.mkdtemp(prefix="gen-", dir=store))
    new_target = f"{STORE_NAME}/{generation.name}"
    switched = False
    try:
        os.chmod(generation, 0o700)
        os.chown(generation, dcvu.pw_uid, dcvu.pw_gid)
        write_private(cert, generation / "dcv.pem", dcvu.pw_uid, dcvu.pw_gid)
        write_private(key, generation / "dcv.key", dcvu.pw_uid, dcvu.pw_gid)
        verify_material(generation / "dcv.pem", generation / "dcv.key")
        # One atomic pointer swap activates the pair. DCV may not notice a
        # symlink swap automatically: intentionally restart and verify console.
        switch(dcv, new_target)
        switched = True
        restart_and_check()
    except Exception:
        if switched:
            switch(dcv, str(previous))
            # Try to recover the prior working console. Never claim success
            # if rollback/restart also fails.
            restart_and_check()
        raise
    return "Activated a verified DCV certificate generation; console ready"


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    args = parser.parse_args(argv)
    print(activate())


if __name__ == "__main__":
    main()
