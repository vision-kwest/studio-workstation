#!/usr/bin/env python3
"""T2: import original RES 2026.06 modules in an isolated, non-deploying process.

Requires original archives downloaded separately and an environment with pinned
third-party requirements. Does not install packages or invoke RES configuration.
"""
import argparse
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import subprocess
import sys
import tarfile
import tempfile

HASH = "f5d2297537bda746fe74411c18140bcd0f2d4322852fa9d32ce7606a93aed7a8"
MODULES = [
    "ideavirtualdesktop.app.linux.bootstrap",
    "ideavirtualdesktop.app.linux.post_reboot",
    "ideabootstrap.dcv.dcv",
    "ideabootstrap.dcv.linux.dcv_host",
    "ideabootstrap.dcv.linux.gl",
    "ideabootstrap.dcv.linux.x_server",
    "ideabootstrap.dcv.linux.start_dcv_service",
]
INNER = ["idea-bootstrap-lib.tar.gz", "idea-virtual-desktop-lib.tar.gz",
         "library-lib.tar.gz", "idea-sdk-lib.tar.gz",
         "idea-data-model-lib.tar.gz", "datamodel-lib.tar.gz"]
def safe_files(blob):
    with tarfile.open(fileobj=io.BytesIO(blob), mode="r:gz") as tf:
        for m in tf:
            path = PurePosixPath(m.name)
            if m.name.startswith("/") or ".." in path.parts or not (m.isfile() or m.isdir()):
                raise ValueError("Unsafe archive entry: " + m.name)
            if m.issym() or m.islnk() or m.size > 80_000_000:
                raise ValueError("Unsafe archive member: " + m.name)
            if m.isfile():
                yield m.name.removeprefix("./"), tf.extractfile(m).read()
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--archive", required=True)
    ap.add_argument("--report", default="gate2-t2-report.json")
    args = ap.parse_args()
    data = Path(args.archive).read_bytes()
    if hashlib.sha256(data).hexdigest() != HASH:
        raise ValueError("Original RES archive hash mismatch")
    outer = dict(safe_files(data))
    with tempfile.TemporaryDirectory(prefix="res-t2-") as tmp:
        roots = []
        for archive in INNER:
            if archive not in outer:
                raise ValueError("Missing nested archive: " + archive)
            root = Path(tmp) / archive.replace(".tar.gz", "")
            root.mkdir()
            for name, content in safe_files(outer[archive]):
                dest = root.joinpath(*PurePosixPath(name).parts)
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_bytes(content)
            # Each nested source distribution has a top-level versioned directory.
            dirs = [p for p in root.iterdir() if p.is_dir()]
            if len(dirs) != 1:
                raise ValueError(f"Unexpected package roots in {archive}: {dirs}")
            roots.append(str(dirs[0]))
        runner = r"""
import importlib, json, os, sys
# Audit hooks are defense in depth; run this process only in a network-disabled
# CI job/container without credentials or write access to the host.
def audit(event, args):
    if event in ("socket.connect", "socket.bind", "socket.__new__", "subprocess.Popen", "os.system",
                 "os.exec", "os.spawn", "os.fork", "os.posix_spawn"):
        raise PermissionError("T2 blocked side effect: " + event)
sys.addaudithook(audit)
result = {}
for name in json.loads(os.environ["T2_MODULES"]):
    try:
        importlib.import_module(name)
        result[name] = {"status": "imported"}
    except BaseException as e:
        result[name] = {"status": "failed", "error_type": type(e).__name__, "message": str(e)[:400]}
print("T2_JSON=" + json.dumps(result, sort_keys=True))
"""
        env = dict(os.environ)
        for key in list(env):
            if key.startswith(("AWS_", "BOTO_", "IDEA_", "RES_")) or key in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY"):
                env.pop(key, None)
        env.update({"PYTHONPATH": os.pathsep.join(roots), "PYTHONDONTWRITEBYTECODE": "1",
                    "RES_BASE_OS": "ubuntu2404", "T2_MODULES": json.dumps(MODULES),
                    "AWS_EC2_METADATA_DISABLED": "true", "HOME": tmp})
        proc = subprocess.run([sys.executable, "-B", "-c", runner], env=env,
                              cwd=tmp, capture_output=True, text=True, timeout=90)
        marker = next((line[8:] for line in proc.stdout.splitlines() if line.startswith("T2_JSON=")), None)
        findings = json.loads(marker) if marker else {}
        report = {"test": "T2 import-only", "status": "PASS" if len(findings) == len(MODULES) and all(v["status"] == "imported" for v in findings.values()) else "FAIL",
                  "modules": findings, "process_returncode": proc.returncode,
                  "stderr_tail": proc.stderr[-2000:], "notes": [
                      "No bootstrap.run(), DCV configure(), or systemd operations invoked",
                      "Not a standalone DCV or Python initialization success claim",
                      "No third-party packages installed by this harness",
                      "Execute only in isolated no-network, no-credentials runner"] }
        Path(args.report).write_text(json.dumps(report, indent=2) + "\n")
        print(json.dumps(report, indent=2))
        if report["status"] != "PASS":
            sys.exit(1)
if __name__ == "__main__":
    main()
