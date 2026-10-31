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
import copy
import gzip

EXPECTED = {
 "installation-scripts.tar.gz":"2e3b0e4b198582ea03b1926aaaf76da21688f2e6809ad4d153b95a9417c72658",
 "virtual-desktop.tar.gz":"f5d2297537bda746fe74411c18140bcd0f2d4322852fa9d32ce7606a93aed7a8",
}
ROOT=Path(__file__).resolve().parent/"overrides"

def patch_dcv_dpms(raw):
    """Fix Ubuntu echo -e portability in the original RES 2026.06 installer."""
    old = b"""  echo -e '
Section "Extensions"
    Option      "DPMS" "Disable"
EndSection' > /etc/X11/xorg.conf.d/99-disable-dpms.conf"""
    new = b"""  printf 'Section "Extensions"\\n    Option "DPMS" "Disable"\\nEndSection\\n' > /etc/X11/xorg.conf.d/99-disable-dpms.conf"""
    if raw.count(old) != 1:
        raise ValueError("Pinned RES DPMS source anchor changed")
    return raw.replace(old, new)

def patch_xserver_dcv_access(raw):
    """Grant only the local DCV service user, only in standalone mode."""
    old = b'        return "SI:localuser:dcv" in xhost_output'
    new = b'''        if os.getenv("STUDIO_STANDALONE") == "true" and "SI:localuser:dcv" not in xhost_output:
            subprocess.check_output(["xhost", "+SI:localuser:dcv"], env=env, text=True)
            xhost_output = subprocess.check_output(["xhost"], env=env, text=True)

        return "SI:localuser:dcv" in xhost_output'''
    if raw.count(old) != 1:
        raise ValueError("Pinned RES Xorg validator anchor changed")
    return raw.replace(old, new)
def rewrite(raw, mapping, additions=None, replacements=None):
    """Copy a tarball, changing only explicitly listed members.

    Preserve source metadata and fail on unexpected file types or missing
    targets. Additions are limited to a new file in an existing package.
    """
    additions = additions or {}
    replacements = replacements or {}
    out=io.BytesIO()
    tar_buffer=io.BytesIO()
    found=set()
    existing=set()
    with tarfile.open(fileobj=io.BytesIO(raw),mode="r:gz") as src:
        with tarfile.open(fileobj=tar_buffer,mode="w",format=tarfile.PAX_FORMAT) as dst:
            for original in src:
                member=copy.copy(original)
                name=PurePosixPath(member.name)
                if member.name.startswith("/") or ".." in name.parts or not (member.isfile() or member.isdir()):
                    raise ValueError("Unsafe archive entry "+member.name)
                key=member.name.removeprefix("./")
                if key in existing:
                    raise ValueError("Duplicate archive member "+key)
                existing.add(key)
                data=src.extractfile(original).read() if member.isfile() else None
                if key in mapping or key in replacements:
                    if not member.isfile():
                        raise ValueError("Cannot replace directory "+key)
                    data=mapping[key].read_bytes() if key in mapping else replacements[key]
                    member.size=len(data)
                    found.add(key)
                dst.addfile(member,io.BytesIO(data) if data is not None else None)
            missing=(set(mapping)|set(replacements))-found
            if missing:
                raise ValueError("Original archive missing override paths: "+str(sorted(missing)))
            for name,source in sorted(additions.items()):
                path=PurePosixPath(name)
                if name.startswith("/") or ".." in path.parts or name in existing:
                    raise ValueError("Unsafe or duplicate addition "+name)
                data=source.read_bytes()
                info=tarfile.TarInfo(name)
                info.size=len(data)
                info.mode=0o644
                info.mtime=0
                info.uid=info.gid=0
                info.uname=info.gname=""
                dst.addfile(info,io.BytesIO(data))
    # Gzip mtime is otherwise the current wall-clock time. Keep original
    # tar member metadata; normalize only the outer compression wrapper.
    with gzip.GzipFile(fileobj=out,mode="wb",filename="",mtime=0,compresslevel=9) as gz:
        gz.write(tar_buffer.getvalue())
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
        with tarfile.open(fileobj=io.BytesIO(raw),mode="r:gz") as tf:
            matches=[m.name.removeprefix("./") for m in tf if m.isfile()
                     and m.name.removeprefix("./").endswith("scripts/dcv/linux/debian/dcv_server.sh")]
            if len(matches)!=1:
                raise ValueError("Cannot locate unique RES Ubuntu DCV installer")
            original=tf.extractfile(next(m for m in tf if m.name.removeprefix("./")==matches[0])).read()
        output=rewrite(raw,mapping,replacements={matches[0]:patch_dcv_dpms(original)})
    else:
        with tarfile.open(fileobj=io.BytesIO(raw),mode="r:gz") as tf:
            members={m.name.removeprefix("./"):tf.extractfile(m).read() for m in tf if m.isfile()}
        nested={}
        for package,prefix,files in [
            ("idea-bootstrap-lib.tar.gz","ideabootstrap",[
                "dcv/linux/dcv_host.py","dcv/linux/gl.py","dcv/linux/x_server.py","dcv/dcv.py"]),
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
            if prefix=="ideabootstrap":
                xmatches=[n for n in names if n.endswith("/ideabootstrap/dcv/linux/x_server.py")]
                if len(xmatches)!=1:
                    raise ValueError("Cannot locate unique pinned RES Xorg validator")
                with tarfile.open(fileobj=io.BytesIO(members[package]),mode="r:gz") as tf:
                    original=tf.extractfile(next(m for m in tf if m.name.removeprefix("./")==xmatches[0])).read()
                mapping.pop(xmatches[0])
                nested[package]=rewrite(members[package],mapping,
                    replacements={xmatches[0]:patch_xserver_dcv_access(original)})
            elif prefix=="ideavirtualdesktop":
                # Keep original source distribution layout and setup metadata.
                # setuptools package discovery includes this .py in the existing
                # ideavirtualdesktop.app package; no new package is introduced.
                parents=[n.rsplit("/",1)[0] for n in names
                         if n.endswith("/ideavirtualdesktop/app/__init__.py")]
                if len(parents)!=1:
                    raise ValueError("Cannot identify original app package root")
                entry=parents[0]+"/standalone_bootstrap.py"
                if entry in names:
                    raise ValueError("Standalone entry already exists in source archive")
                nested[package]=rewrite(members[package],mapping,
                    additions={entry:ROOT/"ideavirtualdesktop/app/standalone_bootstrap.py"})
        # Replace only the two modified nested source distributions. Other
        # original application archive members remain unchanged.
        output=rewrite(raw,{},replacements=nested)
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_bytes(output)
    print(args.output,hashlib.sha256(output).hexdigest())
if __name__=="__main__":
    main()
