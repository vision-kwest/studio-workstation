#!/usr/bin/env python3
"""Issue #43: activate one AWCP-exported certificate generation for Amazon DCV.

The initial enrollment was validated on the Issue #43 CPU workstation. Future
changes to this hook require offline tests and a separately approved rollout.
It expects the two enrolled DCV links through one versioned-generation pointer.

AWCP should write to /var/lib/studio/dcv-acm/ (NOT directly into /etc/dcv).
This script is an AWCP refresh_command, invoked through its exact sudoers rule.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import pwd
import re
import secrets
import stat
import logging
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
GEN_NAME = re.compile(r"^gen-(?:initial-)?[a-z0-9_-]{6,64}$")
# A successful rotation retains exactly the active and preceding managed
# generation; unrecognized or unexpectedly owned paths are left untouched.
RETAIN_GENERATIONS = 2


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
    if not GEN_NAME.fullmatch(target.name):
        raise RuntimeError("Invalid active certificate generation name")
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


def same_file_contents(first: Path, second: Path) -> bool:
    """Compare complete PEM bytes without exposing private key material."""
    try:
        with first.open("rb") as a, second.open("rb") as b:
            while True:
                x, y = a.read(65536), b.read(65536)
                if x != y:
                    return False
                if not x:
                    return True
    except (OSError, ValueError):
        # Never call an unchanged refresh safe if the current pair is missing
        # or unreadable: the next path must either rotate or fail closed.
        return False


def current_pair_matches(source: Path, dcv: Path) -> bool:
    return all(
        same_file_contents(source / staged, dcv / installed)
        for staged, installed in (("fullchain.pem", "dcv.pem"),
                                  ("privkey.pem", "dcv.key"))
    )


def validate_console_without_restart():
    """Do not hide a failed DCV console just because certificates are unchanged."""
    if command(["systemctl", "is-active", "dcvserver"]) != "active":
        raise RuntimeError("DCV service is not active")
    info = json.loads(command(["dcv", "describe-session", "console", "--json"]))
    if not isinstance(info, dict) or info.get("id") != "console" or info.get("status") != "running":
        raise RuntimeError("DCV console not ready")


def prune_managed_generations(store: Path, active: str, previous: str,
                              uid: int, gid: int) -> tuple[int, int]:
    """Best-effort bounded retention, never follow links or delete unknowns.

    Operates relative to O_NOFOLLOW directory descriptors, validating exact
    two-file contents, owner and restrictive modes. Preserve active and the
    immediate rollback generation even when a third generation exists.
    Unexpected entries are skipped for manual inspection, not force-deleted.
    No cleanup is attempted before a successful DCV restart.
    """
    removed = skipped = 0
    flags = os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_NOFOLLOW", 0)
    with os.scandir(store) as iterator:
        names = [entry.name for entry in iterator]
    store_fd = os.open(store, flags)
    try:
        root = os.fstat(store_fd)
        if not stat.S_ISDIR(root.st_mode) or root.st_uid != uid or root.st_gid != gid or stat.S_IMODE(root.st_mode) != 0o700:
            raise RuntimeError("DCV generation store has unexpected owner or permissions")
        preserved = {active, previous}
        for name in names:
            if name in preserved:
                continue
            if not GEN_NAME.fullmatch(name):
                skipped += 1
                continue
            try:
                gen_fd = os.open(name, flags, dir_fd=store_fd)
            except OSError:
                skipped += 1
                continue
            try:
                gen_stat = os.fstat(gen_fd)
                if (gen_stat.st_uid != uid or gen_stat.st_gid != gid
                        or stat.S_IMODE(gen_stat.st_mode) != 0o700
                        or set(os.listdir(gen_fd)) != set(LINKS)):
                    skipped += 1
                    continue
                valid = True
                for filename in LINKS:
                    s = os.stat(filename, dir_fd=gen_fd, follow_symlinks=False)
                    if (not stat.S_ISREG(s.st_mode) or s.st_uid != uid
                            or s.st_gid != gid or stat.S_IMODE(s.st_mode) != 0o600
                            or s.st_nlink != 1):
                        valid = False
                        break
                if not valid:
                    skipped += 1
                    continue
                # unlink removes entries themselves, never follows symlinks.
                for filename in LINKS:
                    os.unlink(filename, dir_fd=gen_fd)
            finally:
                os.close(gen_fd)
            os.rmdir(name, dir_fd=store_fd)
            removed += 1
    finally:
        os.close(store_fd)
    return removed, skipped


def activate(source: Path = SOURCE, dcv: Path = DCV):
    if os.geteuid() != 0:
        raise PermissionError("DCV TLS activation requires root via exact AWCP refresh-command sudoers")
    store, previous = check_enrolled(dcv)
    cert, key = source / "fullchain.pem", source / "privkey.pem"
    assert_local_file(cert)
    assert_local_file(key)
    verify_material(cert, key)  # Before any DCV file or pointer changes.
    dcvu = pwd.getpwnam("dcv")
    if current_pair_matches(source, dcv):
        validate_console_without_restart()
        return "Certificate unchanged; DCV console ready (no restart or new generation)"
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
    # Cleanup may not interrupt an already-working console or roll back the
    # newly activated certificate; log an operator-actionable warning instead.
    try:
        removed, skipped = prune_managed_generations(
            store, generation.name, previous.name, dcvu.pw_uid, dcvu.pw_gid)
        if skipped:
            logging.warning("DCV TLS retention left %d unfamiliar or unsafe entries for review", skipped)
        logging.info("DCV TLS retention pruned %d old managed generations", removed)
    except (OSError, RuntimeError) as exc:
        logging.warning("DCV TLS retention incomplete (active cert unchanged): %s", exc)
    return "Activated a verified DCV certificate generation; console ready"


def enroll_default(source: Path = SOURCE, dcv: Path = DCV):
    """Supervised first enrollment when DCV uses its built-in self-signed cert.

    Does not replace existing files: both target paths and generation directory
    must be absent. On service failure the links are removed and the default
    built-in self-signed certificate is restored by a second DCV restart.
    """
    if os.geteuid() != 0:
        raise PermissionError("First enrollment requires root")
    if dcv.is_symlink() or not dcv.is_dir():
        raise RuntimeError("Invalid DCV directory")
    if any((dcv / name).exists() or (dcv / name).is_symlink()
           for name in (*LINKS, ACTIVE_NAME, STORE_NAME)):
        raise RuntimeError("Refusing first enrollment over preexisting DCV certificate paths")
    cert, key = source / "fullchain.pem", source / "privkey.pem"
    assert_local_file(cert)
    assert_local_file(key)
    verify_material(cert, key)
    dcvu = pwd.getpwnam("dcv")
    store = dcv / STORE_NAME
    store.mkdir(mode=0o700)
    os.chown(store, dcvu.pw_uid, dcvu.pw_gid)
    generation = store / ("gen-initial-" + secrets.token_hex(8))
    try:
        generation.mkdir(mode=0o700)
        os.chown(generation, dcvu.pw_uid, dcvu.pw_gid)
        write_private(cert, generation / "dcv.pem", dcvu.pw_uid, dcvu.pw_gid)
        write_private(key, generation / "dcv.key", dcvu.pw_uid, dcvu.pw_gid)
        verify_material(generation / "dcv.pem", generation / "dcv.key")
        (dcv / ACTIVE_NAME).symlink_to(f"{STORE_NAME}/{generation.name}")
        for name, link in LINKS.items():
            (dcv / name).symlink_to(link)
        restart_and_check()
    except Exception:
        # Never erase anything unexpected: only delete our verified symlinks.
        for name, link in LINKS.items():
            p = dcv / name
            if p.is_symlink() and os.readlink(p) == link:
                p.unlink()
        p = dcv / ACTIVE_NAME
        if p.is_symlink() and os.readlink(p) == f"{STORE_NAME}/{generation.name}":
            p.unlink()
        # Revert to DCV built-in self-signed certificate if first install failed.
        restart_and_check()
        raise
    return "First custom certificate installed; console ready"


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--enroll-default", action="store_true",
                        help="Supervised first install: only when DCV has no custom cert files")
    args = parser.parse_args(argv)
    print(enroll_default() if args.enroll_default else activate())


if __name__ == "__main__":
    main()
