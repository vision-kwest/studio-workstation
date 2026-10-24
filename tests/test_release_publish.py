"""Offline publication tests; no AWS calls."""
import importlib.util
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

PATH=Path(__file__).resolve().parents[1]/"scripts/release_publish.py"
spec=importlib.util.spec_from_file_location("release_publish",PATH)
m=importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

class NotFound(Exception):
    def __init__(self):
        self.response={"Error":{"Code":"404"}}

class FakeBody:
    def __init__(self,data): self.data=data
    def read(self): return self.data
    def close(self): pass

class FakeS3:
    def __init__(self): self.objects={}; self.calls=[]
    def head_object(self,Bucket,Key):
        if Key not in self.objects: raise NotFound()
        return {}
    def put_object(self,**kw):
        self.calls.append(kw["Key"])
        if kw["Key"] in self.objects: raise FileExistsError()
        self.objects[kw["Key"]]=kw["Body"]
    def get_object(self,Bucket,Key): return {"Body":FakeBody(self.objects[Key])}

class ReleasePublishTests(unittest.TestCase):
    def setup_release(self,root):
        (root/"derived").mkdir()
        hashes={}
        for name in m.ARTIFACTS:
            data=("test "+name).encode()
            (root/"derived"/name).write_bytes(data)
            hashes[name]=hashlib.sha256(data).hexdigest()
        (root/"release-manifest.json").write_text(json.dumps({
            "status":"LOCAL_VERIFIED_NOT_PUBLISHED","source_commit":"abc",
            "derived_sha256":hashes}))
    def test_version_validation(self):
        for invalid in ("v1","latest","v1.0.0/evil","v01.0.0"):
            with self.assertRaises(ValueError):m.prefix_for(invalid)
        self.assertEqual(m.prefix_for("v0.1.0"),
            "releases/v0.1.0/res-2026.06")
    def test_publish_and_verify_manifest_last(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);self.setup_release(root);s3=FakeS3()
            m.publish(root,"v0.1.0",client=s3)
            self.assertEqual(len(s3.objects),3)
            self.assertTrue(s3.calls[-1].endswith("/release-manifest.json"))
            self.assertEqual(json.loads(s3.objects[s3.calls[-1]])["status"],"PUBLISHED")
            self.assertEqual(json.loads((root/"release-manifest.json").read_text())["status"],
                             "LOCAL_VERIFIED_NOT_PUBLISHED")
    def test_existing_key_blocks_all_writes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);self.setup_release(root);s3=FakeS3()
            key=m.prefix_for("v0.1.0")+"/virtual-desktop.tar.gz"
            s3.objects[key]=b"existing"
            with self.assertRaises(FileExistsError):m.publish(root,"v0.1.0",client=s3)
            self.assertEqual(s3.calls,[])
            self.assertEqual(s3.objects[key],b"existing")
    def test_bad_local_checksum_blocks_all_writes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);self.setup_release(root);s3=FakeS3()
            (root/"derived"/m.ARTIFACTS[0]).write_bytes(b"corrupt")
            with self.assertRaises(ValueError):m.publish(root,"v0.1.0",client=s3)
            self.assertEqual(s3.calls,[])

if __name__=="__main__":unittest.main()
