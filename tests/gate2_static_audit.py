#!/usr/bin/env python3
"""Read-only RES 2026.06 artifact inventory and static import audit.

Never extracts to disk, imports RES modules, installs dependencies, or calls RES APIs.
"""
import argparse
import ast
import hashlib
import io
import json
import pathlib
import sys
import tarfile

EXPECTED = {
    "installation-scripts.tar.gz": "2e3b0e4b198582ea03b1926aaaf76da21688f2e6809ad4d153b95a9417c72658",
    "virtual-desktop.tar.gz": "f5d2297537bda746fe74411c18140bcd0f2d4322852fa9d32ce7606a93aed7a8",
}
TARGETS = (
    "ideavirtualdesktop/app/linux/bootstrap.py",
    "ideavirtualdesktop/app/linux/post_reboot.py",
    "ideabootstrap/dcv/dcv.py",
    "ideabootstrap/dcv/linux/dcv_host.py",
    "ideabootstrap/dcv/linux/gl.py",
    "ideabootstrap/dcv/linux/x_server.py",
    "ideabootstrap/dcv/linux/start_dcv_service.py",
)
ARCHIVES = (
    "idea-bootstrap-lib.tar.gz",
    "idea-virtual-desktop-lib.tar.gz",
    "library-lib.tar.gz",
    "idea-sdk-lib.tar.gz",
    "idea-data-model-lib.tar.gz",
    "datamodel-lib.tar.gz",
)
def validate_tar(blob, label):
    out = {}
    with tarfile.open(fileobj=io.BytesIO(blob), mode="r:gz") as tf:
        for m in tf:
            name = pathlib.PurePosixPath(m.name)
            if m.name.startswith("/") or ".." in name.parts or m.issym() or m.islnk() or not (m.isfile() or m.isdir()):
                raise ValueError(f"Unsafe tar member in {label}: {m.name}")
            if m.size > 80_000_000:
                raise ValueError(f"Oversized member in {label}: {m.name}")
            if m.isfile():
                out[m.name.removeprefix("./")] = tf.extractfile(m).read()
    return out
def analyze(path, source):
    tree = ast.parse(source, filename=path)
    imports = []
    calls = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.extend(n.name for n in node.names)
        elif isinstance(node, ast.ImportFrom):
            imports.append("." * node.level + (node.module or ""))
        elif isinstance(node, ast.Call):
            try:
                name = ast.unparse(node.func)
            except Exception:
                continue
            if any(key in name for key in ("cluster_settings", "update_session", "subprocess", "systemctl", "session_permissions", "crontab", "os.chown")):
                calls.append({"line": node.lineno, "callee": name})
    return {"imports": sorted(set(imports)), "external_call_sites": calls}
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--directory", default=".")
    ap.add_argument("--report", default="gate2-static-report.json")
    args = ap.parse_args()
    folder = pathlib.Path(args.directory)
    raw = {}
    for filename, expected in EXPECTED.items():
        p = folder / filename
        data = p.read_bytes()
        digest = hashlib.sha256(data).hexdigest()
        if digest != expected:
            raise ValueError(f"SHA-256 mismatch: {filename}: {digest}")
        raw[filename] = validate_tar(data, filename)
    install = raw["installation-scripts.tar.gz"]
    for name in ("scripts/virtual-desktop-host/linux/install.sh", "scripts/virtual-desktop-host/linux/install_post_reboot.sh"):
        if name not in install:
            raise ValueError(f"Missing installer: {name}")
    app = raw["virtual-desktop.tar.gz"]
    nested = {}
    for name in ARCHIVES:
        if name not in app:
            raise ValueError(f"Missing nested archive: {name}")
        nested[name] = validate_tar(app[name], name)
    py_files = {}
    for members in nested.values():
        for path, data in members.items():
            if path.endswith(".py"):
                py_files[path] = data
    findings = {}
    for target in TARGETS:
        matches = [(path, data) for path, data in py_files.items() if path.endswith("/" + target)]
        if len(matches) != 1:
            raise ValueError(f"Expected one copy of {target}, found {len(matches)}")
        path, data = matches[0]
        findings[target] = analyze(path, data.decode("utf-8"))
    report = {
        "status": "T0/T1 static only; T2/T3/T4 not executed",
        "source_tag": "2026.06",
        "source_commit": "1f526a241fb293a29fb018f08ccf81c100c05a42",
        "sha256": EXPECTED,
        "modules": findings,
        "limitations": [
            "No RES Python imports or runtime initialization executed",
            "No transitive dynamic-import closure proven",
            "No EC2 IAM access or DCV login tested",
            "Hash equality confirms observed Gate 1 bytes, not independent upstream signature",
        ],
    }
    pathlib.Path(args.report).write_text(json.dumps(report, indent=2) + "\n")
    print(f"PASS: hashes, safe archive inventory, {len(findings)} Python AST audits; report {args.report}")
if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        sys.exit(1)
