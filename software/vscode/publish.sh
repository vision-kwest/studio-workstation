#!/usr/bin/env bash
# Entry point for Studio publisher-run; never build over a public package version.
set -euo pipefail
package_root="${STUDIO_PACKAGE_ROOT:?Set the authorized Studio package root}"
source "${STUDIO_REZ_ROOT:-/opt/studio/rez}/env/studio-dev.sh"
export STUDIO_PACKAGE_ROOT="$package_root"
export REZ_PACKAGES_PATH="$package_root/dev:$package_root/test:$package_root/prod"
export PATH="/opt/studio/rez/3.4.0/bin/rez:$PATH"
command -v rez-build >/dev/null || { echo "Publisher requires Rez 3.4.0 on PATH" >&2; exit 1; }
cd -- "$(dirname -- "${BASH_SOURCE[0]}")"
studio-rez-validate-package .
version=$(python3 -c 'import json; print(json.load(open("manifest.json"))["version"])')
destination="$package_root/dev/vscode/$version"
if test -e "$destination" || test -L "$destination"; then
    echo "Refusing existing package: $destination" >&2
    exit 1
fi
test -d "$package_root/dev" && test -w "$package_root/dev"
staging=$(mktemp -d "$package_root/dev/.vscode-build.XXXXXXXX")
trap 'rm -rf -- "$staging"' EXIT
rez-build --install --prefix "$staging"
staged="$staging/vscode/$version"
studio-rez-validate-package "$staged"
mkdir -p -- "$(dirname -- "$destination")"
mv -Tn -- "$staged" "$destination"
test ! -e "$staged" || { echo "Publication raced with an existing package" >&2; exit 1; }
echo "VSCODE_PUBLISHED: $destination"
