# On-demand publisher (merged PR #76)

The publisher uses a dedicated EC2 IAM instance profile, SSM, and IAM/TLS EFS. An EC2 publisher is created only on demand and terminated when tests/work are complete; the IAM identity is retained for reuse. It does **not** use DCV, the shared EIP, or a GitHub Actions runner. Its bootstrap clones public `studio-rez` at pinned commit `2fee37e32a09c337d9486e00bcc2a16c3ff4b0fb`.

```bash
python3 studio workstation publisher-create --security-group-id sg-004ad23682c7db931
python3 studio workstation publisher-status --instance-id <publisher-instance-id>
python3 studio workstation publisher-run --instance-id <publisher-instance-id> --operation check
python3 studio workstation publisher-run --instance-id <publisher-instance-id> --operation build --recipe-repo vision-kwest/studio-workstation --recipe-ref <reviewed-40-character-commit> --recipe-path tests/fixtures/publisher_acceptance_build.sh
python3 studio workstation publisher-result --instance-id <publisher-instance-id> --command-id <ssm-command-id>
python3 studio workstation publisher-run --instance-id <publisher-instance-id> --operation promote-test --name publisheraccept --version 0.1
python3 studio workstation publisher-run --instance-id <publisher-instance-id> --operation promote-prod --name publisheraccept --version 0.1
python3 studio workstation publisher-stop --instance-id <publisher-instance-id>
python3 studio workstation publisher-start --instance-id <publisher-instance-id>
```

The build operation runs an explicitly pinned shell recipe from a `vision-kwest` GitHub repository; it submits a long-running SSM command and returns its command ID. It does not build OpenUSD automatically without an OpenUSD recipe. Only execute reviewed recipes. The publisher has EFS write permission across the filesystem to support promotion; the current implementation does **not** sandbox build recipes to dev. Promotion rejects existing destinations.

Live acceptance: fresh bootstrap, public Git clone, IAM/TLS EFS, pinned generic test recipe, dev→test→prod reference promotion, non-root dev write, stop/start persistence and cleanup passed on the historical `i-0499bf014d1fc86eb` test instance. The CLI/parser regression tests passed and PR #76 merged. **That test instance has since been terminated; no publisher EC2 remains.** A full OpenUSD Docker build through this profile is not yet accepted. Avoid running two publishers concurrently.
