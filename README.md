# Studio Workstation — Foundation v0.1

A lightweight, modular single-EC2 workstation foundation. No RES, control host, OpenTofu, SSH, Active Directory, NAT gateway, or always-on launcher.

## Included
- CloudFormation single-instance infrastructure with encrypted EBS, restricted DCV port 8443 and SSM role.
- Optional Python EC2 lifecycle CLI: status, start, stop, connect, check.
- Design-only profiles: developer, creator, editor.
- Unit tests for instance discovery.

## Not implemented
**Do not deploy expecting a working DCV desktop.** Golden Ubuntu 24.04 AMIs, NVIDIA/Xorg/DCV configuration, desktop login, automated console session, end-to-end tests, profile installers, persistent independent data volume, idle stop, and cost guardrails are not yet built.

## Security
Use only a validated AMI; CPU and NVIDIA builds must be separate. SSM requires outbound connectivity. No inbound SSH is permitted. Restrict TCP/UDP 8443 to your public IP /32. Never store passwords in CloudFormation or user data. Root EBS DeleteOnTermination=false can incur ongoing charges after stack deletion. Check AMI root device mapping before deployment.

## CLI (optional)
```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
aws sso login --profile YOUR_SSO_PROFILE
python -m workstation.cli check --instance-id i-EXAMPLE --profile YOUR_SSO_PROFILE
python -m workstation.cli start --instance-id i-EXAMPLE --profile YOUR_SSO_PROFILE --wait
python -m workstation.cli connect --instance-id i-EXAMPLE --profile YOUR_SSO_PROFILE
python -m workstation.cli stop --instance-id i-EXAMPLE --profile YOUR_SSO_PROFILE --wait
```

## Milestones
1. CPU-only Ubuntu 24.04 DCV golden AMI and graphical login validation.
2. NVIDIA g4dn golden AMI; verify nvidia-smi, Xorg, DCV and graphics.
3. Idempotent modular application installers with license/version/disk validation.
4. Optional local launcher, persistent data volume, idle-stop and cost guardrails.

## RES 2026.06 reference
- https://github.com/aws/res/blob/2026.06/source/idea/idea-bootstrap/resources/scripts/dcv/linux/gpu_drivers.sh
- https://github.com/aws/res/blob/2026.06/source/idea/idea-bootstrap/resources/scripts/dcv/linux/debian/dcv_server.sh
- https://github.com/aws/res/blob/2026.06/source/idea/idea-bootstrap/resources/scripts/virtual-desktop-host/linux/install.sh

Do not run RES-dependent scripts unchanged.
