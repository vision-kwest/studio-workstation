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
trap 'rm -rf "$dir"' EXIT
aws --region us-east-1 s3api get-object --bucket studio-workstation-releases-401233741942-us-east-1 --key software/rez/3.4.0/studio-rez-ubuntu2404.tar.gz --version-id "$STUDIO_REZ_VERSION_ID" "$dir/bundle.tgz" >/dev/null
printf '%s  %s\n' "$STUDIO_REZ_SHA256" "$dir/bundle.tgz" | sha256sum -c -
tar -xzf "$dir/bundle.tgz" -C "$dir"
if ! command -v mount.efs >/dev/null; then
  dpkg -i "$dir"/amazon-efs-utils_*.deb
fi
command -v mount.efs >/dev/null
mountpoint=/opt/studio/packages
entry='fs-054fd0b72429f4569:/ /opt/studio/packages efs _netdev,nofail,x-systemd.automount,ro,tls,iam 0 0'
if grep -Eq '^[^#[:space:]]+[[:space:]]+/opt/studio/packages/?[[:space:]]' /etc/fstab; then
  grep -Fxq "$entry" /etc/fstab || { echo "Conflicting EFS mount"; exit 1; }
else
  if test -e "$mountpoint"; then
    test -d "$mountpoint" && test -z "$(ls -A "$mountpoint")" || { echo "Occupied mountpoint"; exit 1; }
  fi
  mkdir -p "$mountpoint"
  printf '%s\n' "$entry" >>/etc/fstab
fi
systemctl daemon-reload
systemctl start opt-studio-packages.automount
rez=/opt/studio/rez/3.4.0
if ! test -e "$rez"; then
  (cd "$dir"; printf '%s  rez-3.4.0.tar.gz\n' '57e03cd2a9dbab818290756de0fd1205fbd898b1e02205dbe2c90a7d0d0f5159' | sha256sum -c -)
  (cd "$dir/wheelhouse"; sha256sum -c ../wheelhouse.sha256)
  tar -xzf "$dir/rez-3.4.0.tar.gz" -C "$dir"
  mkdir -p /opt/studio/rez
  PIP_NO_INDEX=1 PIP_FIND_LINKS="$dir/wheelhouse" /usr/bin/python3 -E "$dir/rez-3.4.0/install.py" "$rez"
fi
test "$(cat "$rez/bin/rez/.rez_production_install")" = 3.4.0
test -f "$dir/studio-rez/env/studio-prod.sh"
test -f "$dir/studio-rez/config/rezconfig.prod.py"
if test ! -e /opt/studio/studio-rez; then cp -a "$dir/studio-rez" /opt/studio/studio-rez; fi
cmp "$dir/studio-rez/config/rezconfig.prod.py" /opt/studio/studio-rez/config/rezconfig.prod.py
cmp "$dir/studio-rez/env/studio-prod.sh" /opt/studio/studio-rez/env/studio-prod.sh
if test -e /usr/local/bin/studio-rez-env; then
  cmp "$dir/studio-rez-env" /usr/local/bin/studio-rez-env
else
  install -m 0755 "$dir/studio-rez-env" /usr/local/bin/studio-rez-env
fi
