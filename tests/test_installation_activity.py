"""Activity is observational; it never relaxes readiness or emits raw diagnostics."""
import os
import json
import subprocess

import pytest

from runtime import cli_progress as progress, installation_activity as activity
from scripts import studio_workstation_cli as cli


@pytest.mark.parametrize("ready,helper,processes,state,result,expected", [
    (False, False, "apt\ndpkg", "running", "success", "packages res"),
    (False, False, "cc1\nmake", "running", "success", "compiler res"),
    (False, False, "nvidia-installe", "running", "success", "nvidia nvidia"),
    (False, False, "", "running", "success", "res res"),
    (True, False, "cargo\nrustc", "auto-restart", "exit-code", "efs_build efs"),
    (True, "broken", "cargo\nrustc", "auto-restart", "exit-code", "efs_build efs"),
    (True, False, "", "auto-restart", "exit-code", "efs_wait efs"),
    (True, True, "", "running", "success", "shared studio"),
    (True, True, "", "auto-restart", "exit-code", "retry studio"),
    (True, True, "", "failed", "exit-code", "failed studio"),
    (True, True, "", "exited", "success", "complete studio"),
])
def test_snapshot_uses_native_state_and_both_efs_streams(tmp_path, monkeypatch, ready, helper,
                                                       processes, state, result, expected):
    for path, present in (("root/bootstrap/semaphore/linux_vdi_config_host_ready.lock", ready),
                          ("usr/sbin/mount.efs", helper)):
        if present:
            target = tmp_path / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.touch()
            if path == "usr/sbin/mount.efs":
                target.write_text("#!/bin/sh\nexit " + ("1" if helper == "broken" else "0") + "\n")
                target.chmod(0o755)
    for stream, modified in (("stdout", 100), ("stderr", 990)):
        target = tmp_path / "var/lib/amazon/ssm/i-test/document/orchestration/cmd/awsrunShellScript/install" / stream
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("SECRET_OUTPUT\n")
        os.utime(target, (modified, modified))
    monkeypatch.setattr(activity, "Path", lambda _: tmp_path)
    monkeypatch.setattr(activity.time, "time", lambda: 1000)
    calls = []
    def output(args, **kwargs):
        calls.append(args)
        return processes if args[0] == "ps" else f"SubState={state}\nResult={result}\nNRestarts=28\n"
    monkeypatch.setattr(activity.subprocess, "check_output", output)
    snapshot = activity.snapshot()
    assert snapshot.startswith("STUDIO_ACTIVITY " + expected + " ")
    assert snapshot.endswith(" 1000 28") and "SECRET" not in snapshot
    assert calls[-1] == ("ps", "-e", "-o", "comm=")
    if expected.endswith("efs"):
        assert " efs 990 1000 " in snapshot  # Recent stderr wins over stale stdout.


def test_report_keeps_readiness_activity_transport_and_age_separate(monkeypatch, capsys):
    clock = [1050]
    monkeypatch.setattr(progress.time, "time", lambda: clock[0])
    with progress.operation("create", "test", interval=3600):
        progress.activity("SECRET_STDOUT\nSTUDIO_ACTIVITY efs_build efs 990 1000 28\n")
        progress.checkpoint("Not ready: shared software setup")
        progress.observe("ssm", {"Status": "Failed", "Comment": "Studio desktop readiness: no credentials"})
        output = capsys.readouterr().err
        assert "last=readiness-probe:Failed" in output and "readiness=Not ready: shared software setup" in output
        assert "activity=EFS helper pending; Rust compiler active" in output
        assert "log_age_at_sample=10s sampled_at=1000 sample_age=50.0s setup_retries=28" in output
        clock[0] = 1800
        progress.checkpoint("SSM unavailable")
        progress.stage("Waiting for SSM")
        output = capsys.readouterr().err
        assert "sample_age=800.0s" in output and "stalled" not in output
        for modified, sampled in ((990, 1801), (1801, 1800)):
            progress.activity(f"STUDIO_ACTIVITY efs_build efs {modified} {sampled} 28")
            progress.stage(f"Clock ordering {sampled}")
            output = capsys.readouterr().err
            assert "log_age_at_sample=unknown" in output
            assert f"sample_age={'unknown' if sampled > clock[0] else '0.0s'}" in output
        progress.activity("STUDIO_ACTIVITY SECRET studio 1 2 3\nSTUDIO_ACTIVITY shared studio 1 2 3 SECRET")
        progress.stage("Another probe")
        assert "activity=Unknown source=unavailable" in capsys.readouterr().err
    assert "SECRET" not in capsys.readouterr().err


@pytest.mark.parametrize("status,marker,expected", [
    ("Failed", "Checking shared software setup", "Not ready"),
    ("Failed", "SECRET_OUTPUT", "Failed"),
    ("Cancelled", "Checking shared software setup", "Cancelled"),
    ("TimedOut", "Checking shared software setup", "TimedOut"),
])
def test_activity_never_changes_probe_result(monkeypatch, capsys, status, marker, expected):
    monkeypatch.setattr(cli, "instance_id", lambda *args: "i-test")
    monkeypatch.setattr(cli.time, "sleep", lambda _: None)
    results = iter([{"Command": {"CommandId": "cmd-test"}}, {"Status": status,
        "StandardOutputContent": "STUDIO_ACTIVITY packages res 990 1000 0\n" + marker,
        "StandardErrorContent": "SECRET_ERROR"}])
    monkeypatch.setattr(cli, "aws", lambda *args: next(results))
    with progress.operation("create", "test", interval=3600):
        with pytest.raises(ValueError, match="GPU readiness failed"):
            cli.gpu_ready("test", "us-east-1", gpu=False)
    output = capsys.readouterr().err
    assert f"readiness={expected}:" in output and "SECRET" not in output


def test_sampler_timeout_does_not_abort_or_relax_readiness(monkeypatch):
    commands = []
    monkeypatch.setattr(cli, "instance_id", lambda *args: "i-test")
    monkeypatch.setattr(cli.time, "sleep", lambda _: None)
    def aws(*args):
        if args[2] == "send-command":
            commands.extend(json.loads(args[args.index("--parameters") + 1])["commands"])
            return {"Command": {"CommandId": "cmd-test"}}
        return {"Status": "Success"}
    monkeypatch.setattr(cli, "aws", aws)
    cli.gpu_ready("test", "us-east-1", gpu=False)
    marker = commands.index("echo 'Checking RES desktop readiness marker'") - 1
    from runtime.readiness_lifetime import checks
    assert commands[marker:] == checks(False)
    script = "timeout() { return 124; }; systemctl() { return 1; };\n" + "\n".join(commands[:marker])
    result = subprocess.run(["sh", "-c", script + "\necho readiness_checks_still_run\nexit 7"],
                            text=True, capture_output=True, timeout=2)
    assert result.returncode == 7 and result.stdout == "readiness_checks_still_run\n"
