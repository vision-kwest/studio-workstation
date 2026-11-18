"""No AWS/network writes: current endpoint selection and HTTP/TLS negative paths."""
import contextlib
import http.client
import io
import ssl
import unittest
from unittest import mock

from runtime import dcv_endpoint as endpoint
from scripts import studio_workstation_cli as cli

IID = "i-0123456789abcdef0"
STATE = {"stack": "studio-gpu-art", "instance": IID, "state": "running",
         "public_ip": "203.0.113.12", "dcv_url": "https://203.0.113.99:8443"}


class EndpointTests(unittest.TestCase):
    def resolve(self, state=None, **kwargs):
        return endpoint.resolve(state or STATE, "us-east-1", self.aws, self.tls,
                                probe=self.probe, **kwargs)

    def setUp(self):
        self.aws, self.probe = mock.Mock(), mock.Mock()
        self.tls = mock.Mock(return_value="https://dcv.kwesidavis.com:8443")
        self.address = {"InstanceId": IID, "Tags": [{"Key": "StudioDcvInstanceId", "Value": IID}]}

    def test_every_name_including_legacy_names_can_use_its_current_ip(self):
        for name in ("studio-gpu-art", cli.DEFAULT_STACK, cli.GPU_ACCEPTANCE_STACK):
            result = self.resolve(dict(STATE, stack=name))
            self.assertEqual(result, {"url": "https://203.0.113.12:8443",
                                      "trusted": False, "reachable": True})
        self.probe.assert_called_with("203.0.113.12", False)
        self.aws.assert_not_called()
        self.tls.assert_not_called()

    def test_stopped_missing_ip_or_malformed_ip_never_reports_ready(self):
        for state in (dict(STATE, state="stopped"), dict(STATE, public_ip=None),
                      dict(STATE, public_ip="bad-address")):
            with self.assertRaises(ValueError):
                self.resolve(state)
        self.probe.assert_not_called()

    def test_nonowner_never_redirects_to_another_workstation(self):
        with self.assertRaisesRegex(ValueError, "does not belong"):
            self.resolve(hostname="dcv.kwesidavis.com")
        self.aws.assert_not_called()
        self.probe.assert_not_called()

    def test_named_owner_gets_trusted_url_only_after_ownership_tls_and_http(self):
        with mock.patch.object(endpoint.shared, "_eip", return_value=self.address):
            result = self.resolve(dict(STATE, public_ip=endpoint.shared.EIP_IP))
        self.assertTrue(result["trusted"])
        self.tls.assert_called_once_with(endpoint.shared.HOST, endpoint.shared.EIP_IP)
        self.probe.assert_called_once_with(endpoint.shared.HOST, True)

    def test_wrong_eip_owner_or_tag_fails_before_network(self):
        for address in (dict(self.address, InstanceId="i-other"),
                        dict(self.address, Tags=[])):
            with mock.patch.object(endpoint.shared, "_eip", return_value=address):
                with self.assertRaisesRegex(ValueError, "ownership mismatch"):
                    self.resolve(dict(STATE, public_ip=endpoint.shared.EIP_IP))
        self.tls.assert_not_called()
        self.probe.assert_not_called()

    def test_trusted_tls_failure_never_falls_back_to_unverified_ip(self):
        self.tls.side_effect = ValueError("certificate expired")
        with mock.patch.object(endpoint.shared, "_eip", return_value=self.address):
            with self.assertRaisesRegex(ValueError, "certificate expired"):
                self.resolve(dict(STATE, public_ip=endpoint.shared.EIP_IP))
        self.probe.assert_not_called()

    def test_unreachable_page_propagates_failure(self):
        self.probe.side_effect = ValueError("login page unreachable")
        with self.assertRaisesRegex(ValueError, "unreachable"):
            self.resolve()

    def test_connect_never_reports_missing_public_ip_as_success(self):
        with (mock.patch.object(cli, "describe", return_value=dict(STATE, public_ip=None)),
              mock.patch.object(cli, "run") as run,
              contextlib.redirect_stdout(io.StringIO()) as out,
              contextlib.redirect_stderr(io.StringIO())):
            with self.assertRaises(SystemExit) as failure:
                cli.main(["connect", STATE["stack"]])
        self.assertNotEqual(failure.exception.code, 0)
        self.assertNotIn("Reachable", out.getvalue())
        run.assert_not_called()

    def test_ssm_escape_hatch_remains_explicit_and_skips_web_probe(self):
        with (mock.patch.object(cli, "describe", return_value=STATE),
              mock.patch.object(cli, "interactive_ssm") as ssm,
              mock.patch.object(endpoint, "_probe") as probe):
            cli.main(["connect", STATE["stack"], "--ssm"])
        ssm.assert_called_once_with(IID, "us-east-1")
        probe.assert_not_called()


class HttpsProbeTests(unittest.TestCase):
    def test_ip_warning_is_allowed_but_trusted_host_uses_required_verification(self):
        with mock.patch.object(endpoint.http.client, "HTTPSConnection") as connection:
            response = connection.return_value.getresponse.return_value
            response.status = 200
            response.getheader.return_value = "text/html; charset=UTF-8"
            endpoint._probe("203.0.113.12", False)
            ctx = connection.call_args.kwargs["context"]
            self.assertEqual(ctx.verify_mode, ssl.CERT_NONE)
            self.assertFalse(ctx.check_hostname)
            connection.return_value.request.assert_called_with("GET", "/")
            endpoint._probe(endpoint.shared.HOST, True)
            ctx = connection.call_args.kwargs["context"]
            self.assertEqual(ctx.verify_mode, ssl.CERT_REQUIRED)
            self.assertTrue(ctx.check_hostname)
            self.assertEqual(connection.call_args.kwargs["timeout"], 5)
            self.assertEqual(connection.return_value.close.call_count, 2)

    def test_nonlogin_response_fails_and_closes_connection(self):
        for status, content in ((503, "text/html"), (200, "application/json"), (404, "text/html")):
            with mock.patch.object(endpoint.http.client, "HTTPSConnection") as connection:
                response = connection.return_value.getresponse.return_value
                response.status, response.getheader.return_value = status, content
                with self.assertRaisesRegex(ValueError, "HTML login page"):
                    endpoint._probe("203.0.113.12", False)
                connection.return_value.close.assert_called_once()

    def test_transport_and_http_errors_never_look_like_certificate_warnings(self):
        for error in (TimeoutError("blocked"), ssl.SSLError("handshake"),
                      http.client.RemoteDisconnected("closed")):
            with mock.patch.object(endpoint.http.client, "HTTPSConnection") as connection:
                connection.return_value.request.side_effect = error
                with self.assertRaisesRegex(ValueError, "unreachable"):
                    endpoint._probe("203.0.113.12", False)
                connection.return_value.close.assert_called_once()


if __name__ == "__main__":
    unittest.main()
