#!/usr/bin/env python3
"""Build RES-derived archive copies from hash-verified AWS RES 2026.06 archives.

Does not run installers or modify original archives. Output is a new, local,
reviewable artifact; caller must approve before upload/deployment.
"""
import argparse
import hashlib
import io
from pathlib import Path, PurePosixPath
import tarfile

EXPECTED = {
 "installation-scripts.tar.gz":"2e3b0e4b198582ea03b1926aaaf76da21688f2e6809ad4d153b95a9417c72658",
 "virtual-desktop.tar.gz":"f5d2297537bda746fe74411c18140bcd0f2d4322852fa9d32ce7606a93aed7a8",
}
ROOT=Path(__file__).resolve().parent/"overrides"
def rewrite(raw, mapping):
    out=io.BytesIO()
    found=set()
    with tarfile.open(fileobj=io.BytesIO(raw),mode="r:gz") as src:
        with tarfile.open(fileobj=out,mode="w:gz") as dst:
            for member in src:
                path=PurePosixPath(member.name)
                if path.is_absolute() or ".." in path.parts or not (member.isfile() or member.isdir()):
                    raise ValueError("Unsafe archive entry "+member.name)
                key=member.name.removeprefix("./")
                data=src.extractfile(member).read() if member.isfile() else None
                if key in mapping:
                    data=mapping[key].read_bytes()
                    member.size=len(data)
                    found.add(key)
                dst.addfile(member,io.BytesIO(data) if data is not None else None)
            missing=set(mapping)-found
            if missing:
                raise ValueError("Original archive missing override paths: "+str(sorted(missing)))
    return out.getvalue()
def main():
    p=argparse.ArgumentParser()
    p.add_argument("--input",type=Path,required=True)
    p.add_argument("--output",type=Path,required=True)
    args=p.parse_args()
    if args.input.resolve()==args.output.resolve():
        raise ValueError("Never overwrite the original archive")
    if args.input.name not in EXPECTED:
        raise ValueError("Input must be named installation-scripts.tar.gz or virtual-desktop.tar.gz")
    raw=args.input.read_bytes()
    if hashlib.sha256(raw).hexdigest()!=EXPECTED[args.input.name]:
        raise ValueError("Original archive SHA256 mismatch")
    if args.input.name=="installation-scripts.tar.gz":
        mapping={
            "scripts/virtual-desktop-host/linux/install.sh":ROOT/"scripts/virtual-desktop-host/linux/install.sh",
            "scripts/common/linux/install_app.sh":ROOT/"scripts/common/linux/install_app.sh",
        }
        output=rewrite(raw,mapping)
    else:
        with tarfile.open(fileobj=io.BytesIO(raw),mode="r:gz") as tf:
            members={m.name.removeprefix("./"):tf.extractfile(m).read() for m in tf if m.isfile()}
        nested={}
        for package,prefix,files in [
            ("idea-bootstrap-lib.tar.gz","ideabootstrap",[
                "dcv/linux/dcv_host.py","dcv/linux/gl.py","dcv/dcv.py"]),
            ("idea-virtual-desktop-lib.tar.gz","ideavirtualdesktop",[
                "app/linux/bootstrap.py","app/linux/post_reboot.py"])]:
            with tarfile.open(fileobj=io.BytesIO(members[package]),mode="r:gz") as tf:
                names=[m.name.removeprefix("./") for m in tf if m.isfile()]
            mapping={}
            for relative in files:
                matches=[n for n in names if n.endswith("/"+prefix+"/"+relative)]
                if len(matches)!=1:
                    raise ValueError("Cannot locate unique original module "+relative)
                mapping[matches[0]]=ROOT/prefix/relative
            nested[package]=rewrite(members[package],mapping)
        # The new standalone entry point must be inserted into the existing
        # source distribution. No implicit file insertion is permitted.
        raise RuntimeError("Standalone entrypoint insertion into original Python sdist not implemented; refusing incomplete archive")
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_bytes(output)
    print(args.output,hashlib.sha256(output).hexdigest())
if __name__=="__main__":
    main()
