#!/usr/bin/env python3
"""Rebuild, test and checksum the two RES-derived archives. No AWS changes.

Run from a cloned repository:
    python3 scripts/release.py

The script downloads missing originals from the known RES release bucket.
By default it never uploads to S3 or deploys infrastructure.\nExplicit --publish enables immutable release publication, never EC2 deployment.
"""
import argparse
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path
from release_publish import publish

ROOT=Path(__file__).resolve().parents[1]
SOURCE_BUCKET="research-engineering-studio-us-east-1"
SOURCE_PREFIX="releases/2026.06"
SOURCE_KEYS={
 "installation-scripts.tar.gz":"res-installation-scripts.tar.gz",
 "virtual-desktop.tar.gz":"idea-virtual-desktop-2026.06.tar.gz",
}
EXPECTED={
 "installation-scripts.tar.gz":"2e3b0e4b198582ea03b1926aaaf76da21688f2e6809ad4d153b95a9417c72658",
 "virtual-desktop.tar.gz":"f5d2297537bda746fe74411c18140bcd0f2d4322852fa9d32ce7606a93aed7a8",
}
def digest(path):
    h=hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda:f.read(1024*1024),b""):
            h.update(chunk)
    return h.hexdigest()
def run(*args):
    subprocess.run([str(x) for x in args],check=True,cwd=ROOT)
def ensure_originals(originals, offline=False, region="us-east-1"):
    """Read cached originals, or download and hash-check before atomic rename.

    Never replace a pre-existing file whose checksum is incorrect.
    """
    originals.mkdir(parents=True,exist_ok=True)
    for name,expected in EXPECTED.items():
        path=originals/name
        if path.exists():
            if not path.is_file() or digest(path)!=expected:
                raise ValueError("Existing original archive has wrong SHA-256; refusing overwrite: "+str(path))
        else:
            if offline:
                raise ValueError("Missing original archive in offline mode: "+str(path))
            uri="s3://"+SOURCE_BUCKET+"/"+SOURCE_PREFIX+"/"+SOURCE_KEYS[name]
            temporary=originals/(name+".download")
            if temporary.exists():
                raise ValueError("Stale download exists; inspect and remove manually: "+str(temporary))
            try:
                print("DOWNLOADING ORIGINAL:",uri,flush=True)
                run("aws","s3","cp",uri,temporary,"--region",region,"--no-progress")
                if digest(temporary)!=expected:
                    raise ValueError("Downloaded original SHA-256 mismatch: "+name)
                os.replace(temporary,path)
            except BaseException:
                temporary.unlink(missing_ok=True)
                raise
        print("VERIFIED ORIGINAL:",name,flush=True)

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--release-dir",type=Path,default=Path.home()/"studio-release")
    p.add_argument("--offline",action="store_true",help="Require previously downloaded original archives")
    p.add_argument("--region",default="us-east-1",help="Region for source S3 reads")
    p.add_argument("--publish",action="store_true",help="Explicitly publish immutable versioned artifacts to S3")
    p.add_argument("--version",help="Required with --publish, e.g. v0.1.0")
    args=p.parse_args()
    if args.publish != bool(args.version):
        p.error("--publish and --version must be supplied together")
    release=args.release_dir.expanduser().resolve()
    originals=release/"original"
    derived=release/"derived"
    staging=release/"staging"
    ensure_originals(originals,offline=args.offline,region=args.region)
    staging.mkdir(parents=True,exist_ok=True)
    hashes={}
    for name in EXPECTED:
        first=staging/name
        second=staging/(name+".repeat")
        for output in (first,second):
            run(sys.executable,ROOT/"runtime/build_res_overlay.py",
                "--input",originals/name,"--output",output)
        if digest(first)!=digest(second) or first.read_bytes()!=second.read_bytes():
            raise ValueError("Nondeterministic archive: "+name)
        hashes[name]=digest(first)
        print("REPRODUCIBLE:",name,hashes[name],flush=True)
    run(sys.executable,ROOT/"tests/test_res_overlay_archives.py",originals)
    derived.mkdir(parents=True,exist_ok=True)
    for name in EXPECTED:
        (staging/name).replace(derived/name)
    commit=subprocess.check_output(["git","rev-parse","HEAD"],cwd=ROOT,text=True).strip()
    manifest={"source_commit":commit,"original_sha256":EXPECTED,
              "derived_sha256":hashes,"status":"LOCAL_VERIFIED_NOT_PUBLISHED"}
    (release/"release-manifest.json").write_text(json.dumps(manifest,indent=2)+"\n")
    if args.publish:
        publish(release,args.version,region=args.region)
        print("\nSUCCESS — immutable S3 release published; no EC2 deployment.")
    else:
        print("\nSUCCESS — verified local release; no S3 upload or EC2 deployment.")
    print("Manifest:",release/"release-manifest.json")
if __name__=="__main__":
    try: main()
    except (OSError,ValueError,subprocess.CalledProcessError) as e:
        print("FAILED:",e,file=sys.stderr)
        sys.exit(1)
