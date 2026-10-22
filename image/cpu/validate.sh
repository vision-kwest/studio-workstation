#!/usr/bin/env bash
# Run via SSM after reboot. Reports system readiness; does not prove remote login.
set -Eeuo pipefail
. /etc/os-release
[[ "$ID" == ubuntu && "$VERSION_ID" == 24.04 ]]
command -v dcv
dpkg-query -W nice-dcv-server nice-dcv-web-viewer
systemctl is-enabled gdm3 dcvserver
systemctl is-active gdm3 dcvserver
pgrep -a Xorg
dcv list-sessions
ss -lnt | grep -E '[:.]8443[[:space:]]'
getent passwd workstation
passwd -S workstation
echo 'Preflight checks completed. A manual browser login and actual console desktop check are still required.'
