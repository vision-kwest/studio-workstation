"""Progress is observable during waits and cannot change lifecycle safety."""
import contextlib
import copy
import io
from itertools import product
import json
from pathlib import Path
import re
import subprocess
import sys
import threading
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from runtime import cli_progress as progress, dcv_endpoint, publisher, pr65_one_shot_guard as guard
from scripts import studio_workstation_cli as cli
from test_issue43_cli_lifecycle import CliFixture
from workstation_fake import AWS, NOW, h, tags

ROOT = Path(__file__).resolve().parents[1]


def test_default_heartbeat_covers_three_minutes_without_aws(monkeypatch, capsys):
    clock, waits = [0.0], []
    begin, reached, stopped = threading.Event(), threading.Event(), threading.Event()

    class SimulatedWait:
        def wait(self, interval):
            assert begin.wait(2)
            if len(waits) == 18:
                reached.set()
                return stopped.wait(2)
            waits.append(interval)
            clock[0] += interval
            return False

        def set(self):
            stopped.set()

    monkeypatch.setattr(progress, "Event", SimulatedWait)
    monkeypatch.setattr(progress.time, "monotonic", lambda: clock[0])
    with progress.operation("stop", "art"):
        progress.stage("Waiting for EC2 to stop")
        progress.observe("ec2", {"StoppingInstances": [{"CurrentState": {"Name": "stopping"}}]})
        begin.set()
        assert reached.wait(2)
    output = capsys.readouterr()
    lines = [line for line in output.err.splitlines() if line.endswith("| heartbeat")]
    elapsed = [float(re.search(r"elapsed=([0-9.]+)s", line)[1]) for line in lines]
    assert waits == [10] * 18 and elapsed == list(range(10, 181, 10))
    assert all("Waiting for EC2 to stop" in line and "ec2:stopping" in line for line in lines)
    assert "ec2:stopped" not in output.err and output.out == ""
    assert "elapsed=180.0s" in output.err and "command finished" in output.err


def test_real_subprocess_progress_flushes_before_exit_with_clean_stdout():
    payload = "import json,time; time.sleep(0.35); print(json.dumps({'state': 'stopped'}))"
    source = f"""
from runtime import cli_progress as progress
from scripts import studio_workstation_cli as cli
import sys
with progress.operation('stop', 'art', interval=0.03):
    progress.stage('Waiting for EC2')
    print(cli.run([sys.executable, '-c',
          {payload!r},
          'SECRET_ARGUMENT']), end='')
"""
    with subprocess.Popen([sys.executable, "-c", source], cwd=ROOT,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) as child:
        first = child.stderr.readline()
        assert child.poll() is None and "started" in first
        stdout, stderr = child.communicate(timeout=5)
    assert child.returncode == 0 and json.loads(stdout) == {"state": "stopped"}
    assert "heartbeat" in stderr and "command finished" in stderr
    assert "SECRET_ARGUMENT" not in first + stderr


@pytest.mark.parametrize("error,event", [
    (subprocess.TimeoutExpired(["aws", "SECRET_ARGUMENT"], 30), "failed"),
    (subprocess.CalledProcessError(254, ["aws", "SECRET_ARGUMENT"], stderr="AccessDenied"), "failed"),
    (KeyboardInterrupt(), "interrupted"),
])
def test_failure_and_interrupt_propagate_and_stop_reporter(error, event, capsys):
    with pytest.raises(type(error)) as raised:
        with progress.operation("destroy", "art", interval=0.01):
            progress.stage("Deleting workstation")
            raise error
    assert raised.value is error
    output = capsys.readouterr()
    assert event in output.err and "command finished" not in output.err
    assert "SECRET_ARGUMENT" not in output.err and output.out == ""
    assert not any(t.name == "studio-progress" for t in threading.enumerate())
    progress.stage("outside operation")
    assert capsys.readouterr().err == ""


def test_actual_subprocess_failure_preserves_exit_and_command(capsys):
    command = [sys.executable, "-c", "raise SystemExit(7)", "SECRET_ARGUMENT"]
    with pytest.raises(subprocess.CalledProcessError) as raised:
        with progress.operation("run", "publish"):
            cli.run(command)
    assert raised.value.returncode == 7 and raised.value.cmd == command
    assert "SECRET_ARGUMENT" not in capsys.readouterr().err


def test_observations_include_only_allowlisted_states(capsys):
    secret = "SECRET_CREDENTIAL_OR_CLEANUP_TOKEN"
    with progress.operation("run", "publish"):
        progress.observe("ssm", {"Status": "InProgress", "StandardOutputContent": secret,
                                "StandardErrorContent": secret, "Parameters": {"commands": [secret]}})
        progress.observe("cloudformation", {"Stacks": [{"StackStatus": "CREATE_IN_PROGRESS",
                                                       "Tags": [{"Value": secret}]}]})
        progress.observe("ec2", {"Reservations": [{"Instances": [{"State": {"Name": "running"},
                                                                  "UserData": secret}]}]})
        progress.observe("ssm", {"Status": secret})
        progress.observe("scheduler", {"State": secret, "Target": {"Input": secret}})
    stderr = capsys.readouterr().err
    assert "ssm:InProgress" in stderr and "cloudformation:CREATE_IN_PROGRESS" in stderr
    assert "ec2:running" in stderr and secret not in stderr


def test_checkpoint_freshness_and_unchanged_time_are_independent(monkeypatch, capsys):
    clock = [0.0]
    monkeypatch.setattr(progress.time, "monotonic", lambda: clock[0])
    with progress.operation("start", "art", interval=3600):
        assert "last_age=unknown" in capsys.readouterr().err
        for observed, reported in ((0, 15), (20, 25)):
            clock[0] = observed
            progress.observe("ssm", {"Status": "Failed"})
            progress.checkpoint("Failed: NVIDIA T4")
            clock[0] = reported
            progress.stage(f"Readiness retry at {reported}")
            line = capsys.readouterr().err.splitlines()[-1]
            assert f"last_age={reported - observed:.1f}s" in line
            assert f"checked_age={reported - observed:.1f}s" in line
            assert f"last_check=Failed: NVIDIA T4 unchanged_for={reported:.1f}s" in line
        progress.checkpoint("Failed: Xorg")
        progress.stage("Another readiness check")
        assert "last_check=Failed: Xorg unchanged_for=0.0s checked_age=0.0s" in capsys.readouterr().err
        progress.checkpoint(None)
        progress.stage("Ready")
        assert "last_check=" not in capsys.readouterr().err


@pytest.mark.parametrize("gpu", [False, True])
@pytest.mark.parametrize("succeeds", [False, True])
def test_readiness_retry_preserves_requests_checks_and_safe_diagnostics(gpu, succeeds):
    label, records = "NVIDIA T4" if gpu else "GDM and DCV services", []
    checks = ["set -e", "test -f /root/bootstrap/semaphore/linux_vdi_config_host_ready.lock",
              "systemctl is-active gdm3 dcvserver", "dcv describe-session console"]
    if gpu:
        checks += ["nvidia-smi -L | grep -q 'Tesla T4'", "pgrep -x Xorg",
                   "DISPLAY=:0 XAUTHORITY=/run/user/119/gdm/Xauthority __GLX_VENDOR_LIBRARY_NAME=nvidia glxinfo -B | grep -q 'OpenGL renderer string: Tesla T4'"]
    failed = {"Status": "Failed", "StandardOutputContent": f"Checking {label}\nSECRET_STDOUT",
              "StandardErrorContent": "SECRET_STDERR"}
    for quiet in (False, True):
        calls, pauses, waiting, stderr = [], [], [], io.StringIO()
        replies = iter([{"Command": {"CommandId": "probe-1", "Status": "Pending"}}, failed,
                        {"Command": {"CommandId": "probe-2", "Status": "Pending"}},
                        {"Status": "Success"} if succeeds else failed])

        def execute(argv, **kwargs):
            calls.append(list(argv))
            return json.dumps(next(replies))

        def sleep(seconds):
            pauses.append(seconds)
            if seconds == 15 and not quiet:
                waiting.append(stderr.getvalue().splitlines()[-1])

        error = None
        with patch.object(cli, "instance_id", return_value="i-0123456789abcdef0"), \
             patch.object(cli.subprocess, "check_output", side_effect=execute), \
             patch.object(cli.time, "sleep", side_effect=sleep), contextlib.redirect_stderr(stderr):
            with progress.operation("start", "art", enabled=not quiet, interval=3600):
                try:
                    cli.wait_gpu_bootstrap("art", h.REGION, gpu=gpu, attempts=2, sleep=sleep)
                except RuntimeError as exc:
                    error = str(exc)
        assert pauses == [5, 15, 5]
        assert [call[2] for call in calls] == ["send-command", "get-command-invocation"] * 2
        for call in calls[::2]:
            parameters = json.loads(call[call.index("--parameters") + 1])
            assert [line for line in parameters["commands"] if not line.startswith("echo ")] == checks
            assert parameters["executionTimeout"] == ["45"]
            assert call[call.index("--timeout-seconds") + 1] == "60"
            assert call[call.index("--document-name") + 1] == "AWS-RunShellScript"
        assert bool(error) == (not succeeds)
        assert "SECRET_" not in stderr.getvalue() + (error or "")
        if error:
            assert "workstation expiry remains armed" in error and "SSM command=probe-2" in error
        if quiet:
            assert stderr.getvalue() == ""
        else:
            assert len(waiting) == 1 and "after attempt 1/2 (15s delay)" in waiting[0]
            assert f"last_check=Failed: {label}" in waiting[0]
            assert "attempt 2/2" in stderr.getvalue()
            assert ("last_check=" in stderr.getvalue().splitlines()[-1]) == (not succeeds)
        records.append((calls, pauses, error))
    assert records[0] == records[1]


@pytest.mark.parametrize("failure", [OSError("closed pipe"), ValueError("closed stream")])
def test_closed_stderr_never_interrupts_work_or_cleanup(failure):
    class ClosedStream:
        def write(self, _):
            raise failure

    completed = []
    with contextlib.redirect_stderr(ClosedStream()):
        with progress.operation("destroy", "art"):
            progress.stage("Verifying resource absence")
            completed.append("cleanup")
    assert completed == ["cleanup"]
    assert not any(t.name == "studio-progress" for t in threading.enumerate())


@pytest.mark.parametrize("argv", [["--quiet", "stop", "art"], ["stop", "art", "--quiet"],
                                 ["--no-progress", "stop", "art"], ["stop", "art", "--no-progress"]])
def test_quiet_aliases_keep_json_and_suppress_progress(argv, monkeypatch, capsys):
    monkeypatch.setattr(cli, "command", lambda args: print(json.dumps({"state": "stopped"})))
    cli.main(argv)
    output = capsys.readouterr()
    assert json.loads(output.out) == {"state": "stopped"} and output.err == ""


def test_cli_error_does_not_echo_sensitive_subprocess_arguments(monkeypatch, capsys):
    def fail(_):
        raise subprocess.CalledProcessError(254, ["aws", "--parameters", "SECRET_ARGUMENT"])
    monkeypatch.setattr(cli, "command", fail)
    with pytest.raises(SystemExit) as raised:
        cli.main(["stop", "art"])
    stderr = capsys.readouterr().err
    assert raised.value.code == 1 and "SECRET_ARGUMENT" not in stderr
    assert "254" in stderr and "failed" in stderr


@pytest.mark.parametrize("profile", ["cpu", "gpu"])
def test_progress_never_changes_lifecycle_aws_calls_or_guards(profile):
    baseline = AWS()
    selected = baseline.desktop("art", profile)
    baseline.address.update(InstanceId=selected["instance"], AssociationId="eipassoc-reviewed",
                            NetworkInterfaceId=baseline.vms[selected["instance"]]["NetworkInterfaces"][0]["NetworkInterfaceId"])
    baseline.address["Tags"] += tags({"StudioDcvInstanceId": selected["instance"]})
    baseline.vms[selected["instance"]]["PublicIpAddress"] = h.EIP_IP
    real_utc = guard._utc
    records = []
    for quiet in (False, True):
        aws, results, stderr = copy.deepcopy(baseline), [], io.StringIO()
        aws.calls.clear()

        def execute(argv, **kwargs):
            args = list(argv[1:])
            region = args.pop(args.index("--region") + 1)
            args.remove("--region")
            del args[args.index("--output"):args.index("--output") + 2]
            args.remove("--no-cli-pager")
            return json.dumps(aws(region, *args))

        with patch.object(cli.subprocess, "check_output", side_effect=execute), \
             patch.object(cli, "wait_gpu_bootstrap"), \
             patch.object(dcv_endpoint, "_probe"), \
             patch.object(cli, "verified_dcv_hostname", return_value="https://" + h.HOST + ":8443"), \
             patch.object(h, "check_gpu_tls"), \
             patch.object(guard, "_utc", side_effect=lambda value=None: real_utc(NOW if value is None else value)), \
             contextlib.redirect_stderr(stderr):
            for action in ("stop", "start", "destroy"):
                stdout = io.StringIO()
                argv = [action, "art"] + (["--quiet"] if quiet else [])
                with contextlib.redirect_stdout(stdout):
                    cli.main(argv)
                results.append(json.loads(stdout.getvalue()))
        assert bool(stderr.getvalue()) is not quiet
        assert not aws.volumes and not aws.schedules
        assert aws.address["AllocationId"] == h.EIP_ID and "InstanceId" not in aws.address
        if profile == "gpu":
            assert selected["token"] not in stderr.getvalue()
        records.append((aws.calls, results, aws.address, aws.stacks, aws.roles))
    assert records[0] == records[1]


class TestCreateProgress(CliFixture):
    def test_cpu_gpu_create_emit_one_final_json_result(self):
        requested = "Shared /studio and Rez setup requested after DCV"
        override = ["--rez-version-id", "reviewed-private-pin", "--rez-sha256", "b" * 64]
        cases = [("default", [], requested),
                 ("disabled", ["--no-studio-software"], "Shared /studio setup requested after DCV; Rez skipped"),
                 ("override", override, requested)]
        for profile, (mode, options, plan), quiet in product(("cpu", "gpu"), cases, (False, True)):
            with self.subTest(profile=profile, software=mode, quiet=quiet):
                cli.install_efs_client.reset_mock()
                stdout, stderr = io.StringIO(), io.StringIO()
                argv = ["create", "--profile", profile, "--name", f"{profile}-{mode}-{int(quiet)}",
                        "--client-cidr", "198.51.100.17/32", *options]
                if quiet:
                    argv += ["--quiet"]
                with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                    cli.main(argv)
                result = json.loads(stdout.getvalue())
                self.assertEqual((result["profile"], result["state"]), (profile, "running"))
                cli.install_efs_client.assert_called_once_with(result["instance"], h.REGION)
                self.assertNotIn(plan, stdout.getvalue())
                if quiet:
                    self.assertEqual(stderr.getvalue(), "")
                else:
                    self.assertIn(plan, stderr.getvalue())
                    self.assertIn("command finished", stderr.getvalue())
                for pin in (cli.DEFAULT_REZ_VERSION_ID, cli.DEFAULT_REZ_SHA256, override[1], override[3]):
                    self.assertNotIn(pin, stdout.getvalue() + stderr.getvalue())


def test_publisher_build_remains_submission_without_waiting(capsys):
    calls = []
    def aws(region, service, action, *args):
        calls.append((service, action))
        if action == "describe-instances":
            return {"Reservations": [{"Instances": [{"State": {"Name": "running"},
                "Tags": [{"Key": "StudioRole", "Value": "Publisher"}],
                "IamInstanceProfile": {"Arn": "arn:aws:iam::401233741942:instance-profile/" + publisher.PROFILE}}]}]}
        return {"Command": {"CommandId": "00000000-0000-0000-0000-000000000099", "Status": "Pending"}}
    with progress.operation("run", "publish"):
        result = publisher.command(SimpleNamespace(region=h.REGION, instance_id="i-" + "a" * 17,
            action="publisher-run", operation="build", recipe_repo="vision-kwest/studio-openusd",
            recipe_ref="a" * 40, recipe_path="build.sh"), aws)
    assert result["status"] == "submitted"
    assert calls == [("ec2", "describe-instances"), ("ssm", "send-command")]
    assert "build completed" not in capsys.readouterr().err.lower()
