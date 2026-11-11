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
