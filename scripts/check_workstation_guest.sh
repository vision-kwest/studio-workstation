#!/usr/bin/env bash
# Read-only guest acceptance: run on the workstation over SSM, not CloudShell.
set -euo pipefail
EXPECTED_INSTANCE="${1:-}"
if [[ -z "$EXPECTED_INSTANCE" ]]; then
  echo "Usage: sudo bash check_workstation_guest.sh i-INSTANCE-ID" >&2
  exit 2
fi
TOKEN=$(curl -fsS --max-time 3 -X PUT -H 'X-aws-ec2-metadata-token-ttl-seconds: 60' http://169.254.169.254/latest/api/token)
INSTANCE=$(curl -fsS --max-time 3 -H "X-aws-ec2-metadata-token: $TOKEN" http://169.254.169.254/latest/meta-data/instance-id)
[[ "$INSTANCE" == "$EXPECTED_INSTANCE" ]] || { echo "WRONG INSTANCE: $INSTANCE" >&2; exit 1; }
echo "INSTANCE: $INSTANCE"
cloud-init status --long
[[ "$(cloud-init status)" == "status: done" ]] || { echo "cloud-init not complete" >&2; exit 1; }
for lock in pre_install_finished.lock install_finished.lock install_post_reboot_finished.lock app_installed.lock linux_vdi_config.lock linux_vdi_config_host_ready.lock; do
  test -f "/root/bootstrap/semaphore/$lock" || { echo "MISSING LOCK: $lock" >&2; exit 1; }
done
systemctl is-active --quiet dcvserver || { echo "DCV SERVICE NOT ACTIVE" >&2; exit 1; }
ss -ltn | grep -Eq '(^|[[:space:]])[^[:space:]]*:8443[[:space:]]' || { echo "DCV PORT 8443 NOT LISTENING" >&2; exit 1; }
dcv describe-session console --json | python3 -c 'import json,sys; d=json.load(sys.stdin); assert d["status"]=="running" and d["owner"]=="workstation",d; print("DCV CONSOLE READY:",d["id"])'
echo "PASS: guest-side readiness; browser login and interactive desktop must be verified separately"
