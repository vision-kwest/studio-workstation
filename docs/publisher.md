# On-demand publisher (draft PR #76)

The publisher uses a dedicated EC2 IAM instance profile, SSM, and IAM/TLS EFS. It does **not** use DCV, the shared EIP, or a GitHub Actions runner. Its bootstrap clones public `studio-rez` at pinned commit `2fee37e32a09c337d9486e00bcc2a16c3ff4b0fb`.

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

Live acceptance: fresh bootstrap, public Git clone, IAM/TLS EFS, generic isolated test recipe, dev→test→prod promotion, non-root dev write, stop/start persistence and cleanup passed on `i-0499bf014d1fc86eb` (stopped). The CLI itself and its new parser/tests still require CI verification. Avoid running two publishers concurrently.
