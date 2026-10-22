# Image Builder desktop installation hang: evidence and next diagnostics

## Observed

Build instance `i-093e12de3fb559209`, SSM command `71d55e9b-cddb-4eeb-aa4c-24efb9846fbd`. Reported last visible line at 09:12:29 UTC: `invoke-rc.d: initscript gdm3, action "reload" failed` / `gdm.service is not active, cannot reload`. SSM command remained pending after hundreds of 30-second checks.

This is a **correlation**, not proof that the GDM reload is the blocking process. PR #3 already had `DEBIAN_FRONTEND=noninteractive` and `--no-install-recommends`. Its subsequent `policy-rc.d` workaround has not been validated.

## Preserve evidence before modifying installers

If the build is still alive and authorized to inspect, collect via AWS Systems Manager (no SSH):
```bash
date -Is
ps -eo pid,ppid,stat,etime,wchan:28,args --forest | grep -E 'apt|dpkg|debconf|needrestart|gdm|systemctl|invoke-rc|imagebuilder|awstoe' | head -120
sudo fuser /var/lib/dpkg/lock /var/lib/dpkg/lock-frontend || true
sudo tail -150 /var/log/apt/term.log
sudo tail -150 /var/log/dpkg.log
sudo journalctl -u gdm3 -u systemd-logind --no-pager -n 100
sudo systemctl list-jobs
sudo dpkg --audit
```
Also capture Image Builder AWSTOE logs, SSM command invocation status/output, and Image Builder workflow logs. Never paste credentials or tokens into logs.

## Interpretation rules

- If `apt-get` is still running, inspect its child process tree and wait channel; do not assume GDM is blocking.
- If `dpkg` is waiting on a service job, inspect the exact systemd job and timeout.
- If `apt-get` exited but SSM remains InProgress, investigate AWSTOE/SSM command status and agent logs.
- If process output is truncated, collect actual files rather than interpreting the last line as the final executed command.
- Do not force-kill dpkg or delete apt locks without understanding the process state.

## Safe change policy

The next image recipe must match RES's validated desktop dependency/staging choices unless a documented standalone constraint requires deviation. Do not mask GDM or suppress package services merely to hide a symptom. Explicit timeouts can bound cost but do not explain the root cause.

## Sources

- https://github.com/aws/res/blob/2026.06/source/idea/idea-bootstrap/resources/scripts/virtual-desktop-host/linux/install.sh
- https://github.com/aws/res/blob/2026.06/source/idea/idea-bootstrap/resources/scripts/dcv/linux/debian/dcv_server.sh
