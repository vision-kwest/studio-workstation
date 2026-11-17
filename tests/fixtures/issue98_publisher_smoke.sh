#!/bin/bash
# Disposable acceptance fixture; never overwrite an existing package family.
set -euo pipefail
test "${STUDIO_PACKAGE_ROOT:?}" = /opt/studio/packages
mountpoint -q "$STUDIO_PACKAGE_ROOT"
python3 - <<'PY'
import os
from pathlib import Path
root = Path(os.environ['STUDIO_PACKAGE_ROOT'])
name = 'issue98_smoke_20261117'
for stage in ('dev', 'test', 'prod', 'archive', 'artifacts'):
    if os.path.lexists(root / stage / name):
        raise SystemExit('Refusing existing acceptance package family: ' + stage)
path = root / 'dev' / name / '0.1'
path.mkdir(parents=True, exist_ok=False)
(path / 'package.py').write_text("name = 'issue98_smoke_20261117'\nversion = '0.1'\ndef commands():\n    env.STUDIO_ISSUE98_SMOKE = '{root}/payload.txt'\n")
(path / 'payload.txt').write_text('Studio Issue98 isolated publisher lifecycle acceptance\n')
(path / '.studio-acceptance-owner').write_text('issue98-auto-20261117\n')
print('ISSUE98_ISOLATED_DEV_PACKAGE_CREATED', path)
PY
