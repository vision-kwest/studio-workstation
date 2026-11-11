from argparse import Namespace
import pytest
from runtime import publisher


def test_rejects_foreign_instance_before_writes():
    calls = []
    def aws(region, *args):
        calls.append(args)
        return {"Reservations": [{"Instances": [{"State": {"Name": "running"}, "Tags": [],
                 "IamInstanceProfile": {"Arn": "arn:aws:iam::123:instance-profile/other"}}]}]}
    with pytest.raises(ValueError, match="Not an owned"):
        publisher.command(Namespace(region="us-east-1", action="publisher-stop",
                                    instance_id="i-" + "a"*17), aws)
    assert len(calls) == 1


def test_rejects_invalid_id_without_aws():
    with pytest.raises(ValueError, match="Exact publisher"):
        publisher.command(Namespace(region="us-east-1", action="publisher-run",
                                    instance_id="bad"), lambda *a: 1)


def test_rejects_region_without_aws():
    with pytest.raises(ValueError, match="us-east-1"):
        publisher.command(Namespace(region="us-west-2", action="publisher-status",
                                    instance_id="i-" + "a"*17), lambda *a: 1)


def test_build_rejects_unpinned_recipe():
    calls = []
    def aws(region, *args):
        calls.append(args)
        return {"Reservations": [{"Instances": [{"State": {"Name": "running"},
            "Tags": [{"Key": "StudioRole", "Value": "Publisher"}],
            "IamInstanceProfile": {"Arn": "arn:aws:iam::401233741942:instance-profile/StudioEfsCiPublisher"}}]}]}
    with pytest.raises(ValueError, match="pinned commit"):
        publisher.command(Namespace(region="us-east-1", action="publisher-run",
            instance_id="i-" + "a"*17, operation="build", recipe_repo="vision-kwest/studio-openusd",
            recipe_ref="main", recipe_path="build.sh"), aws)
    assert len(calls) == 1


def test_create_bootstrap_pins_public_rez_and_iam_mount():
    captured = {}
    def aws(region, *args):
        captured["args"] = args
        return {"Instances": [{"InstanceId": "i-" + "a"*17}]}
    result = publisher.command(Namespace(region="us-east-1", action="publisher-create",
        instance_type="m7i.large", image_id="ami-test", subnet_id="subnet-test",
        security_group_id="sg-test"), aws)
    script = captured["args"][captured["args"].index("--user-data") + 1]
    assert "git checkout --detach 2fee37e32a09c337d9486e00bcc2a16c3ff4b0fb" in script
    assert "tls,iam" in script
    assert "studio-rez.git" in script
    assert result["instance_id"] == "i-" + "a"*17


def test_cli_parses_publisher_commands(monkeypatch):
    import importlib.util
    from pathlib import Path
    script = Path(__file__).resolve().parents[1] / "scripts/studio_workstation_cli.py"
    spec = importlib.util.spec_from_file_location("studio_cli_publisher_test", script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    seen = []
    monkeypatch.setattr(module, "command", lambda args: seen.append(args))
    for argv in (
        ["publisher-create", "--security-group-id", "sg-test"],
        ["publisher-status", "--instance-id", "i-" + "a"*17],
        ["publisher-start", "--instance-id", "i-" + "a"*17],
        ["publisher-stop", "--instance-id", "i-" + "a"*17],
        ["publisher-run", "--instance-id", "i-" + "a"*17, "--operation", "check"],
        ["publisher-result", "--instance-id", "i-" + "a"*17, "--command-id",
         "00000000-0000-0000-0000-000000000000"],
    ):
        module.main(argv)
        assert seen[-1].action == argv[0]
