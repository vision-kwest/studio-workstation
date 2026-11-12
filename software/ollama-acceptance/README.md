# Isolated Ollama acceptance (dev only)

This uses the existing **studio-rez** validator and **rez-build**, not a custom publisher.
Package name `ollama_acceptance` deliberately cannot overwrite `ollama/0.40`.
The upstream archive is downloaded fresh from the official release URL, not from EFS.

From CloudShell (with Rez 3.4 and an EFS-writable publisher environment):

```bash
source ../studio-rez/env/studio-dev.sh
cd studio-workstation/software/ollama-acceptance
studio-rez-doctor --soft
studio-rez-validate-package .
rez-build --install --prefix /opt/studio/packages/dev
rez-env ollama_acceptance-0.40.16 -- ollama --version
```

Capture the full command output and archive SHA-256. No test/prod promotion.
**Stop if** the build environment lacks Rez or publisher EFS write permissions;
do not replace the standard workflow with a manual installation.
The archive layout and build_command behavior must be checked in the live test.
