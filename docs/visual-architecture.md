# Studio Workstation — architecture

![Architecture showing GitHub, S3, Image Builder, AMIs, CloudFormation, EC2 and DCV](images/system-architecture.svg)

The image records earlier image-build work. Workstation operation is separate:
the Studio CLI renders a RES-derived CloudFormation template and launches a named
EC2 instance. Current GPU launches use the Canonical base with the pinned RES
runtime installer; the lifecycle includes automatic stop and explicit destruction.

## Provisioning relationships

```mermaid
flowchart TD
  Repo["GitHub source and tests"] --> Release["Versioned RES 2026.06 archives in S3"]
  Ubuntu["Canonical Ubuntu 24.04 parent"] --> Builder["EC2 Image Builder"]
  Release --> Builder
  Builder --> CPU["Validated CPU AMI"]
  Builder --> GPU["Validated GPU AMI 0.1.11/1"]
  CPU --> CFN["CloudFormation"]
  Ubuntu --> CFN
  Release --> CFN
  CFN --> EC2["Standalone Ubuntu EC2 workstation"]
  EC2 --> DCV["Amazon DCV browser/client"]
  SSH["SSH client and private key"] --> EC2
  SSM["AWS Systems Manager"] -. "Administrative session" .-> EC2
  CFN --> Scheduler["Configured auto-stop schedule"]
  Scheduler -. "Stop at selected deadline" .-> EC2
```

The GPU AMI `ami-027c98f85b155c2ac` passed Image Builder 0.1.11/1, but its **Image Builder pipeline remains disabled and points to 0.1.10**. A successful manual image build does not imply the pipeline was upgraded. The runtime downloads separate version-pinned installer and application archives after launch.

## Trust and ownership boundaries

- GitHub stores source and tests, not passwords, private keys or proprietary NVIDIA binaries.
- The live EC2 uses `StudioWorkstationSSMRole` for SSM and exact-version RES S3 access; Image Builder has its own permissions.
- DCV authenticates the local Linux `workstation` user, **not AWS IAM**. A unique password is enrolled interactively through SSM.
- Each timed workstation has a tag-conditioned Scheduler role permitted to stop its
  instance. Ordinary expiration cannot delete the stack or terminate the instance.
- New CPU/GPU desktops expose DCV TCP 8443 and SSH TCP 22 publicly by default. `--client-cidr` restricts DCV only. SSH requires the desktop owner's public key, with password/root login disabled and TCP forwarding enabled. Setup copies `/studio/config/ssh/authorized_keys` into local `~/.ssh/authorized_keys.studio` after EFS is ready; private keys stay on the client. Existing local copies survive EFS outages and refresh during setup/reboot. Publisher retains its separate security group and SSM access.
- Manual stop and expiration keep the stack and saved EBS files. Start rearms
  a verified timer and may change the public IP. Explicit `destroy NAME`
  deletes the local resources and owned timer, with absence checks.

See [Studio CLI](studio-cli.md), [lifecycle](workstation-lifecycle-visual.md), [GPU profile](gpu-workstation-profile.md), and [index](INDEX.md).
