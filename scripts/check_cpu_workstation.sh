#!/usr/bin/env bash
# Read-only, on-instance CPU RES 2026.06 acceptance checks.
set -uo pipefail
failed=0
check() {
  local name="$1"; shift
  if "$@"; then printf 'PASS %s\n' "$name"; else printf 'FAIL %s\n' "$name"; failed=1; fi
}
echo "Studio Workstation CPU acceptance — run on EC2 as root or via sudo"
echo "Instance identity: $(aws sts get-caller-identity --query Arn --output text 2>/dev/null || echo unavailable)"
check "cloud-init complete" bash -c 'cloud-init status --long | grep -qx "status: done"'
check "RES application installed" test -f /root/bootstrap/semaphore/app_installed.lock
check "standalone module present" test -f /opt/idea/python/latest/lib/python3.12/site-packages/ideavirtualdesktop/app/standalone_bootstrap.py
check "upstream default logging fix" grep -Fq 'logging_utils.res_logging()' /opt/idea/python/latest/lib/python3.12/site-packages/ideavirtualdesktop/app/standalone_bootstrap.py
check "SSM agent running" systemctl is-active --quiet snap.amazon-ssm-agent.amazon-ssm-agent.service
check "DCV daemon running" systemctl is-active --quiet dcvserver
check "DCV port listening" bash -c 'ss -ltn | grep -Eq "(:8443)[[:space:]]"'
check "DCV console running" bash -c 'dcv describe-session console --json | python3 -c '\''import json,sys; s=json.load(sys.stdin); sys.exit(0 if s.get("status")=="running" and s.get("owner")=="workstation" else 1)'\'''
echo "Supervisor state (one-shot bootstrap may be EXITED after success):"
/opt/idea/python/latest/bin/supervisorctl -c /etc/supervisord.conf status virtual-desktop-app 2>&1 || true
echo "Interactive DCV login, persistence after stop/start, and certificate trust require separate client-side verification."
exit "$failed"
