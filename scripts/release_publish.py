"""Immutable, versioned S3 publication of verified RES-derived artifacts.

No infrastructure provisioning. Objects use conditional PutObject and are
read back for byte-level SHA-256 verification. Manifest is published last.
"""
import hashlib
import json
import re
from pathlib import Path

BUCKET="research-engineering-studio-us-east-1"
BASE="studio-workstation/releases"
UPSTREAM="res-2026.06"
ARTIFACTS=("installation-scripts.tar.gz","virtual-desktop.tar.gz")
MANIFEST="release-manifest.json"

def prefix_for(version):
    if not re.fullmatch(r"v(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)",version):
        raise ValueError("Version must be a release tag such as v0.1.0")
    return f"{BASE}/{version}/{UPSTREAM}"

def checksum(data):
    return hashlib.sha256(data).hexdigest()

def publish(release_dir, version, region="us-east-1", client=None):
    """Publish only if every destination key is absent.

    If a concurrent publisher wins a conditional put, stop. Existing objects
    are never overwritten or deleted. A failure can leave a partial prefix;
    use a new release version rather than reusing it.
    """
    if client is None:
        import boto3
        client=boto3.client("s3",region_name=region)
    release_dir=Path(release_dir)
    prefix=prefix_for(version)
    manifest_path=release_dir/MANIFEST
    manifest=json.loads(manifest_path.read_text())
    if manifest.get("status")!="LOCAL_VERIFIED_NOT_PUBLISHED":
        raise ValueError("Expected a verified local release manifest")
    hashes=manifest.get("derived_sha256")
    if not isinstance(hashes,dict) or set(hashes)!=set(ARTIFACTS):
        raise ValueError("Manifest does not list exactly the two derived artifacts")
    payloads={}
    for name in ARTIFACTS:
        data=(release_dir/"derived"/name).read_bytes()
        if checksum(data)!=hashes[name]:
            raise ValueError("Local derived artifact SHA-256 mismatch: "+name)
        payloads[name]=data
    published=dict(manifest)
    published.update({
        "status":"PUBLISHED",
        "release_version":version,
        "upstream_res_version":"2026.06",
        "bucket":BUCKET,
        "prefix":prefix,
    })
    payloads[MANIFEST]=(json.dumps(published,indent=2,sort_keys=True)+"\n").encode()

    # Preflight is advisory only; atomic IfNoneMatch protects every write.
    for name in payloads:
        key=f"{prefix}/{name}"
        try:
            client.head_object(Bucket=BUCKET,Key=key)
        except Exception as exc:
            code=exc.response.get("Error",{}).get("Code") if hasattr(exc,"response") else None
            if code not in ("404","NoSuchKey","NotFound"):
                raise
        else:
            raise FileExistsError("Release object already exists: s3://"+BUCKET+"/"+key)

    for name in (*ARTIFACTS,MANIFEST):
        key=f"{prefix}/{name}"
        data=payloads[name]
        print("PUBLISH:",f"s3://{BUCKET}/{key}",flush=True)
        client.put_object(
            Bucket=BUCKET,Key=key,Body=data,IfNoneMatch="*",
            ContentType="application/json" if name==MANIFEST else "application/gzip",
            Metadata={"sha256":checksum(data)},
        )
        # Read back the full object rather than relying on ETag (which may
        # not represent an MD5 of the original bytes).
        response=client.get_object(Bucket=BUCKET,Key=key)
        try:
            remote=response["Body"].read()
        finally:
            response["Body"].close()
        if checksum(remote)!=checksum(data):
            raise RuntimeError("Uploaded object verification failed: "+key)
        print("VERIFIED:",name,checksum(data),flush=True)
    print("RELEASE PUBLISHED:",f"s3://{BUCKET}/{prefix}/")
    return prefix
