"""Opt-in CPU/GPU EFS package mount, patterned on RES 2026.06 Linux EFS flow.

Unlike the full RES storage configure(), no DynamoDB, SSH keys, FSx, S3
credential broker or global mount -a. Never invoked by workstation bootstrap.
Requires a reviewed EFS IAM access point and amazon-efs-utils on the host.
"""
import fcntl
import os
from pathlib import Path
import re
import shutil
import subprocess
import time

MOUNTPOINT = Path("/opt/studio/packages")
FSTAB = Path("/etc/fstab")
MOUNTINFO = Path("/proc/self/mountinfo")
RETRY_DELAYS = (2, 4)
PROFILES = ("cpu", "gpu")


def _entry(filesystem_id, access_point_id, profile):
    if profile not in PROFILES:
        raise ValueError("EFS package mount is limited to CPU/GPU workstation profiles")
    if not re.fullmatch(r"fs-[0-9a-f]{8,17}", filesystem_id):
        raise ValueError("Invalid EFS filesystem ID")
    if not re.fullmatch(r"fsap-[0-9a-f]{8,17}", access_point_id):
        raise ValueError("EFS access point is required")
    # RES EFS fstab/idempotence semantics, but use the EFS mount helper
    # rather than its unauthenticated NFS4 path to require TLS and IAM.
    opts = ("_netdev,nofail,x-systemd.automount,ro,tls,iam,"
            f"accesspoint={access_point_id}")
    return f"{filesystem_id}:/ {MOUNTPOINT} efs {opts} 0 0"


def _fstab_action(contents, entry):
    matches = []
    for line in contents.splitlines():
        fields = line.split()
        if not fields or fields[0].startswith("#"):
            continue
        if len(fields) >= 2 and fields[1].rstrip("/") == str(MOUNTPOINT):
            matches.append(fields)
    if not matches:
        return "add"
    if len(matches) == 1 and matches[0] == entry.split():
        return "present"
    raise ValueError("Conflicting or duplicate EFS mount entry; refusing changes")


def _mount_state(mountinfo):
    found = []
    for line in mountinfo.splitlines():
        if " - " not in line:
            continue
        left, right = line.split(" - ", 1)
        first, second = left.split(), right.split()
        if len(first) >= 6 and len(second) >= 2 and first[4] == str(MOUNTPOINT):
            found.append((first[5].split(","), second[0]))
    if not found:
        return False
    if len(found) != 1 or found[0][1] != "nfs4" or "ro" not in found[0][0]:
        raise ValueError("Unexpected or writable package mount; refusing EFS setup")
    return True


def _target_present(mountinfo):
    """Detect even an unsafe/partial mount before changing persistence."""
    for line in mountinfo.splitlines():
        fields = line.split(" - ", 1)[0].split()
        if len(fields) >= 5 and fields[4] == str(MOUNTPOINT):
            return True
    return False


def plan(filesystem_id, access_point_id, *, profile="cpu", fstab_text=""):
    entry = _entry(filesystem_id, access_point_id, profile)
    return {"profile": profile, "mountpoint": str(MOUNTPOINT), "fstab_entry": entry,
            "fstab_action": _fstab_action(fstab_text, entry),
            "filesystem_id": filesystem_id, "access_point_id": access_point_id,
            "transport": "EFS helper TLS + IAM", "read_only": True,
            "aws_identity_verified": False}


def configure(filesystem_id, access_point_id, *, profile="cpu",
              fstab=FSTAB, mountinfo=MOUNTINFO, runner=subprocess.run,
              sleeper=time.sleep, helper_available=None):
    """Explicitly configure/mount when called by an approved deployment.

    No connection to DCV startup. Roll back only our own newly appended fstab
    line on failure; never unmount an existing filesystem or edit other lines.
    """
    entry = _entry(filesystem_id, access_point_id, profile)
    available = (helper_available if helper_available is not None else
                 bool(shutil.which("mount.efs") or Path("/sbin/mount.efs").exists()
                      or Path("/usr/sbin/mount.efs").exists()))
    if not available:
        raise RuntimeError("amazon-efs-utils mount.efs is required")
    fstab, mountinfo = Path(fstab), Path(mountinfo)
    with fstab.open("r+", encoding="utf-8") as file:
        fcntl.flock(file, fcntl.LOCK_EX)
        original = file.read()
        action = _fstab_action(original, entry)
        mounted = _mount_state(mountinfo.read_text(encoding="utf-8"))
        if mounted:
            if action != "present":
                raise ValueError("Already mounted without matching managed fstab entry")
            return {"mounted": True, "changed": False}
        if MOUNTPOINT.is_symlink():
            raise ValueError("EFS mountpoint may not be a symlink")
        if MOUNTPOINT.exists() and (not MOUNTPOINT.is_dir() or any(MOUNTPOINT.iterdir())):
            raise ValueError("Unmanaged files at unmounted package path")
        MOUNTPOINT.mkdir(parents=True, exist_ok=True)
        appended = action == "add"
        suffix = ("" if original.endswith("\n") or not original else "\n") + entry + "\n"
        if appended:
            file.seek(0, os.SEEK_END)
            file.write(suffix)
            file.flush()
            os.fsync(file.fileno())
        try:
            for attempt in range(len(RETRY_DELAYS) + 1):
                try:
                    runner(["mount", str(MOUNTPOINT)], check=True, timeout=45)
                    if _mount_state(mountinfo.read_text(encoding="utf-8")):
                        return {"mounted": True, "changed": appended}
                    raise RuntimeError("Mount succeeded but read-only NFS4 not verified")
                except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
                    # A timed-out mount helper can finish successfully after
                    # reporting an error. Verify before retrying or rolling back
                    # our managed fstab entry; never leave a mounted target
                    # without its matching persistence configuration.
                    if _mount_state(mountinfo.read_text(encoding="utf-8")):
                        return {"mounted": True, "changed": appended}
                    if attempt == len(RETRY_DELAYS):
                        raise RuntimeError("EFS mount failed; verify mount helper, IAM and TCP/2049")
                    sleeper(RETRY_DELAYS[attempt])
        except BaseException as exc:
            if appended:
                # A failed/unsafe mount may still be present (including as
                # an autofs stub). Never orphan an active mount by removing
                # its managed fstab entry. Escalate for supervised cleanup.
                try:
                    visible = _target_present(mountinfo.read_text(encoding="utf-8"))
                except OSError as read_error:
                    raise RuntimeError(
                        "Cannot verify EFS mount after failure; manual reconciliation"
                    ) from read_error
                if visible:
                    raise RuntimeError(
                        "EFS mount state unsafe; fstab retained for manual reconciliation"
                    ) from exc
                file.seek(0)
                current = file.read()
                if current == original + suffix:
                    file.seek(0)
                    file.truncate(0)
                    file.write(original)
                    file.flush()
                    os.fsync(file.fileno())
            raise
        finally:
            fcntl.flock(file, fcntl.LOCK_UN)
