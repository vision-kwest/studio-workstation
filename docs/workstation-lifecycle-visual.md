# Workstation lifecycle — visual guide

Image creation and day-to-day instance lifecycle are distinct operations.

```mermaid
flowchart TD
  AMI["Validated CPU or GPU AMI"] --> Create["CloudFormation create"]
  Create --> Running["EC2 running"]
  Running --> Ready["SSM online + RES/DCV console ready"]
  Ready --> Connect["Connect via DCV using local workstation login"]
  Connect --> Stop["Save work; stop EC2"]
  Stop --> Start["Start same EC2 instance"]
  Start --> IP["Refresh public IP and verify DCV readiness"]
  IP --> Connect
  Ready --> Destroy["CloudFormation destroy (explicit approval)"]
  Stop --> Destroy
  Destroy --> EBS["Audit retained EBS volumes"]
```

## Operational rules

- **Stop** preserves the EBS-backed workstation and avoids running-instance compute charges; EBS charges remain.
- **Start** may assign a new public IPv4 address. Verify SSM and DCV rather than assuming EC2 running means the desktop is ready.
- **Destroy** removes stack-managed resources. With `DeleteOnTermination=false`, a root EBS volume can remain and continue to incur charges; inspect before deletion.
- The validated CPU v0.1.3 workstation successfully survived stop/start and DCV reconnection. GPU lifecycle acceptance remains pending.
- No inbound SSH is required for daily DCV use; SSM is the administrative path.

See [CLI usage](studio-cli.md) and [architecture](visual-architecture.md).
