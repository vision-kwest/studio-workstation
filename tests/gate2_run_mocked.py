#!/usr/bin/env python3
"""Run original RES T3 mocked tests from verified archive, without installing or executing RES setup."""
import hashlib, io, os, pathlib, subprocess, sys, tarfile, tempfile
from pathlib import PurePosixPath
ARCHIVE_HASH="f5d2297537bda746fe74411c18140bcd0f2d4322852fa9d32ce7606a93aed7a8"
INNER=("idea-bootstrap-lib.tar.gz","idea-virtual-desktop-lib.tar.gz","library-lib.tar.gz","idea-sdk-lib.tar.gz","idea-data-model-lib.tar.gz","datamodel-lib.tar.gz")
def members(data):
    with tarfile.open(fileobj=io.BytesIO(data),mode="r:gz") as tf:
        for m in tf:
            p=PurePosixPath(m.name)
            if m.name.startswith("/") or ".." in p.parts or not (m.isfile() or m.isdir()) or m.issym() or m.islnk() or m.size>80_000_000:
                raise ValueError("unsafe member: "+m.name)
            if m.isfile():
                yield m.name.removeprefix("./"),tf.extractfile(m).read()
def main():
    if len(sys.argv)!=4:
        raise SystemExit("Usage: gate2_run_mocked.py archive.tar.gz test_script.py report.json")
    raw=pathlib.Path(sys.argv[1]).read_bytes()
    if hashlib.sha256(raw).hexdigest()!=ARCHIVE_HASH:
        raise ValueError("Archive hash mismatch")
    outer=dict(members(raw))
    with tempfile.TemporaryDirectory(prefix="res-t3-") as tmp:
        roots=[]
        for archive in INNER:
            root=pathlib.Path(tmp)/archive.removesuffix(".tar.gz")
            root.mkdir()
            for name,blob in members(outer[archive]):
                dest=root.joinpath(*PurePosixPath(name).parts)
                dest.parent.mkdir(parents=True,exist_ok=True)
                dest.write_bytes(blob)
            subdirs=[p for p in root.iterdir() if p.is_dir()]
            if len(subdirs)!=1:
                raise ValueError("Unexpected source archive layout: "+archive)
            roots.append(str(subdirs[0]))
        env={k:v for k,v in os.environ.items() if not k.startswith(("AWS_","BOTO_","IDEA_","RES_"))}
        env.update(PYTHONPATH=os.pathsep.join(roots),PYTHONDONTWRITEBYTECODE="1",
                   AWS_EC2_METADATA_DISABLED="true",RES_BASE_OS="ubuntu2404",T3_REPORT=str(pathlib.Path(sys.argv[3]).resolve()))
        p=subprocess.run([sys.executable,"-B",str(pathlib.Path(sys.argv[2]).resolve())],env=env,cwd=tmp,timeout=120)
        raise SystemExit(p.returncode)
if __name__=="__main__":main()
