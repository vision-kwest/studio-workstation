"""Repackage the pinned Microsoft archive; publish.sh owns the safe destination."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import tarfile
import tempfile
import urllib.request


def build():
    source = Path(__file__).resolve().parent
    manifest = json.loads((source / "manifest.json").read_text())
    if os.environ.get("REZ_BUILD_INSTALL") != "1":
        raise RuntimeError("Use publish.sh to build into an isolated Rez repository")
    if (os.environ["REZ_BUILD_PROJECT_NAME"], os.environ["REZ_BUILD_PROJECT_VERSION"]) != (
        manifest["name"], manifest["version"]
    ):
        raise ValueError("Package metadata does not match the pinned manifest")
    install = Path(os.environ["REZ_BUILD_INSTALL_PATH"])
    with tempfile.TemporaryDirectory(prefix="studio-vscode-") as temporary:
        work = Path(temporary)
        archive = Path(os.environ.get("STUDIO_VSCODE_ARCHIVE", work / "vscode.tar.gz"))
        if "STUDIO_VSCODE_ARCHIVE" not in os.environ:
            with urllib.request.urlopen(manifest["source_url"], timeout=60) as response:
                with archive.open("wb") as stream:
                    shutil.copyfileobj(response, stream)
        with archive.open("rb") as stream:
            if hashlib.file_digest(stream, "sha256").hexdigest() != manifest["source_sha256"]:
                raise ValueError("VS Code archive SHA-256 mismatch")
        with tarfile.open(archive, "r:gz") as bundle:
            bundle.extractall(work, filter="data")
        app = work / "VSCode-linux-x64"
        version = json.loads((app / "resources/app/package.json").read_text())["version"]
        commit = json.loads((app / "resources/app/product.json").read_text())["commit"]
        if (version, commit) != (manifest["version"], manifest["source_commit"]):
            raise ValueError("VS Code payload version/commit mismatch")
        if not os.access(app / "bin/code", os.X_OK) or not os.access(app / "code", os.X_OK):
            raise ValueError("VS Code executables missing from the archive")
        if (app / "data").exists():
            raise ValueError("Portable user data must not be inside the shared package")
        shutil.move(str(app), install / "app")
        shutil.copy2(source / "manifest.json", install / "manifest.json")


if __name__ == "__main__":
    build()
