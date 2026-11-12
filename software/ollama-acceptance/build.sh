#!/usr/bin/env bash
set -euo pipefail
# This is a new upstream download, never a copy of the existing EFS package.
: "${REZ_BUILD_INSTALL_PATH:?Run through rez-build --install}"
url="https://github.com/ollama/ollama/releases/download/v0.40.16/ollama-linux-amd64.tgz"
work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT
curl --fail --location --retry 3 "$url" --output "$work/ollama.tgz"
sha256sum "$work/ollama.tgz" | tee "$work/source.sha256"
mkdir -p "$work/unpacked"
tar -xzf "$work/ollama.tgz" -C "$work/unpacked"
test -x "$work/unpacked/bin/ollama"
test ! -e "$REZ_BUILD_INSTALL_PATH/bin/ollama"
mkdir -p "$REZ_BUILD_INSTALL_PATH"
cp -a "$work/unpacked/." "$REZ_BUILD_INSTALL_PATH/"
cp "$work/source.sha256" "$REZ_BUILD_INSTALL_PATH/source.sha256"
printf '%s\\n' "$url" > "$REZ_BUILD_INSTALL_PATH/source.url"
"$REZ_BUILD_INSTALL_PATH/bin/ollama" --version
