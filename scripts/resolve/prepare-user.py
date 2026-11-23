#!/usr/bin/python3
"""Initialize missing Resolve 21.2 preferences; preserve every existing file."""
import fcntl
import os
from pathlib import Path
import re
import sys
import tempfile
import xml.etree.ElementTree as ET

RESOLVE = Path("/opt/resolve")
BACKUPS = Path("/studio/projects/resolve/backups")


def _missing(paths):
    for path in paths:
        if path.is_symlink() or (path.exists() and not path.is_file()):
            raise ValueError(f"Preserve and resolve the conflicting configuration path: {path}")
    return [path for path in paths if not path.exists()]


def _assert_idle(payload):
    for process in Path("/proc").iterdir():
        if not process.name.isdecimal():
            continue
        try:
            if process.stat().st_uid != os.getuid():
                continue
            executable = (process / "exe").resolve(strict=True)
        except (OSError, RuntimeError):
            continue
        if executable == payload / "bin/resolve" or (
                executable.name == "resolve" and executable.parent.name == "bin"
                and "davinci-resolve" in executable.parts):
            raise ValueError("Close Resolve before initializing missing preferences.")


def prepare():
    home = Path.home()
    configs = home / ".local/share/DaVinciResolve/configs"
    paths = [configs / "config.dat", configs / "config.user.xml"]
    if not _missing(paths):
        return
    configs.mkdir(parents=True, exist_ok=True)
    lock_path = configs / ".studio-setup.lock"
    if lock_path.is_symlink():
        raise ValueError(f"Unexpected setup lock symlink: {lock_path}")
    with lock_path.open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        missing = _missing(paths)
        if not missing:
            return
        payload = RESOLVE.resolve(strict=True)
        if payload.name != "21.2-free":
            raise ValueError("Preference initialization is verified only for Resolve 21.2-free.")
        _assert_idle(payload)
        defaults = (payload / "share/default-config.dat").read_text()
        system = "".join(line for line in defaults.splitlines(keepends=True)
                         if not re.match(r"^Site\.1\.FS\.", line)).rstrip("\n") + "\n"
        roots = [home / ".cache/davinci-resolve", home / "Videos", Path("/studio"), "ResolveVirtual"]
        system += f"Site.1.FS.Count = {len(roots)}\n"
        for index, root in enumerate(roots, 1):
            for key, value in (("Type", "IOFileSys"), ("Root", root), ("MappedRoot", ""),
                               ("DIO", "0"), ("BrightClip", "0")):
                system += f"Site.1.FS.{index}.{key} = {value}\n"
        user = ET.Element("SM_UserPrefs")
        for key, value in (("AutoSaveMode", "1"), ("AutoSaveDuration", "10"),
                           ("IsProjectLiveSaveEnabled", "true"), ("IsTimelineBackupsEnabled", "true"),
                           ("ProjectBackupLocation", str(BACKUPS))):
            ET.SubElement(user, key).text = value
        contents = [system.encode(), ET.tostring(user, encoding="utf-8", xml_declaration=True)]
        for directory in (home / ".cache/davinci-resolve/CacheClip", home / "Videos", BACKUPS):
            directory.mkdir(parents=True, exist_ok=True)
        for path, content in zip(paths, contents):
            if path not in missing:
                continue
            with tempfile.NamedTemporaryFile(dir=configs, prefix=".studio-", delete=False) as temporary:
                temporary.write(content)
            try:
                os.link(temporary.name, path)
            except FileExistsError:
                _missing([path])
            finally:
                Path(temporary.name).unlink(missing_ok=True)


if __name__ == "__main__":
    try:
        prepare()
    except (OSError, ValueError) as error:
        sys.exit(f"Resolve preferences: {error}")
