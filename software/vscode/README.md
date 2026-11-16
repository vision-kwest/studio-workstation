# VS Code Rez package

Packages the official Microsoft **VS Code 1.146.0, Linux x86_64** desktop application
for Ubuntu 24.04 CPU/GPU DCV workstations. Tracks [Issue #111](https://github.com/vision-kwest/studio-workstation/issues/111)
and applies the workflow in [packaging guide PR #95](https://github.com/vision-kwest/studio-workstation/pull/95).

## Package and writable state

`manifest.json` pins Microsoft's download URL, release commit and SHA-256. The
build verifies all three against the downloaded archive and embedded metadata.
The complete upstream application, resources and license notices are retained
under `{root}/app`; Rez exposes the original `app/bin/code` launcher.

The package is `vscode-1.146.0`. Settings use the user's normal `~/.config/Code`
directory and extensions use `~/.vscode/extensions`. Do not create portable
`app/data/`: it would redirect writable state into the shared software tree.
Application updates are new reviewed Rez versions, followed by explicit promotion.

## Build with the existing publisher

The publisher requires Python 3.12+, an installed Rez 3.4.0 runtime, the pinned
`studio-rez` standards checkout, bound `platform`/`arch`/`os` packages, and authorized
EFS writes. Its existing bootstrap installs the standards at `/opt/studio/rez`;
**`publisher-create` alone does not install the Rez runtime.** Verify these
prerequisites on the selected publisher; workstation bootstrap is not a publisher
installer. CloudShell needs the Studio CLI, not a local Rez installation.

From a reviewed checkout containing this recipe and the three-component promotion
version fix:

```bash
: "${PUBLISHER_ID:?Set the exact existing publisher instance ID}"
python3 studio workstation publisher-status --instance-id "$PUBLISHER_ID"
# If stopped, start it and wait for SSM before the next command:
# python3 studio workstation publisher-start --instance-id "$PUBLISHER_ID"
python3 studio workstation publisher-run --instance-id "$PUBLISHER_ID" --operation check
recipe_ref=$(git rev-parse HEAD)
python3 studio workstation publisher-run \
  --instance-id "$PUBLISHER_ID" --operation build \
  --recipe-repo vision-kwest/studio-workstation --recipe-ref "$recipe_ref" \
  --recipe-path software/vscode/publish.sh
# Use the command ID returned above; build submission is not build success.
python3 studio workstation publisher-result \
  --instance-id "$PUBLISHER_ID" --command-id "$COMMAND_ID"
```

Require `Success` and `VSCODE_PUBLISHED` before continuing. `publish.sh` uses the
existing Studio validator and `rez-build --install --prefix` against a fresh
same-filesystem staging repository. Only a successful complete build is moved
into `/opt/studio/packages/dev/vscode/1.146.0`; existing versions and symlinks are
refused. A failed build removes its own staging directory only. Use this outer
script rather than building directly into a public stage: Rez can delete its
install target when a build fails.

For a shell already on the authorized publisher, the equivalent entry point is:

```bash
STUDIO_PACKAGE_ROOT=/opt/studio/packages bash software/vscode/publish.sh
```

Set `STUDIO_REZ_ROOT` only if its standards checkout lives elsewhere. An optional
`STUDIO_VSCODE_ARCHIVE=/path/to/download.tar.gz` reuses a local download; the same
pinned SHA-256 is still mandatory. Neither path bypasses verification.

## Validate, then promote

In an ordinary user's DCV terminal, select the dev package explicitly:

```bash
export PATH="/opt/studio/rez/3.4.0/bin/rez:$PATH"
source /opt/studio/studio-rez/env/studio-dev.sh
rez-env vscode-1.146.0 -- code --version
rez-env vscode-1.146.0 -- code --new-window .
```

The version output must be `1.146.0`, commit
`69047191fc427f2f92828279aae1d90d8eeda7ab`, and `x64`. Also confirm the resolved
`REZ_VSCODE_ROOT` points to the intended stage. Close an existing VS Code instance
before testing a different Rez environment, or give it a distinct writable
`--user-data-dir`; an existing instance may retain its original environment.

After dev GUI acceptance, run the existing explicit promotions:

```bash
python3 studio workstation publisher-run --instance-id "$PUBLISHER_ID" \
  --operation promote-test --name vscode --version 1.146.0
# Validate the test stage before promoting to prod.
python3 studio workstation publisher-run --instance-id "$PUBLISHER_ID" \
  --operation promote-prod --name vscode --version 1.146.0
python3 studio workstation publisher-stop --instance-id "$PUBLISHER_ID"
```

For test-only resolution after sourcing the environment, use
`REZ_PACKAGES_PATH=/opt/studio/packages/test rez-env vscode-1.146.0 -- code --version`;
the test repository must also contain the bound core packages. The pinned
`studio-rez` helper moves the dev payload into
`artifacts/vscode/1.146.0/published` and creates stage references. Promotion reuses
the payload, rejects existing destinations, and does not download another copy.

After production acceptance, everyday launch is:

```bash
studio-rez-env vscode-1.146.0 -- code --new-window .
```

## Acceptance and troubleshooting

- Verify checksum, embedded version/commit, real Rez resolution, and `code --version`.
- Open a project through DCV as the normal desktop user, edit/save a file, and
  open the integrated terminal. Check the intended Rez tools are available there.
- Install a chosen extension and change a setting; close/reopen to check persistence.
  Confirm the EFS package remains read-only and mutable data stays in the user home.
- Repeat the GUI checks on CPU and GPU workstations. `--version` runs Electron's
  Node CLI and does not prove GUI startup or sandbox operation.
- On Ubuntu 24.04, inspect AppArmor/user-namespace denials if Electron reports a
  sandbox error at the custom EFS path. A narrowly scoped application profile may
  be needed; diagnose on the actual host before changing its policy. The recipe
  does not disable the sandbox, change system namespace policy, or launch as root.
- For an observed blank DCV window, try Microsoft's documented `--disable-gpu`
  troubleshooting flag. It is not a package default.

Rollback: stop requesting this version and select a previously accepted version,
if one exists. Keep settings/extensions and other packages intact. Do not overwrite
an existing version to repair it; publish a deliberate new version.

**Local evidence (2026-11-15 Pacific):** Debian 13 x86_64, Python 3.12.14, disposable
Rez 3.4.0 validation environment. The 409,004,073-byte official archive matched
the pinned SHA-256. A real staged build, Rez resolve, `code --version`, and
`code --list-extensions` passed with the package files set read-only. The unchanged
Studio helper promoted dev → test → prod; stage-only resolves reported the same
version/commit, all three stages referenced the same payload, and repeated
promotion was refused. The package/publisher/Studio CLI tests passed (73 tests,
27 subtests). CI uses small synthetic archives for failure-path tests.

**Live acceptance remains pending:** AWS read-only inventory returned no
nonterminated EC2 or managed SSM instances in us-east-1. No live package was
published, no CPU/GPU GUI launch was claimed, and no AWS resources were changed.
See Issue #111 for the live ledger. A recipe being merged or a build being
submitted is not proof of installation on a workstation.

## Sources

- [Microsoft version metadata](https://update.code.visualstudio.com/api/versions/1.146.0/linux-x64/stable)
- [Linux installation](https://code.visualstudio.com/docs/setup/linux)
- [Portable mode and writable state](https://code.visualstudio.com/docs/setup/portable)
- [VS Code CLI](https://code.visualstudio.com/docs/configure/command-line)
- [Ubuntu 24.04 release notes: user-namespace restrictions](https://documentation.ubuntu.com/release-notes/24.04/)
- [Rez 3.4 build command](https://rez.readthedocs.io/en/3.4.0/commands/rez-build.html)
- [Studio publisher](../../docs/publisher.md)
