#!/usr/bin/env python3
"""Offline validation of original RES archive overlays; no installer execution."""
import ast
import hashlib
import io
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile

ROOT=Path(__file__).resolve().parents[1]
BUILDER=ROOT/"runtime/build_res_overlay.py"
EXPECTED={
    "installation-scripts.tar.gz":{
        "scripts/virtual-desktop-host/linux/install.sh",
        "scripts/common/linux/install_app.sh",
        "scripts/dcv/linux/debian/dcv_server.sh",
        "scripts/virtual-desktop-host/linux/install_post_reboot.sh",
    },
    "virtual-desktop.tar.gz":{
        "idea-bootstrap-lib.tar.gz",
        "idea-virtual-desktop-lib.tar.gz",
    },
}
def files(blob):
    with tarfile.open(fileobj=io.BytesIO(blob),mode="r:gz") as tf:
        return {m.name.removeprefix("./"):tf.extractfile(m).read()
                for m in tf if m.isfile()}
def run(directory):
    with tempfile.TemporaryDirectory() as temp:
        for filename, required in EXPECTED.items():
            src=Path(directory)/filename
            dst=Path(temp)/(filename+".modified")
            subprocess.run([sys.executable,str(BUILDER),"--input",str(src),
                            "--output",str(dst)],check=True)
            # Rebuild independently; output bytes must be identical.
            repeat=Path(temp)/(filename+".repeat")
            subprocess.run([sys.executable,str(BUILDER),"--input",str(src),
                            "--output",str(repeat)],check=True)
            assert dst.read_bytes()==repeat.read_bytes(), "Nondeterministic archive: "+filename
            original=files(src.read_bytes())
            modified=files(dst.read_bytes())
            assert required <= modified.keys()
            if filename=="installation-scripts.tar.gz":
                assert original.keys()==modified.keys()
                for name in original:
                    if name not in required:
                        assert original[name]==modified[name],name
                assert b"STUDIO_STANDALONE" in modified["scripts/common/linux/install_app.sh"]
                assert b"install_post_reboot.sh" in modified["scripts/virtual-desktop-host/linux/install.sh"]
                assert b"printf" in modified["scripts/dcv/linux/debian/dcv_server.sh"]
                assert b"STUDIO_STANDALONE" in modified["scripts/virtual-desktop-host/linux/install_post_reboot.sh"]
            else:
                assert original.keys()==modified.keys()
                for name in original:
                    if name not in required:
                        assert original[name]==modified[name],name
                for name in required:
                    a=files(original[name])
                    b=files(modified[name])
                    for member in a:
                        if not member.endswith(("/dcv_host.py","/gl.py","/x_server.py","/dcv.py",
                                                "/bootstrap.py","/post_reboot.py")):
                            assert a[member]==b[member],member
                    for member,data in b.items():
                        if member.endswith(".py"):
                            ast.parse(data,filename=member)
                    if name=="idea-virtual-desktop-lib.tar.gz":
                        added=set(b)-set(a)
                        assert len(added)==1,added
                        assert next(iter(added)).endswith("/ideavirtualdesktop/app/standalone_bootstrap.py")
                assert any(b"STUDIO_STANDALONE" in data for data in files(modified["idea-bootstrap-lib.tar.gz"]).values())
            print("PASS",filename,hashlib.sha256(dst.read_bytes()).hexdigest())
    print("Overlay archive tests passed. No RES runtime executed.")
if __name__=="__main__":
    run(sys.argv[1])
