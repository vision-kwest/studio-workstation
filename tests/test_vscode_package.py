"""Exercise the publisher recipe with real Rez and small local archive fixtures."""
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import tarfile

import pytest

ROOT = Path(__file__).resolve().parents[1]
STANDARDS = Path(os.environ.get("STUDIO_REZ_TEST_ROOT", ROOT.parent / "studio-rez"))


def run(command, env):
    return subprocess.run(command, env=env, text=True, capture_output=True, timeout=45)


@pytest.fixture
def package(tmp_path, request):
    if not shutil.which("rez-build") or not (STANDARDS / "env/studio-dev.sh").is_file():
        pytest.skip("Requires Rez 3.4 on PATH and STUDIO_REZ_TEST_ROOT or sibling studio-rez")
    scenario = getattr(request, "param", "good")
    recipe = tmp_path / "recipe"
    shutil.copytree(ROOT / "software/vscode", recipe,
                    ignore=shutil.ignore_patterns("build", "__pycache__"))
    manifest = json.loads((recipe / "manifest.json").read_text())
    archive = tmp_path / "vscode.tar.gz"
    members = {
        "VSCode-linux-x64/bin/code": b"#!/bin/sh\necho vscode-fixture\n",
        "VSCode-linux-x64/code": b"#!/bin/sh\nexit 0\n",
        "VSCode-linux-x64/resources/app/package.json": json.dumps({
            "version": "0.0.0" if scenario == "version" else manifest["version"]}).encode(),
        "VSCode-linux-x64/resources/app/product.json": json.dumps({
            "commit": manifest["source_commit"]}).encode(),
    }
    if scenario == "traversal":
        members["../escaped"] = b"must not escape"
    with tarfile.open(archive, "w:gz") as bundle:
        for name, data in members.items():
            item = tarfile.TarInfo(name)
            item.size, item.mode = len(data), 0o755 if name.endswith("/code") else 0o644
            bundle.addfile(item, io.BytesIO(data))
    manifest["source_sha256"] = ("0" * 64 if scenario == "checksum"
                                else hashlib.sha256(archive.read_bytes()).hexdigest())
    (recipe / "manifest.json").write_text(json.dumps(manifest))
    root = tmp_path / "packages"
    (root / "dev").mkdir(parents=True)
    env = dict(os.environ, STUDIO_PACKAGE_ROOT=str(root), STUDIO_REZ_ROOT=str(STANDARDS),
               STUDIO_VSCODE_ARCHIVE=str(archive), REZ_PACKAGES_PATH=str(root / "dev"),
               REZ_CONFIG_FILE=str(STANDARDS / "config/rezconfig.dev.py"))
    for name in ("platform", "arch", "os"):
        result = run(["rez-bind", "--install-path", str(root / "dev"), name], env)
        assert result.returncode == 0, result.stdout + result.stderr
    return recipe, root, env, manifest, scenario


@pytest.mark.parametrize("package", ["good", "checksum", "version", "traversal"], indirect=True)
def test_publish_validates_before_exposing_version(package):
    recipe, root, env, manifest, scenario = package
    destination = root / "dev/vscode" / manifest["version"]
    result = run(["bash", str(recipe / "publish.sh")], env)
    output = result.stdout + result.stderr
    assert not list((root / "dev").glob(".vscode-build.*"))
    if scenario == "good":
        assert result.returncode == 0, output
        assert json.loads((destination / "manifest.json").read_text()) == manifest
        resolved = run(["rez-env", "vscode-" + manifest["version"], "--", "code"], env)
        assert resolved.returncode == 0, resolved.stdout + resolved.stderr
        assert resolved.stdout.strip() == "vscode-fixture"
        repeated = run(["bash", str(recipe / "publish.sh")], env)
        assert repeated.returncode != 0 and "Refusing existing package" in repeated.stderr
        assert json.loads((destination / "manifest.json").read_text()) == manifest
        assert (destination / "app/bin/code").read_bytes() == b"#!/bin/sh\necho vscode-fixture\n"
        assert not list((root / "dev").glob(".vscode-build.*"))
    else:
        assert result.returncode != 0, output
        expected = {"checksum": "SHA-256 mismatch", "version": "payload version/commit mismatch",
                    "traversal": "outside the destination"}[scenario]
        assert expected in output
        assert not destination.exists() and not destination.is_symlink()


@pytest.mark.parametrize("existing", ["directory", "broken_symlink"])
def test_existing_version_is_preserved(package, existing):
    recipe, root, env, manifest, _ = package
    destination = root / "dev/vscode" / manifest["version"]
    destination.parent.mkdir()
    if existing == "broken_symlink":
        destination.symlink_to("missing-target")
    else:
        destination.mkdir()
        (destination / "keep").write_bytes(b"existing package must survive")
    env["STUDIO_VSCODE_ARCHIVE"] = "/missing-archive-must-not-be-read"
    result = run(["bash", str(recipe / "publish.sh")], env)
    assert result.returncode != 0
    assert "Refusing existing package" in result.stderr
    assert not list((root / "dev").glob(".vscode-build.*"))
    if existing == "broken_symlink":
        assert destination.is_symlink() and os.readlink(destination) == "missing-target"
    else:
        assert (destination / "keep").read_bytes() == b"existing package must survive"
