"""Retain real guest-probe contracts without historical CPU/GPU identities."""
import json
import subprocess
import unittest
from runtime import issue43_hostname_handoff as h
from workstation_fake import AWS

GPU = 'i-0123456789abcdef0'

class ReadinessTests(unittest.TestCase):
    def setUp(self):
        self.aws = AWS()

    def test_readiness_requires_exact_marker_and_success(self):
        h.check_gpu_tls(self.aws, GPU, sleep=lambda _: None)
        self.assertEqual(self.aws.calls[0][:2], ('ssm', 'send-command'))
        for status, output, error in [('Success', 'READY_OTHER', ValueError), ('Failed', '', RuntimeError)]:
            with self.subTest(status=status):
                def changed(service, action, args, reply):
                    if action == 'get-command-invocation':
                        reply.update(Status=status, StandardOutputContent=output)
                self.aws.after = changed
                with self.assertRaises(error):
                    h.check_gpu_tls(self.aws, GPU, sleep=lambda _: None)

    def test_invocation_eventual_consistency_retry_is_bounded(self):
        attempts = 0
        def call(region, *args):
            nonlocal attempts
            if args[:2] == ('ssm', 'get-command-invocation'):
                attempts += 1
                if attempts <= 2:
                    raise subprocess.CalledProcessError(254, args, stderr='InvocationDoesNotExist')
            return self.aws(region, *args)
        h.check_gpu_tls(call, GPU, sleep=lambda _: None)
        self.assertEqual(attempts, 3)

    def test_access_denied_is_not_retried(self):
        attempts = 0
        def call(region, *args):
            nonlocal attempts
            if args[:2] == ('ssm', 'get-command-invocation'):
                attempts += 1
                raise subprocess.CalledProcessError(254, args, stderr='AccessDenied')
            return self.aws(region, *args)
        with self.assertRaises(subprocess.CalledProcessError):
            h.check_gpu_tls(call, GPU, sleep=lambda _: None)
        self.assertEqual(attempts, 1)

    def test_tls_probe_checks_expiry_and_hostname_separately(self):
        h.check_gpu_tls(self.aws, GPU, sleep=lambda _: None)
        sent = self.aws.calls[0]
        commands = json.loads(sent[sent.index('--parameters') + 1])['commands']
        expiry = [c for c in commands if 'openssl x509' in c and '-checkend 172800' in c]
        hostname = [c for c in commands if 'openssl x509' in c and '-checkhost' in c]
        self.assertEqual(len(expiry), 1)
        self.assertEqual(len(hostname), 1)
        self.assertNotIn('-checkhost', expiry[0])
        self.assertNotIn('-checkend', hostname[0])
        self.assertIn("grep -q 'does match'", hostname[0])
