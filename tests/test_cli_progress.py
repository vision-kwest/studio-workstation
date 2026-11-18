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

from runtime import cli_progress as progress, publisher, pr65_one_shot_guard as guard
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
    real_status = guard.status
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
             patch.object(cli, "wait_dcv", return_value={"url": "https://203.0.113.200:8443"}), \
             patch.object(guard, "status", side_effect=lambda *a, **kw: real_status(*a, now=NOW, **kw)), \
             contextlib.redirect_stderr(stderr):
            for action in ("stop", "start", "destroy"):
                stdout = io.StringIO()
                argv = [action, "art"] + (["--quiet"] if quiet else [])
                if action == "destroy":
                    argv += ["--delete-stack"]
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
