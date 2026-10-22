# CPU-only Ubuntu 24.04 DCV golden AMI — experimental v0.2

**Status:** implementation candidate; not yet deployed or tested on EC2. Do not label this AMI production-ready until graphical login and reboot tests pass.

## Goal
Create a CPU-only Ubuntu 24.04 x86_64 desktop AMI with GNOME/Xorg, DCV web viewer, system authentication, automatic console session and SSM. No SSH or RES dependency.

## Build with AWS console + Systems Manager (no control instance)
1. In **us-east-1**, launch a disposable Ubuntu 24.04 amd64 Canonical EC2 instance (suggest `m7i.xlarge` for the first test; confirm SSM support and regional availability). Use a 60–100 GiB root disk, an instance profile with `AmazonSSMManagedInstanceCore`, and a security group without inbound SSH. Use a public subnet with public IP + internet egress, or private subnet with working SSM endpoints and access to DCV downloads. This is a temporary paid build instance.
2. Confirm **Systems Manager → Fleet Manager / Managed nodes** reports the instance Online.
3. Review `install.sh` and the official DCV download. Obtain the archive's SHA-256 from AWS's published checksum and provide it to the script via `DCV_SHA256`. Do **not** bypass checksum verification. AWS's release URL is pinned to 2026.0 build 24952; update deliberately as versions change.
4. Copy this repository's `image/cpu/install.sh` onto the instance using SSM Run Command (e.g. a version-pinned S3 artifact with a narrowly scoped read role), then run `sudo DCV_SHA256=<verified-64-hex> bash install.sh`. Do not paste passwords into Run Command. Capture output from `/var/log/studio-workstation-image.log`.
5. Reboot through EC2 Console. Execute `validate.sh` using SSM. Check `dcv list-sessions` reports `console` and Xorg is healthy. If not, diagnose logs (`journalctl -u gdm3 -u dcvserver`) before continuing.
6. **Test direct login on the temporary instance before making the AMI:** set a strong, unique password for the `workstation` Linux account using an approved interactive SSM mechanism; avoid passing it as a Run Command parameter or logging it. Restrict TCP/UDP 8443 to your public IP /32. Visit `https://<public-ip>:8443/`, review the certificate trust warning, authenticate, and confirm the actual GNOME desktop renders. The initial account is deliberately locked, so login will not work until a password is set.
7. **Before capturing an AMI**, lock the workstation password again, remove personal files and temporary secrets, clear build logs if appropriate, and review machine-specific state and SSM registration. Do not bake usable credentials into the AMI. Stop and create an AMI from the validated instance, then launch a **new** test instance from that AMI. Set a new password on the new instance using a secure interactive mechanism. Repeat all tests, including stop/start.
8. Only after a clean clone passes should the AMI ID be used with `infra/workstation.yaml`. Terminate the temporary build/test instances and inspect remaining EBS volumes and snapshots to avoid charges.

## Known limitations
- No automation yet for safe first-boot credential provisioning or cleanup of machine identity; the image must not be distributed with usable passwords.
- Xorg dummy driver/monitor modeline and GDM startup are hypotheses requiring EC2 validation. `dcv list-sessions` and `pgrep Xorg` may expose incompatibilities.
- DCV's built-in self-signed certificate is suitable for a disposable test, not a polished production security posture. Use a trusted certificate/hostname for long-term use.
- The current installer deliberately fails closed if no independently verified DCV SHA-256 is supplied.
- `workstation` currently belongs to the sudo group; revisit least privilege for routine users.
- This is CPU-only; GPU installation will be a separate AMI recipe.

## References
- https://docs.aws.amazon.com/dcv/latest/adminguide/setting-up-installing-linux-x11-installation.html
- https://docs.aws.amazon.com/dcv/latest/adminguide/managing-sessions-start.html
- https://github.com/aws/res/tree/2026.06/source/idea/idea-bootstrap/resources/scripts/dcv/linux
