#!/bin/bash
# Nonblocking, post-DCV setup. The bundle is an independently approved immutable release.
set -euo pipefail
exec >>/var/log/studio-software-setup.log 2>&1
for _ in $(seq 1 120); do
  test -f /root/bootstrap/semaphore/linux_vdi_config_host_ready.lock && break
  sleep 5
done
test -f /root/bootstrap/semaphore/linux_vdi_config_host_ready.lock || { echo "DCV not ready"; exit 1; }
. /etc/studio-software-release.env
dir=$(mktemp -d)
stage=""
trap 'rm -rf -- "$dir"; if test -n "$stage"; then rm -rf -- "$stage"; fi' EXIT
aws --region us-east-1 s3api get-object --bucket studio-workstation-releases-401233741942-us-east-1 --key software/rez/3.4.0/studio-rez-ubuntu2404.tar.gz --version-id "$STUDIO_REZ_VERSION_ID" "$dir/bundle.tgz" >/dev/null
printf '%s  %s\n' "$STUDIO_REZ_SHA256" "$dir/bundle.tgz" | sha256sum -c -
tar -xzf "$dir/bundle.tgz" -C "$dir"
# The Studio CLI installs the official EFS client via AWS SSM Distributor.
# The immutable Rez bundle intentionally does not contain a .deb package.
# This service retries until the independently managed helper is available.
command -v mount.efs >/dev/null || { echo "Waiting for Studio CLI EFS helper installation"; exit 1; }
mountpoint=/opt/studio/packages
fstab=/etc/fstab
entry="fs-054fd0b72429f4569:/ $mountpoint efs _netdev,nofail,x-systemd.automount,ro,tls,iam 0 0"
entries=$(awk -v p="$mountpoint" '$1 !~ /^#/ && ($2 == p || $2 == p"/") {print}' "$fstab")
if test -n "$entries"; then
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
