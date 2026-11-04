#!/usr/bin/env python3
"""Read-only local EFS/Rez/USD contract preflight; NEVER mounts or calls AWS."""
import argparse
import ast
import json
from pathlib import Path
import sys

PACKAGE_ROOT = Path("/opt/studio/packages")
STAGES = ("dev", "test", "prod")


def inspect(root=PACKAGE_ROOT, stage="prod", version="26.05", *, mountinfo=None):
    root = Path(root)
    if stage not in STAGES or version != "26.05" or not root.is_absolute():
        raise ValueError("Unsupported package stage, version, or relative root")
    if mountinfo is None:
        mountinfo = Path("/proc/self/mountinfo").read_text(encoding="utf-8")
    mounts = []
    for line in mountinfo.splitlines():
        if " - " not in line:
            continue
        before, after = line.split(" - ", 1)
        left, right = before.split(), after.split()
        if len(left) >= 6 and len(right) >= 2 and left[4] == str(root):
            mounts.append((left[5].split(","), right[0], right[1]))
    if len(mounts) != 1:
        raise ValueError("Package root is not a unique mountpoint")
    options, fs_type, source = mounts[0]
    if fs_type not in ("nfs4", "efs") or "ro" not in options:
        raise ValueError("Expected a read-only NFS4/EFS package mount")
    package = root / stage / "usd" / version
    metadata = json.loads((package / "manifest.json").read_text(encoding="utf-8"))
    assignments = {}
    for node in ast.parse((package / "package.py").read_text(encoding="utf-8")).body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            if node.targets[0].id in ("name", "version", "requires"):
                assignments[node.targets[0].id] = ast.literal_eval(node.value)
    if (assignments.get("name"), assignments.get("version")) != ("usd", version):
        raise ValueError("USD Rez package name or version mismatch")
    requires = assignments.get("requires")
    if not isinstance(requires, list) or not {"platform-linux", "arch-x86_64"} <= set(requires):
        raise ValueError("USD package missing Linux/x86_64 Rez requirements")
    if metadata.get("package_name") != "usd" or metadata.get("package_version") != version:
        raise ValueError("USD build manifest identity mismatch")
    if not (package / "lib" / "python").is_dir():
        raise ValueError("USD Python module directory missing")
    return {
        "package": str(package), "stage": stage, "mount_type": fs_type,
        "mount_source": source, "read_only": True,
        "rez_requires": requires, "manifest_python": metadata.get("python_version"),
        "host_python": ".".join(map(str, sys.version_info[:2])),
        "python_abi": "UNVERIFIED - must test pxr import in target Rez environment",
        "efs_identity": "UNVERIFIED - validate configured filesystem/access point separately",
    }


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--root", type=Path, default=PACKAGE_ROOT)
    p.add_argument("--stage", choices=STAGES, default="prod")
    a = p.parse_args(argv)
    try:
        print(json.dumps(inspect(a.root, a.stage), indent=2))
    except (ValueError, OSError, json.JSONDecodeError, SyntaxError, KeyError) as exc:
        p.exit(1, f"ERROR: {exc}\n")


if __name__ == "__main__":
    main()
