#!/usr/bin/env python3
"""Deterministically package the optional Issue #43 DNS Lambda for offline review.

Does not upload, publish, provision, or invoke AWS.
"""
import argparse
import hashlib
from pathlib import Path
import zipfile

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "runtime" / "dcv_dynamic_dns.py"


def package(destination: Path):
    source_bytes = SOURCE.read_bytes()
    # Stable member timestamp/permissions, payload name matches Lambda handler.
    info = zipfile.ZipInfo("dcv_dynamic_dns.py", date_time=(2020, 1, 1, 0, 0, 0))
    info.compress_type = zipfile.ZIP_DEFLATED
    info.external_attr = 0o644 << 16
    destination.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(destination, "w", compression=zipfile.ZIP_DEFLATED,
                         compresslevel=9) as archive:
        archive.writestr(info, source_bytes, compress_type=zipfile.ZIP_DEFLATED,
                         compresslevel=9)
    data = destination.read_bytes()
    return {"file": str(destination), "sha256": hashlib.sha256(data).hexdigest(),
            "size": len(data), "source_sha256": hashlib.sha256(source_bytes).hexdigest()}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    import json
    print(json.dumps(package(args.output), indent=2))


if __name__ == "__main__":
    main()
