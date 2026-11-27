"""Read-only, bounded metadata for the existing SSM readiness probe."""
from pathlib import Path
import subprocess
import time


def snapshot():
    def output(*args):
        return subprocess.check_output(args, text=True, stderr=subprocess.DEVNULL, timeout=3)

    unit = dict(line.split("=", 1) for line in output(
        "systemctl", "show", "studio-software-setup.service", "-p", "SubState",
        "-p", "Result", "-p", "NRestarts").splitlines() if "=" in line)
    processes = set(output("ps", "-e", "-o", "comm=").split())  # Never inspect or export argv.
    root = Path("/")
    try:
        helper_ok = subprocess.run([str(root / "usr/sbin/mount.efs"), "--version"],
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=3).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        helper_ok = False
    activity, source = "unknown", "res"
    if not (root / "root/bootstrap/semaphore/linux_vdi_config_host_ready.lock").exists():
        activity = "res"
        if processes & {"nvidia-installe", "nvidia-installer"}:
            activity, source = "nvidia", "nvidia"
    elif not helper_ok:
        source = "efs"
        activity = "efs_build" if processes & {"cargo", "rustc"} else "efs_wait"
    else:
        source = "studio"
        activity = {"running": "shared", "auto-restart": "retry", "failed": "failed"}.get(
            unit.get("SubState"), "unknown")
        if unit.get("SubState") == "exited" and unit.get("Result") == "success":
            activity = "complete"
    if activity in {"res", "shared", "efs_wait"}:
        if processes & {"apt", "apt-get", "dpkg"}:
            activity = "packages"
        elif processes & {"cc1", "cc1plus", "gcc", "make"}:
            activity = "compiler"
    patterns = {
        "res": ["root/bootstrap/logs/*.log*", "opt/idea/app/logs/stdout.log"],
        "nvidia": ["var/log/nvidia-installer.log", "var/lib/dkms/nvidia/*/build/make.log",
                   "var/lib/dkms/nvidia/*/*/x86_64/log/make.log"],
        "efs": ["var/lib/amazon/ssm/*/document/orchestration/*/awsrunShellScript/install/stdout",
                "var/lib/amazon/ssm/*/document/orchestration/*/awsrunShellScript/install/stderr"],
        "studio": ["var/log/studio-software-setup.log"],
    }
    newest = max((int(p.stat().st_mtime) for pattern in patterns[source]
                  for p in root.glob(pattern) if p.is_file()), default=-1)
    restarts = unit.get("NRestarts", "")
    return f"STUDIO_ACTIVITY {activity} {source} {newest} {int(time.time())} {restarts if restarts.isdecimal() else '-1'}"


if __name__ == "__main__":
    try:
        print(snapshot())
    except (OSError, subprocess.SubprocessError):
        pass  # Missing activity must never change readiness or expose command output.
