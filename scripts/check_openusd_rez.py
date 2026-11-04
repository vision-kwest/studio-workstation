#!/usr/bin/env python3
"""Read-only production OpenUSD 26.05 smoke checks via studio-rez-env.

Never installs software, mounts EFS, promotes Rez packages, or invokes AWS.
Requires an already approved production package mount and Rez installation.
"""
import argparse
import json
from pathlib import Path
import subprocess
import sys

try:
    from scripts import rez_openusd_preflight
except ModuleNotFoundError:
    import rez_openusd_preflight

VERSION = "26.05"
PACKAGE_REQUEST = "usd-26.05"
PACKAGE_ROOT = Path("/opt/studio/packages")
REZ_LAUNCHER = Path("/usr/local/bin/studio-rez-env")
PYTHON = Path("/usr/bin/python3")
FIXTURE = Path(__file__).resolve().parent.parent / "fixtures/openusd/hello.usda"
PROBE_TAG = "STUDIO_OPENUSD_PROBE="
TIMEOUT = 60
PROFILES = ("cpu", "gpu")

# Run in the resolved Rez environment. Importing pxr.Usd._usd actually loads
# its compiled extension; an innocuous manifest or namespace import cannot
# establish binary ABI compatibility.
PYTHON_PROBE = """
import importlib
import json
import os
import sys
import sysconfig
from pxr import Usd

native = importlib.import_module("pxr.Usd._usd")
stage = Usd.Stage.CreateInMemory()
stage.DefinePrim("/World", "Xform")
assert stage.GetPrimAtPath("/World").IsValid()
print("STUDIO_OPENUSD_PROBE=" + json.dumps({
    "python_implementation": sys.implementation.name,
    "python_version": list(sys.version_info[:3]),
    "soabi": sysconfig.get_config_var("SOABI") or "",
    "usd_version": list(Usd.GetVersion()),
    "pxr_usd_file": getattr(Usd, "__file__", ""),
    "pxr_native_file": getattr(native, "__file__", ""),
    "rez_packages_path": os.environ.get("REZ_PACKAGES_PATH", ""),
    "rez_config_file": os.environ.get("REZ_CONFIG_FILE", ""),
}))
"""


def _command(runner, launcher, args, label):
    command = [str(launcher), PACKAGE_REQUEST, "--", *args]
    try:
        result = runner(command, capture_output=True, text=True,
                        check=False, timeout=TIMEOUT)
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"{label} timed out after {TIMEOUT}s") from exc
    except OSError as exc:
        raise RuntimeError(f"{label} could not execute: {exc}") from exc
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "no diagnostic output").strip()
        raise RuntimeError(f"{label} failed (exit {result.returncode}): {detail[-500:]}")
    return result.stdout


def _under_package(origin, package, label):
    if not isinstance(origin, str) or not origin:
        raise ValueError(f"{label} has no module path")
    resolved = Path(origin).resolve()
    package = package.resolve()
    if not resolved.is_relative_to(package):
        raise ValueError(f"{label} resolved outside requested USD package: {resolved}")
    return resolved


def _validate_probe(output, package, root):
    records = [line[len(PROBE_TAG):] for line in output.splitlines()
               if line.startswith(PROBE_TAG)]
    if len(records) != 1:
        raise ValueError("Python ABI probe output missing or ambiguous")
    try:
        data = json.loads(records[0])
    except json.JSONDecodeError as exc:
        raise ValueError("Python ABI probe output is not JSON") from exc
    if not isinstance(data, dict):
        raise ValueError("Python ABI probe result is not an object")
    if data.get("python_implementation") != "cpython":
        raise ValueError("USD Python bindings require the tested CPython interpreter")
    version = data.get("python_version")
    if (not isinstance(version, list) or len(version) != 3 or
            any(type(item) is not int or item < 0 for item in version)):
        raise ValueError("Python ABI probe version is invalid")
    soabi = data.get("soabi")
    if not isinstance(soabi, str) or not soabi.startswith("cpython-"):
        raise ValueError("Python ABI probe lacks a CPython SOABI")
    if not soabi.startswith(f"cpython-{version[0]}{version[1]}-"):
        raise ValueError("Python ABI tag is inconsistent with the runtime")
    usd_version = data.get("usd_version")
    if (not isinstance(usd_version, list) or len(usd_version) < 3 or
            any(type(item) is not int for item in usd_version) or
            usd_version[-2:] != [26, 5]):
        raise ValueError("Imported OpenUSD binary version does not match usd-26.05")
    if data.get("rez_packages_path") != str(root / "prod"):
        raise ValueError("Rez resolved outside the production package repository")
    config = data.get("rez_config_file")
    if (not isinstance(config, str) or not Path(config).is_absolute() or
            not config.endswith("/config/rezconfig.prod.py")):
        raise ValueError("Rez production configuration was not loaded")
    _under_package(data.get("pxr_usd_file"), package, "pxr.Usd")
    native = _under_package(data.get("pxr_native_file"), package, "pxr.Usd._usd")
    if native.suffix != ".so":
        raise ValueError("pxr.Usd native binding is not a shared library")
    if ".cpython-" in native.name and f".{soabi}.so" not in native.name:
        raise ValueError("pxr.Usd native extension ABI differs from running CPython")
    return {"verified": True, "python_version": ".".join(map(str, version)),
            "python_soabi": soabi, "usd_version": usd_version,
            "native_module": str(native)}


def smoke(*, profile="cpu", root=PACKAGE_ROOT, launcher=REZ_LAUNCHER,
          python=PYTHON, fixture=FIXTURE, mountinfo=None,
          runner=subprocess.run):
    """Same read-only USD/Rez validation on either approved workstation profile."""
    if profile not in PROFILES:
        raise ValueError("OpenUSD validation is limited to CPU/GPU workstation profiles")
    root, launcher, python, fixture = map(Path, (root, launcher, python, fixture))
    if not root.is_absolute() or not python.is_absolute():
        raise ValueError("Package repository and Python paths must be absolute")
    try:
        metadata = rez_openusd_preflight.inspect(root, "prod", VERSION,
                                                 mountinfo=mountinfo)
    except (OSError, ValueError, SyntaxError, KeyError) as exc:
        raise RuntimeError(f"Production EFS/Rez preflight failed: {exc}") from exc
    if (launcher.is_symlink() or not launcher.is_file() or
            not (launcher.stat().st_mode & 0o111)):
        raise ValueError("Approved executable studio-rez-env launcher is missing")
    if fixture.is_symlink() or not fixture.is_file():
        raise ValueError("OpenUSD smoke fixture is missing or symlinked")
    source = fixture.read_text(encoding="utf-8")
    if "#usda 1.0" not in source or 'def Xform "World"' not in source:
        raise ValueError("OpenUSD smoke fixture does not contain expected scene")

    cat = _command(runner, launcher, ["usdcat", str(fixture)], "usdcat")
    if "#usda 1.0" not in cat or 'def Xform "World"' not in cat:
        raise RuntimeError("usdcat output did not contain expected World Xform")
    _command(runner, launcher, ["usdchecker", str(fixture)], "usdchecker")
    stdout = _command(runner, launcher,
                      [str(python), "-c", PYTHON_PROBE], "Python ABI/import probe")
    abi = _validate_probe(stdout, Path(metadata["package"]), root)
    return {"status": "verified", "profile": profile,
            "package": metadata["package"],
            "rez_request": PACKAGE_REQUEST, "usdcat": "passed",
            "usdchecker": "passed", "python_abi": abi,
            "efs_identity": metadata["efs_identity"]}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=PACKAGE_ROOT)
    parser.add_argument("--launcher", type=Path, default=REZ_LAUNCHER)
    parser.add_argument("--python", type=Path, default=PYTHON)
    parser.add_argument("--profile", choices=PROFILES, default="cpu")
    args = parser.parse_args(argv)
    try:
        print(json.dumps(smoke(profile=args.profile, root=args.root,
                               launcher=args.launcher, python=args.python), indent=2))
    except (ValueError, RuntimeError, OSError) as exc:
        parser.exit(1, f"OpenUSD validation FAILED: {exc}\n")


if __name__ == "__main__":
    main()
