#!/usr/bin/env bash
# Publish pinned Ollama Linux AMD64 as a Rez package to Studio dev.
set -euo pipefail
upstream_version=0.40.16
package_version=0.40
sha256=87bfafb2acbc89b2e6a1be0a9043de6aa75fb450696f86bc426e65fc448f788d
url="https://github.com/ollama/ollama/releases/download/v${upstream_version}/ollama-linux-amd64.tar.zst"
root="${STUDIO_PACKAGE_ROOT:?}/dev/ollama"
target="${root}/${package_version}"
test "$(uname -m)" = x86_64
mountpoint -q "${STUDIO_PACKAGE_ROOT}"
test -w "${STUDIO_PACKAGE_ROOT}/dev"
test ! -e "${target}"
command -v curl >/dev/null
if ! command -v zstd >/dev/null; then
  apt-get update -qq
  DEBIAN_FRONTEND=noninteractive apt-get install -y zstd
fi
work="$(mktemp -d /tmp/studio-ollama.XXXXXXXX)"
trap 'rm -rf "$work"' EXIT
mkdir -p "$work/payload"
curl --fail --location --retry 3 --output "$work/ollama.tar.zst" "$url"
printf '%s  %s\n' "$sha256" "$work/ollama.tar.zst" | sha256sum --check --status
tar --use-compress-program=unzstd -xf "$work/ollama.tar.zst" -C "$work/payload"
test -x "$work/payload/bin/ollama"
cat > "$work/payload/package.py" <<'PACKAGE'
name = "ollama"
version = "0.40"
description = "Ollama 0.40.16 official Linux AMD64 runtime (CPU/NVIDIA)"
def commands():
    env.PATH.prepend("{root}/bin")
PACKAGE
cat > "$work/payload/manifest.json" <<MANIFEST
{"name":"ollama","rez_version":"0.40","upstream_version":"0.40.16","source":"$url","sha256":"$sha256","architecture":"linux-amd64"}
MANIFEST
mkdir -p "$root"
stage="$(mktemp -d "$root/.0.40.tmp.XXXXXXXX")"
trap 'rm -rf "$work" "$stage"' EXIT
cp -a "$work/payload/." "$stage/"
test -x "$stage/bin/ollama"
test -f "$stage/package.py"
test -f "$stage/manifest.json"
chmod -R a+rX "$stage"
test ! -e "$target"
mv -T "$stage" "$target"
trap 'rm -rf "$work"' EXIT
echo "OLLAMA_PUBLISH_OK $target upstream=$upstream_version"
