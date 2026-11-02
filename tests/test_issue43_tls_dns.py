"""Issue 43 offline contracts: zero AWS, DNS, Certbot or EC2 activity."""
import contextlib
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from types import SimpleNamespace
from unittest import mock

from runtime import dcv_dynamic_dns as dns
from scripts import dcv_tls as tls
from scripts import studio_workstation_cli as cli

IID = "i-0123456789abcdef0"
IP = "44.202.195.142"
STATE = {"InstanceId": IID, "State": {"Name": "running"},
         "PublicIpAddress": IP, "Tags": [
             {"Key": "StudioDcvDnsHost", "Value": "dcv.kwesidavis.com"},
             {"Key": "StudioDcvDnsManaged", "Value": "true"}]}
EVENT = {"source": "aws.ec2", "detail-type": "EC2 Instance State-change Notification",
         "region": "us-east-1", "detail": {"instance-id": IID, "state": "running"}}


class DnsTests(unittest.TestCase):
    def setUp(self):
        self.ec2, self.r53 = mock.Mock(), mock.Mock()
        self.ec2.describe_instances.return_value = {"Reservations": [{"Instances": [STATE]}]}
        self.r53.list_resource_record_sets.return_value = {"ResourceRecordSets": []}

    def reconcile(self, event=EVENT):
        return dns.reconcile(event, self.ec2, self.r53, IID)

    def test_running_creates_exact_a_record(self):
        result = self.reconcile()
        self.assertEqual(result["action"], "UPSERT")
        args = self.r53.change_resource_record_sets.call_args.kwargs
        self.assertEqual(args["HostedZoneId"], dns.ZONE)
        record = args["ChangeBatch"]["Changes"][0]["ResourceRecordSet"]
        self.assertEqual((record["Name"], record["Type"], record["TTL"]), (dns.HOST, "A", 60))
        self.assertEqual(record["ResourceRecords"], [{"Value": IP}])

    def test_same_ip_idempotent(self):
        self.r53.list_resource_record_sets.return_value = {"ResourceRecordSets": [
            {"Name": dns.HOST, "Type": "A", "TTL": 60, "ResourceRecords": [{"Value": IP}]}]}
        self.assertEqual(self.reconcile()["status"], "unchanged")
        self.r53.change_resource_record_sets.assert_not_called()

    def test_new_running_ip_replaces_old_one(self):
        self.r53.list_resource_record_sets.return_value = {"ResourceRecordSets": [
            {"Name": dns.HOST, "Type": "A", "TTL": 60, "ResourceRecords": [{"Value": "44.202.195.11"}]}]}
        self.assertEqual(self.reconcile()["action"], "UPSERT")

    def test_stopped_removes_record(self):
        self.ec2.describe_instances.return_value = {"Reservations": [{"Instances": [
            dict(STATE, State={"Name": "stopped"}, PublicIpAddress=None)]}]}
        old = {"Name": dns.HOST, "Type": "A", "TTL": 300,
               "ResourceRecords": [{"Value": "44.202.195.11"}]}
        self.r53.list_resource_record_sets.return_value = {"ResourceRecordSets": [old]}
        self.assertEqual(self.reconcile()["action"], "DELETE")
        change = self.r53.change_resource_record_sets.call_args.kwargs["ChangeBatch"]["Changes"][0]
        self.assertEqual(change["ResourceRecordSet"], old)

    def test_stale_stopping_event_does_not_delete_running_record(self):
        event = dict(EVENT, detail={"instance-id": IID, "state": "stopping"})
        self.assertEqual(self.reconcile(event)["action"], "UPSERT")

    def test_terminal_no_record_idempotent(self):
        self.ec2.describe_instances.return_value = {"Reservations": [{"Instances": [
            dict(STATE, State={"Name": "terminated"}, PublicIpAddress=None)]}]}
        self.assertEqual(self.reconcile()["status"], "unchanged")
        self.r53.change_resource_record_sets.assert_not_called()

    def test_running_without_ip_defers_and_never_removes(self):
        self.ec2.describe_instances.return_value = {"Reservations": [{"Instances": [
            dict(STATE, PublicIpAddress=None)]}]}
        self.assertEqual(self.reconcile()["status"], "deferred")
        self.r53.change_resource_record_sets.assert_not_called()

    def test_pending_never_changes(self):
        self.ec2.describe_instances.return_value = {"Reservations": [{"Instances": [
            dict(STATE, State={"Name": "pending"})]}]}
        self.assertEqual(self.reconcile()["status"], "deferred")
        self.r53.change_resource_record_sets.assert_not_called()

    def test_wrong_instance_event_never_reads_ec2(self):
        self.assertEqual(self.reconcile(dict(EVENT, detail={"instance-id": "i-evil"}))["status"], "ignored")
        self.ec2.describe_instances.assert_not_called()

    def test_unknown_event_ignored(self):
        self.assertEqual(self.reconcile({"source": "untrusted"})["status"], "ignored")
        self.ec2.describe_instances.assert_not_called()

    def test_scheduled_reconcile(self):
        self.assertEqual(self.reconcile({"source": "studio.dcv.reconcile",
                                          "instance-id": IID})["action"], "UPSERT")

    def test_scheduled_wrong_instance_ignored(self):
        self.assertEqual(self.reconcile({"source": "studio.dcv.reconcile",
                                         "instance-id": "i-wrong"})["status"], "ignored")

    def test_wrong_owner_fails_closed(self):
        self.ec2.describe_instances.return_value = {"Reservations": [{"Instances": [
            dict(STATE, Tags=[])]}]}
        with self.assertRaisesRegex(RuntimeError, "ownership"):
            self.reconcile()
        self.r53.change_resource_record_sets.assert_not_called()

    def test_wrong_region_ignored(self):
        self.assertEqual(self.reconcile(dict(EVENT, region="us-west-2"))["status"], "ignored")

    def test_complex_existing_record_not_overwritten(self):
        self.r53.list_resource_record_sets.return_value = {"ResourceRecordSets": [
            {"Name": dns.HOST, "Type": "A", "AliasTarget": {"DNSName": "other"}}]}
        with self.assertRaises(ValueError):
            self.reconcile()
        self.r53.change_resource_record_sets.assert_not_called()

    def test_missing_instance_fails_closed(self):
        self.ec2.describe_instances.return_value = {"Reservations": []}
        with self.assertRaisesRegex(RuntimeError, "not found"):
            self.reconcile()

    def test_private_ip_refused(self):
        self.ec2.describe_instances.return_value = {"Reservations": [{"Instances": [
            dict(STATE, PublicIpAddress="10.0.0.4")]}]}
        with self.assertRaisesRegex(RuntimeError, "eligible"):
            self.reconcile()
        self.r53.change_resource_record_sets.assert_not_called()


class TlsTests(unittest.TestCase):
    @mock.patch.object(tls, "execute")
    def test_matching_cert_pair(self, execute):
        execute.side_effect = ["", "Hostname dcv.kwesidavis.com does match certificate",
                               "PUBKEY", "PUBKEY"]
        tls.verify_pair(Path("/fake/cert"), Path("/fake/key"))
        self.assertEqual(execute.call_count, 4)

    @mock.patch.object(tls, "execute")
    def test_hostname_mismatch_rejected(self, execute):
        execute.side_effect = ["", "Hostname dcv.kwesidavis.com does NOT match certificate"]
        with self.assertRaisesRegex(ValueError, "does not match"):
            tls.verify_pair(Path("/fake/cert"), Path("/fake/key"))

    @mock.patch.object(tls, "execute")
    def test_key_mismatch_rejected(self, execute):
        execute.side_effect = ["", "Hostname dcv.kwesidavis.com does match certificate",
                               "PUBKEY-A", "PUBKEY-B"]
        with self.assertRaisesRegex(ValueError, "private key"):
            tls.verify_pair(Path("/fake/cert"), Path("/fake/key"))

    @mock.patch.object(tls, "execute", side_effect=__import__("subprocess").CalledProcessError(1, "openssl"))
    def test_expired_certificate_never_installed(self, execute):
        with tempfile.TemporaryDirectory() as tmp:
            dcv = Path(tmp) / "dcv"
            dcv.mkdir()
            with mock.patch.object(tls.os, "geteuid", return_value=0):
                with self.assertRaises(__import__("subprocess").CalledProcessError):
                    tls.install(Path(tmp), dcv)
            self.assertEqual(list(dcv.iterdir()), [])

    @mock.patch.object(tls, "execute")
    @mock.patch.object(tls, "install", return_value="ok")
    @mock.patch.object(tls.os, "geteuid", return_value=0)
    def test_boot_or_daily_ensure_issues_or_renews(self, uid, install, execute):
        self.assertEqual(tls.ensure("admin@example.org"), "ok")
        argv = execute.call_args.args[0]
        self.assertEqual(argv[:3], ["certbot", "certonly", "--dns-route53"])
        self.assertIn("--keep-until-expiring", argv)
        self.assertIn(tls.HOST, argv)
        install.assert_called_once()

    @mock.patch.object(tls, "execute")
    @mock.patch.object(tls.os, "geteuid", return_value=0)
    def test_invalid_email_prevents_any_certbot_call(self, uid, execute):
        with self.assertRaises(ValueError):
            tls.ensure("bad email")
        execute.assert_not_called()

    @mock.patch.object(tls.os, "geteuid", return_value=1000)
    def test_not_root_rejected(self, uid):
        with self.assertRaises(PermissionError):
            tls.install()

    @mock.patch.object(tls, "verify_pair")
    @mock.patch.object(tls.os, "geteuid", return_value=0)
    def test_secure_install_without_res_restart(self, uid, verify):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            letsencrypt = directory / "live"
            letsencrypt.mkdir()
            (letsencrypt / "fullchain.pem").write_text("CERT")
            (letsencrypt / "privkey.pem").write_text("KEY")
            dcv = directory / "dcv"
            dcv.mkdir()
            with mock.patch.object(tls.pwd, "getpwnam", return_value=SimpleNamespace(
                    pw_uid=os.getuid(), pw_gid=os.getgid())):
                with mock.patch.object(tls.os, "fchown"):
                    tls.install(letsencrypt, dcv)
            self.assertEqual((dcv / "dcv.pem").read_text(), "CERT")
            self.assertEqual((dcv / "dcv.key").read_text(), "KEY")
            self.assertEqual((dcv / "dcv.key").stat().st_mode & 0o777, 0o600)
            self.assertEqual((dcv / "dcv.pem").stat().st_mode & 0o777, 0o600)
            self.assertEqual(sorted(p.name for p in dcv.iterdir()), ["dcv.key", "dcv.pem"])


class CliHostnameTests(unittest.TestCase):
    @mock.patch.object(cli.socket, "getaddrinfo")
    def test_dns_mismatch_fails_before_tls(self, resolve):
        resolve.return_value = [(2, 1, 6, "", ("44.202.195.11", 8443))]
        with mock.patch.object(cli.ssl, "create_default_context") as tls_context:
            with self.assertRaisesRegex(ValueError, "DNS A record"):
                cli.verified_dcv_hostname("dcv.kwesidavis.com", IP)
            tls_context.assert_not_called()

    def test_wrong_hostname_refused(self):
        with self.assertRaises(ValueError):
            cli.verified_dcv_hostname("attacker.example", IP)

    @mock.patch.object(cli.socket, "getaddrinfo")
    @mock.patch.object(cli.socket, "create_connection")
    @mock.patch.object(cli.ssl, "create_default_context")
    def test_trusted_url_only_after_valid_tls(self, context, connection, resolve):
        resolve.return_value = [(2, 1, 6, "", (IP, 8443))]
        url = cli.verified_dcv_hostname("dcv.kwesidavis.com", IP)
        self.assertEqual(url, "https://dcv.kwesidavis.com:8443")
        context.return_value.wrap_socket.assert_called_once()
        self.assertEqual(context.return_value.wrap_socket.call_args.kwargs["server_hostname"],
                         "dcv.kwesidavis.com")

    @mock.patch.object(cli.socket, "getaddrinfo")
    @mock.patch.object(cli.socket, "create_connection")
    @mock.patch.object(cli.ssl, "create_default_context")
    def test_bad_certificate_never_claimed_trusted(self, context, connection, resolve):
        import ssl
        resolve.return_value = [(2, 1, 6, "", (IP, 8443))]
        context.return_value.wrap_socket.side_effect = ssl.SSLCertVerificationError("untrusted")
        with self.assertRaisesRegex(ValueError, "TLS verification failed"):
            cli.verified_dcv_hostname("dcv.kwesidavis.com", IP)

    @mock.patch.object(cli, "describe", return_value={"state": "running", "instance": IID,
                      "public_ip": IP, "dcv_url": "https://" + IP + ":8443"})
    @mock.patch.object(cli, "verified_dcv_hostname", return_value="https://dcv.kwesidavis.com:8443")
    def test_connect_opt_in(self, verified, describe):
        with contextlib.redirect_stdout(io.StringIO()) as stream:
            cli.main(["connect", "--hostname", "dcv.kwesidavis.com"])
        self.assertIn("https://dcv.kwesidavis.com:8443", stream.getvalue())
        verified.assert_called_once()

    @mock.patch.object(cli, "describe", return_value={"state": "running", "instance": IID,
                      "public_ip": IP, "dcv_url": "https://" + IP + ":8443"})
    @mock.patch.object(cli, "verified_dcv_hostname")
    def test_old_ip_connect_unchanged(self, verified, describe):
        with contextlib.redirect_stdout(io.StringIO()) as stream:
            cli.main(["connect"])
        self.assertIn("https://" + IP + ":8443", stream.getvalue())
        verified.assert_not_called()


class PolicyTests(unittest.TestCase):
    def test_policies_immutable_zone_and_record_type(self):
        policy = json.loads((Path(__file__).resolve().parents[1] /
                             "infra/issue43-iam.review-only.json").read_text())
        self.assertEqual(policy["HostedZoneId"], dns.ZONE)
        for name, typ in (("LambdaDnsPolicy", "A"), ("WorkstationCertbotPolicy", "TXT")):
            statements = policy[name]["Statement"]
            mutations = [s for s in statements if "route53:ChangeResourceRecordSets" in s["Action"]]
            self.assertEqual(len(mutations), 1)
            self.assertIn(dns.ZONE, mutations[0]["Resource"])
            self.assertEqual(mutations[0]["Condition"]["ForAllValues:StringEquals"]
                             ["route53:ChangeResourceRecordSetsRecordTypes"], [typ])
            self.assertEqual(set(mutations[0]["Condition"]["Null"].values()), {"false"})
            self.assertEqual(set(mutations[0]["Condition"]["Null"]), {
                "route53:ChangeResourceRecordSetsNormalizedRecordNames",
                "route53:ChangeResourceRecordSetsRecordTypes",
                "route53:ChangeResourceRecordSetsActions"})
        self.assertEqual(policy["WorkstationCertbotPolicy"]["Statement"][-1]
                         ["Condition"]["ForAllValues:StringEquals"]
                         ["route53:ChangeResourceRecordSetsNormalizedRecordNames"],
                         ["_acme-challenge.dcv.kwesidavis.com"])


class DeploymentTemplateTests(unittest.TestCase):
    def test_optional_stack_never_launches_workstation(self):
        root = Path(__file__).resolve().parents[1]
        t = json.loads((root / "infra/issue43-dns-reconciler.review-only.json").read_text())
        resources = t["Resources"]
        self.assertEqual(t["Parameters"]["InstanceId"]["AllowedPattern"],
                         "i-[0-9a-f]{8,17}")
        self.assertEqual(resources["DnsFunction"]["Properties"]
                         ["Environment"]["Variables"]["STUDIO_DNS_INSTANCE_ID"],
                         {"Ref": "InstanceId"})
        self.assertIn("DnsErrorsAlarm", resources)
        self.assertIn("rate(5 minutes)",
                      resources["ReconcileRule"]["Properties"]["ScheduleExpression"])
        self.assertFalse(any(r["Type"] in ("AWS::EC2::Instance", "AWS::EC2::SecurityGroup",
                                           "AWS::Route53::HostedZone", "AWS::Route53::RecordSet")
                             for r in resources.values()))
        self.assertEqual(resources["DnsFunction"]["Properties"]["Code"]
                         ["S3ObjectVersion"], {"Ref": "CodeVersion"})
        self.assertEqual(resources["DnsFunction"]["Properties"]["Handler"],
                         "dcv_dynamic_dns.lambda_handler")
        role = resources["DnsRole"]["Properties"]["Policies"][0]["PolicyDocument"]
        changer = [s for s in role["Statement"] if
                   "route53:ChangeResourceRecordSets" in s["Action"]][0]
        self.assertEqual(changer["Condition"]["ForAllValues:StringEquals"]
                         ["route53:ChangeResourceRecordSetsRecordTypes"], ["A"])
        self.assertEqual(set(changer["Condition"]["Null"].values()), {"false"})
        self.assertNotIn("DnsFunction", json.dumps(resources["DnsRole"]))
        self.assertIn("AWS::StackName", json.dumps(resources["DnsRole"]))

    def test_lambda_package_determinism_and_handler(self):
        import zipfile
        from scripts import package_issue43_dns as pack
        with tempfile.TemporaryDirectory() as root:
            one, two = Path(root) / "one.zip", Path(root) / "two.zip"
            a, b = pack.package(one), pack.package(two)
            self.assertEqual(a["sha256"], b["sha256"])
            self.assertEqual(one.read_bytes(), two.read_bytes())
            with zipfile.ZipFile(one) as archive:
                self.assertEqual(archive.namelist(), ["dcv_dynamic_dns.py"])
                self.assertIn(b"def lambda_handler(", archive.read("dcv_dynamic_dns.py"))


if __name__ == "__main__":
    unittest.main()
