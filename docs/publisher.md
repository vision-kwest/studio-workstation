# Named on-demand publisher

Publisher now shares the native CloudFormation lifecycle with desktops, without
installing a desktop or acquiring the shared EIP. Existing software publishing
bootstrap and explicit build/test/prod promotion remain unchanged. The dedicated
`StudioEfsCiPublisher` profile and existing EFS client security group are inputs,
not resources owned by the disposable publisher stack.

```bash
python3 studio workstation create --profile publisher --name publish \
  --security-group-id sg-004ad23682c7db931
python3 studio workstation check-ready publish
python3 studio workstation run publish --operation check
python3 studio workstation run publish --operation build \
  --recipe-repo vision-kwest/REPO --recipe-ref FULL_COMMIT_SHA --recipe-path recipes/build.sh
python3 studio workstation result publish --command-id COMMAND_UUID
python3 studio workstation run publish --operation promote-test --package usd --version 26.05
python3 studio workstation run publish --operation promote-prod --package usd --version 26.05
python3 studio workstation stop publish
python3 studio workstation destroy publish --delete-stack
```

Replace the recipe placeholders with a reviewed pinned recipe. Build only writes
to the existing dev workflow; test/prod promotion is explicit. Retrieve SSM results
before claiming publishing succeeded. `check-ready` proves bootstrap and dev write
access; it does not prove a particular package builds. Stop/start preserves local
EBS; destroy discards it natively with the instance. Shared package data is preserved.

Raw `publisher-* --instance-id` commands are retired, not parallel supported paths.
See [Studio CLI](studio-cli.md#publisher) and Issue #98 for branch/acceptance status.
