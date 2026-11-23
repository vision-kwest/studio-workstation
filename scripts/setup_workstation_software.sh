#!/bin/bash
# Nonblocking SSH, shared storage and application menus, followed by optional Rez.
set -euo pipefail
exec >>/var/log/studio-software-setup.log 2>&1
for _ in $(seq 1 120); do
  test -f /root/bootstrap/semaphore/linux_vdi_config_host_ready.lock && break
  sleep 5
done
test -f /root/bootstrap/semaphore/linux_vdi_config_host_ready.lock || { echo "DCV not ready"; exit 1; }
# RES attempts to disable SSH; restore the approved native service after readiness.
desktop_user=$(getent passwd 2000 | cut -d: -f1)
desktop_home=$(getent passwd 2000 | cut -d: -f6)
cat >/etc/ssh/sshd_config.d/00-studio.conf <<STUDIO_SSH
PubkeyAuthentication yes
PasswordAuthentication no
KbdInteractiveAuthentication no
PermitRootLogin no
AllowTcpForwarding yes
GatewayPorts no
AllowUsers $desktop_user
AuthorizedKeysFile .ssh/authorized_keys .ssh/authorized_keys2 .ssh/authorized_keys.studio
STUDIO_SSH
# Native ssh.service creates /run/sshd before its own ExecStartPre validation.
systemctl enable --now ssh.service
/usr/sbin/sshd -t
systemctl reload ssh.service
# The Studio CLI installs the official EFS client via AWS SSM Distributor.
# The immutable Rez bundle intentionally does not contain a .deb package.
# This service retries until the independently managed helper is available.
command -v mount.efs >/dev/null || { echo "Waiting for Studio CLI EFS helper installation"; exit 1; }
mountpoint=/opt/studio/packages
fstab=/etc/fstab
entry="fs-054fd0b72429f4569:/ $mountpoint efs _netdev,nofail,x-systemd.automount,rw,tls,iam 0 0"
old_entry="${entry/,rw,/,ro,}"
entries=$(awk -v p="$mountpoint" '$1 !~ /^#/ && ($2 == p || $2 == p"/") {print}' "$fstab")
if test "$entries" = "$old_entry"; then
  sed -i "\|^$old_entry\$|s/,ro,/,rw,/" "$fstab"
elif test -n "$entries"; then
  test "$entries" = "$entry" || { echo "Conflicting or duplicate EFS mount"; exit 1; }
else
  if test -e "$mountpoint"; then
    test -d "$mountpoint" && test -z "$(ls -A "$mountpoint")" || { echo "Occupied mountpoint"; exit 1; }
  fi
  mkdir -p "$mountpoint"
  printf '%s\n' "$entry" >>"$fstab"
fi
systemctl daemon-reload
systemctl start opt-studio-packages.automount
systemctl start opt-studio-packages.mount
findmnt -rn -t nfs4 --mountpoint "$mountpoint" >/dev/null
if findmnt -rn -O ro --mountpoint "$mountpoint" >/dev/null; then
  mount -o remount,rw "$mountpoint"
fi
shared=$mountpoint/shared
test ! -L "$shared" || { echo "Shared directory must not be a symlink"; exit 1; }
if ! test -e "$shared"; then
  install -d -m 0755 -o 2000 -g 2000 "$shared"
fi
test -d "$shared" && test "$(stat -c '%u:%g' "$shared")" = 2000:2000 || { echo "Shared directory requires UID/GID 2000"; exit 1; }
if test -e /studio || test -L /studio; then
  test "$(readlink /studio)" = "$shared" || { echo "Conflicting /studio path"; exit 1; }
else
  ln -s "$shared" /studio
fi
# Keep native StrictModes and local access even when EFS is unavailable later.
if test -f /studio/config/ssh/authorized_keys; then
  install -d -m 0700 -o 2000 -g 2000 "$desktop_home/.ssh"
  install -m 0600 -o 2000 -g 2000 /studio/config/ssh/authorized_keys "$desktop_home/.ssh/authorized_keys.studio"
fi
# MenuLibre and GNOME use this native user directory; only launchers are shared.
runuser -u "$desktop_user" -- bash -se <<'STUDIO_APPLICATIONS'
mkdir -p /studio/bin /studio/projects /studio/share/applications /studio/share/icons "$HOME/.local/share"
applications="$HOME/.local/share/applications"
if test -L "$applications"; then
  test "$(readlink "$applications")" = /studio/share/applications || { echo "Conflicting applications link"; exit 1; }
else
  if test -d "$applications"; then
    backup="$applications.before-studio"
    test ! -e "$backup" && test ! -L "$backup" || { echo "Applications backup already exists"; exit 1; }
    cp -an "$applications/." /studio/share/applications/
    mv -T "$applications" "$backup"
  fi
  ln -s /studio/share/applications "$applications"
fi
STUDIO_APPLICATIONS
# The shell and app wrappers are installed once in shared /studio/bin.
if test -e /usr/local/bin/studio || test -L /usr/local/bin/studio; then
  test "$(readlink /usr/local/bin/studio)" = /studio/bin/studio || { echo 'Conflicting studio command'; exit 1; }
else
  ln -s /studio/bin/studio /usr/local/bin/studio
fi
if ! command -v menulibre >/dev/null || ! command -v desktop-file-validate >/dev/null || ! command -v rg >/dev/null; then
  apt-get -o DPkg::Lock::Timeout=300 update
  DEBIAN_FRONTEND=noninteractive apt-get -o DPkg::Lock::Timeout=300 install -y --no-install-recommends menulibre desktop-file-utils ripgrep
fi
# Permit VS Code's own sandbox at the resolved EFS path, including future versions.
cat >/etc/apparmor.d/studio-vscode <<'STUDIO_VSCODE_APPARMOR'
abi <abi/4.0>,
include <tunables/global>

profile studio-vscode /opt/studio/packages/shared/apps/vscode/**/code flags=(unconfined) {
  userns,
}
STUDIO_VSCODE_APPARMOR
chown root:root /etc/apparmor.d/studio-vscode
chmod 0644 /etc/apparmor.d/studio-vscode
apparmor_parser -r /etc/apparmor.d/studio-vscode
# Prepare shared Resolve on NVIDIA desktops after DCV/EFS; retry failures after Rez.
resolve_status=0
if command -v nvidia-smi >/dev/null && test -d /studio/apps/davinci-resolve; then
  if ! /studio/apps/davinci-resolve/setup-local.sh --user "$desktop_user"; then
    echo "Resolve local setup failed; software setup will retry after optional Rez" >&2
    resolve_status=1
  fi
fi
test -f /etc/studio-software-release.env || exit "$resolve_status"
. /etc/studio-software-release.env
dir=$(mktemp -d)
stage=""
trap 'rm -rf -- "$dir"; if test -n "$stage"; then rm -rf -- "$stage"; fi' EXIT
aws --region us-east-1 s3api get-object --bucket studio-workstation-releases-401233741942-us-east-1 --key software/rez/3.4.0/studio-rez-ubuntu2404.tar.gz --version-id "$STUDIO_REZ_VERSION_ID" "$dir/bundle.tgz" >/dev/null
printf '%s  %s\n' "$STUDIO_REZ_SHA256" "$dir/bundle.tgz" | sha256sum -c -
tar -xzf "$dir/bundle.tgz" -C "$dir"
rez=/opt/studio/rez/3.4.0
installing=/opt/studio/rez/.studio-rez-3.4.0-installing
test ! -L "$rez" && test ! -L "$installing" || { echo "Unsafe Rez install path"; exit 1; }
if test -e "$rez" && { ! test -x "$rez/bin/rez/rez-env" || test "$(cat "$rez/bin/rez/.rez_production_install" 2>/dev/null)" != 3.4.0; }; then
  test -f "$installing" || { echo "Unmanaged Rez install; refusing cleanup"; exit 1; }
  rm -rf -- "$rez"
fi
if ! test -e "$rez"; then
  (cd "$dir"; printf '%s  rez-3.4.0.tar.gz\n' '57e03cd2a9dbab818290756de0fd1205fbd898b1e02205dbe2c90a7d0d0f5159' | sha256sum -c -)
  (cd "$dir/wheelhouse"; sha256sum -c ../wheelhouse.sha256)
  tar -xzf "$dir/rez-3.4.0.tar.gz" -C "$dir"
  mkdir -p /opt/studio/rez
  : > "$installing"
  PIP_NO_INDEX=1 PIP_FIND_LINKS="$dir/wheelhouse" /usr/bin/python3 -E "$dir/rez-3.4.0/install.py" "$rez"
fi
test -x "$rez/bin/rez/rez-env" && test "$(cat "$rez/bin/rez/.rez_production_install")" = 3.4.0
rm -f -- "$installing"
test -f "$dir/studio-rez/env/studio-prod.sh"
test -f "$dir/studio-rez/config/rezconfig.prod.py"
if test ! -e /opt/studio/studio-rez; then
  stage=$(mktemp -d /opt/studio/.studio-rez.XXXXXXXX)
  cp -a "$dir/studio-rez/." "$stage/"
  mv -Tn -- "$stage" /opt/studio/studio-rez
fi
cmp "$dir/studio-rez/config/rezconfig.prod.py" /opt/studio/studio-rez/config/rezconfig.prod.py
cmp "$dir/studio-rez/env/studio-prod.sh" /opt/studio/studio-rez/env/studio-prod.sh
if test -e /usr/local/bin/studio-rez-env; then
  cmp "$dir/studio-rez-env" /usr/local/bin/studio-rez-env
else
  install -m 0755 "$dir/studio-rez-env" /usr/local/bin/studio-rez-env
fi
exit "$resolve_status"
