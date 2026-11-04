#!/usr/bin/env python3
"""Opt-in CPU/GPU Rez 3.4.0 installation from pinned offline artifacts.

Uses Rez's upstream production install.py, not pip-installed CLI scripts.
Not called by RES/bootstrap; never downloads, mounts EFS, or invokes AWS.
"""
import argparse
import ast
import hashlib
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import shlex
import subprocess
import sys
import tarfile
import tempfile

VERSION = "3.4.0"
SOURCE_SHA256 = "57e03cd2a9dbab818290756de0fd1205fbd898b1e02205dbe2c90a7d0d0f5159"
STUDIO_REZ_COMMIT = "434a3ffe4775d17234b6d0bb2460ab049ece2262"
TARGET = Path("/opt/studio/rez/3.4.0")
WRAPPER = Path("/usr/local/bin/studio-rez-env")
PROFILES = ("cpu", "gpu")


def _sha(path):
    h = hashlib.sha256()
    with path.open("rb") as src:
        for chunk in iter(lambda: src.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _wheels(directory, manifest):
    listed = {}
    for line in manifest.read_text(encoding="utf-8").splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        found = re.fullmatch(r"([0-9a-f]{64})  ([A-Za-z0-9_.+-]+\.whl)", line)
        if not found or found[2] in listed:
            raise ValueError("Invalid or duplicate wheel manifest entry")
        listed[found[2]] = found[1]
    items = list(directory.iterdir())
    if not listed or {p.name for p in items} != set(listed) or any(
            not p.is_file() or p.is_symlink() for p in items):
        raise ValueError("Wheelhouse must contain exactly the approved wheels")
    for name, digest in listed.items():
        if _sha(directory / name) != digest:
            raise ValueError("Wheelhouse SHA-256 mismatch: " + name)
    return len(listed)


def _checkout(root, run):
    if root.is_symlink():
        raise ValueError("studio-rez checkout must not be a symlink")
    root = root.resolve(strict=True)
    if not root.is_dir():
        raise ValueError("studio-rez checkout must be a directory")
    rev = run(["git", "-C", str(root), "rev-parse", "HEAD"],
              check=True, capture_output=True, text=True)
    dirty = run(["git", "-C", str(root), "status", "--porcelain"],
                check=True, capture_output=True, text=True)
    if rev.stdout.strip() != STUDIO_REZ_COMMIT or dirty.stdout.strip():
        raise ValueError("studio-rez checkout revision or worktree differs")
    tree = ast.parse((root / "config/rezconfig.prod.py").read_text(encoding="utf-8"))
    values = {}
    for node in tree.body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            if node.targets[0].id in ("packages_path", "implicit_packages"):
                values[node.targets[0].id] = ast.literal_eval(node.value)
    if values != {"packages_path": ["/opt/studio/packages/prod"],
                  "implicit_packages": ["platform", "arch", "os"]}:
        raise ValueError("studio-rez production config changed")
    script = (root / "env/studio-prod.sh").read_text(encoding="utf-8")
    if ('REZ_PACKAGES_PATH="' not in script or
            'rezconfig.prod.py' not in script or
            '/opt/studio/packages' not in script):
        raise ValueError("studio-rez environment contract changed")
    return root


def _launcher(root, target):
    source = shlex.quote(str(root / "env/studio-prod.sh"))
    executable = shlex.quote(str(target / "bin/rez/rez-env"))
    return ('#!/bin/bash\nset -e\nexport STUDIO_REZ_QUIET=1\n'
            f'source {source}\n'
            f'exec {executable} "$@"\n')


def plan(archive, wheelhouse, wheel_manifest, studio_rez, *,
         profile="cpu", target=TARGET, wrapper=WRAPPER, run=subprocess.run):
    if profile not in PROFILES:
        raise ValueError("Rez setup is limited to CPU/GPU workstation profiles")
    archive, wheelhouse, wheel_manifest = map(Path, (archive, wheelhouse, wheel_manifest))
    target, wrapper = Path(target), Path(wrapper)
    if not target.is_absolute() or not wrapper.is_absolute():
        raise ValueError("Rez install paths must be absolute")
    if _sha(archive) != SOURCE_SHA256:
        raise ValueError("Pinned upstream Rez 3.4.0 SHA-256 mismatch")
    wheel_count = _wheels(wheelhouse, wheel_manifest)
    source = _checkout(Path(studio_rez), run)
    if target.is_symlink() or wrapper.is_symlink():
        raise ValueError("Refuse symlinked Rez installation paths")
    launcher = _launcher(source, target)
    if wrapper.exists() and wrapper.read_text(encoding="utf-8") != launcher:
        raise ValueError("Existing Rez launcher differs")
    if target.exists():
        marker = target / "bin/rez/.rez_production_install"
        if not marker.is_file() or marker.read_text().strip() != VERSION or not (
                target / "bin/rez/rez-env").is_file():
            raise ValueError("Existing Rez install is not approved version")
    return {"profile": profile, "target": target, "wrapper": wrapper, "launcher": launcher,
            "wheelhouse": wheelhouse, "wheel_count": wheel_count,
            "already_installed": target.exists() and wrapper.exists()}


def _unpack(archive, destination):
    seen = set()
    with tarfile.open(archive, "r:gz") as tar:
        for member in tar:
            name = PurePosixPath(member.name)
            if (name.is_absolute() or ".." in name.parts or
                    not name.parts or name.parts[0] != "rez-" + VERSION or
                    not (member.isfile() or member.isdir()) or member.name in seen):
                raise ValueError("Unsafe Rez source archive member")
            seen.add(member.name)
            out = destination.joinpath(*name.parts)
            if member.isdir():
                out.mkdir(parents=True, exist_ok=True)
            else:
                out.parent.mkdir(parents=True, exist_ok=True)
                with tar.extractfile(member) as src, out.open("wb") as dst:
                    shutil.copyfileobj(src, dst)
    installer = destination / ("rez-" + VERSION) / "install.py"
    if not installer.is_file():
        raise ValueError("Rez source archive missing upstream install.py")
    return installer


def install(archive, wheelhouse, wheel_manifest, studio_rez, *,
            profile="cpu", target=TARGET, wrapper=WRAPPER, run=subprocess.run):
    config = plan(archive, wheelhouse, wheel_manifest, studio_rez,
                  profile=profile, target=target, wrapper=wrapper, run=run)
    if config["already_installed"]:
        return {"installed": True, "changed": False, "version": VERSION}
    target, wrapper = config["target"], config["wrapper"]
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".rez-stage-", dir=target.parent) as tmp:
        temp = Path(tmp)
        installer = _unpack(Path(archive), temp)
        # Upstream install.py creates a virtualenv with absolute shebangs.
        # Installing in a staging directory then renaming is NOT relocatable.
        # Build directly at the final versioned path, and remove only a
        # brand-new failed installation (never an existing valid version).
        created_here = not target.exists()
        try:
            if created_here:
                env = dict(os.environ, PIP_NO_INDEX="1",
                           PIP_FIND_LINKS=str(config["wheelhouse"].resolve(strict=True)),
                           PIP_DISABLE_PIP_VERSION_CHECK="1")
                run([sys.executable, "-E", str(installer), str(target)],
                    check=True, env=env)
                marker = target / "bin/rez/.rez_production_install"
                if not marker.is_file() or marker.read_text().strip() != VERSION or not (
                        target / "bin/rez/rez-env").is_file():
                    raise RuntimeError("Upstream Rez production install verification failed")
            wrapper.parent.mkdir(parents=True, exist_ok=True)
            temporary = temp / "studio-rez-env"
            temporary.write_text(config["launcher"], encoding="utf-8")
            temporary.chmod(0o755)
            if wrapper.exists() and wrapper.read_text() != config["launcher"]:
                raise ValueError("Rez launcher changed during install")
            temporary.replace(wrapper)
        except BaseException:
            if created_here and target.is_dir() and not target.is_symlink():
                shutil.rmtree(target)
            raise
    return {"installed": True, "changed": True, "version": VERSION}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rez-archive", type=Path, required=True)
    parser.add_argument("--wheelhouse", type=Path, required=True)
    parser.add_argument("--wheel-manifest", type=Path, required=True)
    parser.add_argument("--studio-rez-root", type=Path, required=True)
    parser.add_argument("--profile", choices=PROFILES, default="cpu")
    args = parser.parse_args(argv)
    install(args.rez_archive, args.wheelhouse, args.wheel_manifest, args.studio_rez_root,
            profile=args.profile)


if __name__ == "__main__":
    main()
