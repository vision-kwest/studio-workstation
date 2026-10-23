#!/usr/bin/env python3
"""Run mocked tests from a verified, locally derived RES application archive."""
import hashlib
import io
import os
from pathlib import Path, PurePosixPath
import subprocess
import sys
import tarfile
import tempfile

def extract_safe(blob,root):
    with tarfile.open(fileobj=io.BytesIO(blob),mode="r:gz") as tf:
        for member in tf:
            p=PurePosixPath(member.name)
            if p.is_absolute() or ".." in p.parts or not (member.isfile() or member.isdir()):
                raise ValueError("Unsafe archive member "+member.name)
            if member.isfile():
                dest=root.joinpath(*p.parts)
                dest.parent.mkdir(parents=True,exist_ok=True)
                dest.write_bytes(tf.extractfile(member).read())
def main():
    archive=Path(sys.argv[1])
    test=Path(sys.argv[2]).resolve()
    report=Path(sys.argv[3]).resolve()
    with tempfile.TemporaryDirectory() as temp:
        outer=Path(temp)/"outer"
        outer.mkdir()
        extract_safe(archive.read_bytes(),outer)
        roots=[]
        for filename in ("idea-bootstrap-lib.tar.gz","idea-virtual-desktop-lib.tar.gz",
                         "library-lib.tar.gz","idea-sdk-lib.tar.gz",
                         "idea-data-model-lib.tar.gz","datamodel-lib.tar.gz"):
            root=Path(temp)/filename.removesuffix(".tar.gz")
            root.mkdir()
            extract_safe((outer/filename).read_bytes(),root)
            candidates=[p for p in root.iterdir() if p.is_dir()]
            if len(candidates)!=1:
                raise ValueError("Unexpected source distribution layout "+filename)
            roots.append(str(candidates[0]))
        env={k:v for k,v in os.environ.items() if not k.startswith(("AWS_","BOTO_","IDEA_","RES_"))}
        env.update(PYTHONPATH=os.pathsep.join(roots),STUDIO_STANDALONE="true",
                   RES_BASE_OS="ubuntu2404",AWS_EC2_METADATA_DISABLED="true",
                   STUDIO_TEST_REPORT=str(report),PYTHONDONTWRITEBYTECODE="1")
        subprocess.run([sys.executable,"-B",str(test)],env=env,cwd=temp,check=True,timeout=90)
if __name__=="__main__":
    main()
