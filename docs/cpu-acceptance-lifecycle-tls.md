# Standalone CPU workstation: end-to-end acceptance and lifecycle

## Verified baseline (2026-10-25)

On EC2 `i-09122a298e3878a57` (stack `studio-workstation-cpu-v011`),
Ubuntu 24.04 / m7i.large, RES 2026.06 shell stages completed. The
published v0.1.2 `standalone_bootstrap.py` was manually hotfixed from the
SHA-256-verified nested `idea-virtual-desktop-lib.tar.gz` archive.
The application subsequently reported `DCV session placement complete with
state: READY`; `dcv describe-session console --json` reported
`status: running`, owner `workstation`, 2560x1600. A browser DCV
login and interactive GNOME terminal were demonstrated.

**Important distinction:** the live instance received a one-file hotfix.
The repository contains that fix in
`runtime/overrides/ideavirtualdesktop/app/standalone_bootstrap.py`,
and immutable release v0.1.2 contains it. The currently deployed
CloudFormation user-data still points at v0.1.1. A new stack must use
`releases/v0.1.2/res-2026.06` or newer. Do not treat the live
hotfix as proof that a fresh deployment has passed.

## Reproducible fresh-instance validation

From an administrator environment (not inside SSM):

1. Use a non-root IAM administrator with required deployment permissions.
   Confirm `infra/workstation-release-read-policy.json` is applied to
   `StudioWorkstationSSMRole`, along with `AmazonSSMManagedInstanceCore`.
   Verify both release archives can be read **using that role**.
2. Render with
   `python3 runtime/render_workstation.py --bucket studio-workstation-releases-401233741942-us-east-1 --prefix releases/v0.1.2/res-2026.06 --user workstation --output /tmp/studio-workstation-v012.json`.
   Validate with `aws cloudformation validate-template --template-body file:///tmp/studio-workstation-v012.json --region us-east-1`.
3. Deploy a **new, separately named** test stack only after reviewing the
   resulting billable EC2 and EBS resources. Choose a public subnet with
   internet route. `ClientCidr` defaults to `0.0.0.0/0` for access from any IPv4 address; override it with a trusted client `/32` when restriction is desired.
   This is not an automated deployment command.
4. From CloudShell run
   `python3 scripts/check_workstation_aws.py --stack STACK_NAME`.
   Once SSM is Online, connect to the reported instance ID via
   `aws ssm start-session --target INSTANCE_ID --region us-east-1`.
5. From the EC2 SSM session run
   `sudo bash scripts/check_workstation_guest.sh INSTANCE_ID` **after
   copying that script onto the guest**, or execute the equivalent
   read-only checks. A Git checkout in CloudShell is not automatically
   present on the guest. Wait for RES automatic reboot stages to complete.
6. Validate DCV login from the authorized client IP using the local
   `workstation` Linux account. A fresh provisioned account is
   password-locked by design; set a strong local password through a
   privileged SSM session (`sudo passwd workstation`) or adopt an
   approved secret-delivery/login process. **This is the remaining manual
   step; unattended interactive login is not yet implemented.**
7. Confirm a GNOME terminal can run `whoami` and that it returns
   `workstation`. Record SSM, DCV status, console ownership, login and
   desktop results. CloudFormation CREATE_COMPLETE and install locks
   alone do not establish readiness.

The source template now refuses to write `pre_install_finished.lock`
if archive download fails or the expected installer is missing.
This guards against the v0.1.1 false-success semaphore regression.
Test this behavior on a fresh stack; it does not retroactively update
existing instance user-data.

## Stop/start lifecycle test (explicit approval and charges)

Use `python3 scripts/workstation_lifecycle.py status --stack STACK_NAME`
from CloudShell to identify the actual instance. Save its instance ID.
Close interactive DCV work, then:

```bash
python3 scripts/workstation_lifecycle.py stop --stack STACK_NAME --confirm-instance INSTANCE_ID
python3 scripts/workstation_lifecycle.py start --stack STACK_NAME --confirm-instance INSTANCE_ID
python3 scripts/check_workstation_aws.py --stack STACK_NAME
```

The stop/start operations change EC2 state and require approval. The
tool waits for EC2 status checks but **not** DCV readiness. Reconnect
through SSM and rerun guest DCV checks, then test browser login and
interactive GNOME again. Expect the auto-assigned public IPv4 to change
on stop/start; update the browser URL and any DNS record accordingly.
Stopping EC2 generally stops compute billing, but EBS persists and
continues to incur storage charges. Do not delete the CloudFormation
stack to test ordinary stop/start.

## Trusted TLS: choose a connection model

The screenshot confirmed an HTTPS DCV endpoint using an untrusted
certificate. A working DCV session is **not** evidence of a trusted TLS
identity.

**Recommended for direct browser access:** use a domain/subdomain under
your control, point its DNS A record at a **stable Elastic IP** associated
with the workstation, and install a publicly trusted server certificate
and matching private key for that hostname using the supported Amazon DCV
server TLS configuration. Restrict key-file access, reload/restart DCV
in a controlled window, and verify certificate chain and hostname from
the client. Certificate issuance/renewal requires domain control and
automation; AWS ACM public certificates generally cannot be exported
for direct installation on EC2 unless using an exportable-certificate
offering. An alternative is an HTTPS reverse proxy/load balancer with
ACM and compatible DCV WebSocket handling, but that adds infrastructure
and requires separate validation.

Do **not** request a public certificate for a changing EC2 public IP
and do not disable browser certificate validation. Self-signed TLS
encrypts traffic but does not provide publicly trusted server identity.
For private-only access, a managed tunnel/VPN with trusted internal
certificate issuance may be appropriate, but the browser must still
trust the certificate and hostname.

**Not configured yet:** domain ownership, DNS, certificate issuance,
key management, stable IP, and renewal method require a deliberate
choice and authorization. No certificate, Elastic IP, or DNS change is
performed by these scripts.

## Public DCV access (current choice)

The user selected direct public DCV access for now. The CloudFormation
`ClientCidr` parameter defaults to `0.0.0.0/0`, allowing inbound
TCP 8443 from any IPv4 address. No inbound SSH is added. To restrict
access later, pass `ClientCidr=YOUR_PUBLIC_IP/32` when deploying.

**Security:** DCV remains password-protected, but its login endpoint is
publicly reachable. Use a strong unique Linux password. The existing
untrusted HTTPS certificate is unchanged; TLS trust remains deferred.
This does not open other ports or guarantee availability on IPv6.

**Existing stack:** Changing the repository template does not update
`studio-workstation-cpu-v011` automatically. Its live security group
was modified separately. A future CloudFormation update must use a
freshly rendered template with the updated parameter definition and
explicitly preserve `ClientCidr=0.0.0.0/0`; review the change set before
applying it. Do not redeploy the existing workstation merely to change
this parameter without first reviewing whether EC2 replacement is
proposed.


## Validated fresh CPU deployment — v0.1.3 (2026-10-28)

**Result: PASS — fresh provisioning and interactive desktop.** This
acceptance result is based on the deployment and guest logs supplied
during the validation session, plus a successful browser login reported
by the operator. It does **not** imply that the fresh instance has
passed stop/start lifecycle testing.

| Item | Observed result |
| --- | --- |
| Release | `v0.1.3` / RES `2026.06` |
| CloudFormation stack | `studio-workstation-cpu-v013`, deployment succeeded |
| EC2 instance | `i-0bbfbdc878d3a2320`, `m7i.large`, `us-east-1` |
| Base image | `ami-0d6dd9e8265d48906` (Ubuntu 24.04) |
| Network | Public subnet; `ClientCidr=0.0.0.0/0` for TCP 8443 |
| Instance role | `StudioWorkstationSSMRole` |
| Root volume | 100 GiB gp3, encrypted |
| SSM | Online, interactive session established |
| Cloud-init | `status: done`, `errors: []` |
| Installer | All stage markers present: `pre_install_finished.lock`, `install_finished.lock`, `install_post_reboot_finished.lock`, `app_installed.lock` |
| Linux configuration | `linux_vdi_config.lock` and `linux_vdi_config_host_ready.lock` present |
| DCV service | `active` |
| DCV session | `console`, owner `workstation` |
| RES readiness log | `2026-10-28 08:04:17 UTC: DCV session placement complete with state: READY` |
| Browser | Local `workstation` login and interactive DCV shared desktop confirmed by operator |
| Manual repairs | None to Python packages, bootstrap files, IAM or RES configuration during fresh provisioning |

**Package hashes** (verified during publication):
- `installation-scripts.tar.gz`: `813259748335482b1877192a61a008b32e613038ee831305d9e4767c51c23ab7`
- `virtual-desktop.tar.gz`: `4d9b89d149d2d984193e346ef0c48294a7a976dd32208a6e06ac365d4581c4a3`
- `release-manifest.json`: `13bbecd2409215754b825c79cf3fc563fc969e654a205ea32b554a23630ee917`

The immutable release is stored at
`s3://studio-workstation-releases-401233741942-us-east-1/releases/v0.1.3/res-2026.06/`.
The archive hashes match v0.1.2; changes to the CloudFormation
template, bootstrap guard and tests are delivered from the merged
repository and rendered template, not necessarily the two application
archives.

**Manual account enrollment remains required:** the local Linux
`workstation` password was set through SSM using `passwd` before
browser login. This is not yet unattended credential provisioning.

**Remaining acceptance gates:** stop/start lifecycle of this v0.1.3
instance, public-IP change/reconnection after start, and any required
repeatability checks. TLS trust is deferred by decision; DCV is
currently publicly reachable on port 8443 with an untrusted
certificate. Do not include secrets or password values in acceptance
artifacts.
