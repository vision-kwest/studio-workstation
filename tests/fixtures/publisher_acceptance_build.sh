#!/usr/bin/env bash
# Isolated acceptance recipe for generic publisher execution.
set -euo pipefail
root="${STUDIO_PACKAGE_ROOT:?}/dev/publisheraccept/0.1"
test ! -e "$root"
mkdir -p "$root"
printf 'name = "publisheraccept"\nversion = "0.1"\ndef commands():\n    pass\n' > "$root/package.py"
echo PUBLISHER_RECIPE_BUILD_OK
