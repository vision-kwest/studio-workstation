# Retired: fixed-CPU return-to-owner runbook

Retired by Issue #98 on November 16, 2026. This is not a supported deployment path.
The prior document and implementation remain in Git history at pre-integration
commit `1e3efa077f141aabd7adfc936a947f51621873e4`.

Use [Studio CLI](studio-cli.md): named CloudFormation workstations, one disposable
EC2/EBS lifecycle, explicit hostname assignment/release and native workstation
expiration. No separate volume-cleanup controller or historical CPU anchor.

Historical evidence is not a claim of current multi-workstation browser acceptance.
