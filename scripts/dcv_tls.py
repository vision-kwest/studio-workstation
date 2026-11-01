#!/usr/bin/env python3
"""Optional Let's Encrypt DNS-01 renewal + Amazon DCV TLS installation.

Run as root only on an authorized standalone workstation. This does not change
RES bootstrap, DCV sessions, system authentication, Image Builder or AWS IAM.
"""
import argparse
import hashlib
import os
from pathlib import Path
import pwd
import re
import subprocess
import tempfile

HOST = "dcv.kwesidavis.com"
CERT_DIR = Path("/etc/letsencrypt/live") / HOST
DCV_DIR = Path("/etc/dcv")


def execute(argv):
    return subprocess.run(argv, check=True, capture_output=True, text=True).stdout.strip()


def verify_pair(cert, key):
    """Reject bad hostname, near-expiry and mismatched keys *before* modifying DCV."""
    execute(["openssl", "x509", "-in", str(cert), "-noout", "-checkend", "172800"])
    output = execute(["openssl", "x509", "-in", str(cert), "-noout", "-checkhost", HOST])
    if "does match" not in output:
        raise ValueError("Certificate does not match required DCV hostname")
    pub_cert = execute(["openssl", "x509", "-in", str(cert), "-pubkey", "-noout"])
    pub_key = execute(["openssl", "pkey", "-in", str(key), "-pubout"])
    if hashlib.sha256(pub_cert.encode()).digest() != hashlib.sha256(pub_key.encode()).digest():
        raise ValueError("Certificate and private key do not match")


def install(cert_dir=CERT_DIR, dcv_dir=DCV_DIR):
    if os.geteuid() != 0:
        raise PermissionError("Run as root")
    certificate, private_key = cert_dir / "fullchain.pem", cert_dir / "privkey.pem"
    verify_pair(certificate, private_key)
    if dcv_dir.is_symlink() or not dcv_dir.is_dir():
        raise ValueError("DCV configuration directory is missing or a symlink")
    dcv_user = pwd.getpwnam("dcv")
    # Stage in the same filesystem and atomically replace each target. Old files
    # remain in place if verification fails. Existing RES dcv.conf is untouched.
    pending = []
    try:
        for src, target in [(certificate, "dcv.pem"), (private_key, "dcv.key")]:
            with tempfile.NamedTemporaryFile(dir=dcv_dir, prefix=".studio-tls-",
                                             delete=False) as tmp:
                pending.append(Path(tmp.name))
                os.fchmod(tmp.fileno(), 0o600)
                os.fchown(tmp.fileno(), dcv_user.pw_uid, dcv_user.pw_gid)
                with src.open("rb") as stream:
                    while data := stream.read(65536):
                        tmp.write(data)
                tmp.flush()
                os.fsync(tmp.fileno())
        # Key first; avoid exposing newly issued certificate with the old key.
        os.replace(pending[1], dcv_dir / "dcv.key")
        os.replace(pending[0], dcv_dir / "dcv.pem")
    finally:
        for path in pending:
            if path.exists():
                path.unlink()
    return "DCV TLS material installed; validate with a trusted remote client"


def ensure(email, cert_dir=CERT_DIR, dcv_dir=DCV_DIR):
    """Run at boot and daily; certonly retains unexpired cert and renews when due."""
    if os.geteuid() != 0:
        raise PermissionError("Run as root")
    if not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", email):
        raise ValueError("Valid ACME contact email required")
    execute(["certbot", "certonly", "--dns-route53", "--non-interactive",
             "--agree-tos", "--keep-until-expiring", "--cert-name", HOST,
             "-d", HOST, "--email", email])
    return install(cert_dir, dcv_dir)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["install", "ensure"])
    parser.add_argument("--email", help="Required for ensure; never put AWS keys here")
    args = parser.parse_args(argv)
    if args.action == "ensure":
        if not args.email:
            parser.error("--email required for ensure")
        print(ensure(args.email))
    else:
        print(install())


if __name__ == "__main__":
    main()
