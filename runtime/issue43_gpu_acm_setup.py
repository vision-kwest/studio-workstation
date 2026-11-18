"""Exact current-owner/destination certificate grants; reuse pinned AWCP, never log keys."""
import json
import re
import subprocess
import time

from runtime import issue43_hostname_handoff as handoff
from runtime import cli_progress as progress

BASE_ROLE = "StudioWorkstationSSMRole"
EXPORT_NAME = "studio-issue43-dcv-acm-export"
CPU_POLICY = "StudioIssue43ExactInstanceAssume"
GPU_POLICY = "StudioIssue43GpuExactInstanceAssume"  # Retired policy names, reconciled during explicit assignment.
POLICY_PREFIX = "StudioDcv-"
SOURCE_ARN = "arn:aws:iam::" + handoff.ACCOUNT + ":role/" + BASE_ROLE
SOURCE_ID = handoff.BASE_ROLE_ID
PIN = "d5f1799098b7224a8645fa1815fb6deb52fb1ef6"
AWCP_SHA256 = "1480c04bddfdea241f9b67fedd15f1006e53d33d234b5aca3f8eec6e563f420a"
ARTIFACTS = {
    "activate.py": ("https://raw.githubusercontent.com/vision-kwest/studio-workstation/"
                    + PIN + "/scripts/studio_dcv_acm.py",
                    "383042c98986917167e0359709dac6b2b2d0d770"),
    "awcp.toml": ("https://raw.githubusercontent.com/vision-kwest/studio-workstation/"
                  + PIN + "/infra/issue43-awcp.review-only.toml",
                  "f17e2249f22c0279a4aa96b2a68f1648fcea9be4"),
    "refresh.conf": ("https://raw.githubusercontent.com/vision-kwest/studio-workstation/"
                     + PIN + "/infra/issue43-awcp-dcv-refresh.conf",
                     "f0f2d0ec9dc3ecbc6aea63608a85c25835a3d62f"),
    "installer.sh": ("https://raw.githubusercontent.com/aws/aws-workload-credentials-provider/"
                     "v3.1.2/install.sh",
                     "fe77b1db02b9a61acb6027611d260b52fefd1f6d"),
}
MARKER = "STUDIO_ISSUE43_GPU_ACM_INSTALLED"


def _iam(aws, action, *args):
    return aws(handoff.REGION, "iam", action, *args)


def _doc(raw):
    return json.loads(raw) if isinstance(raw, str) else raw


def _statement(session):
    return {"Effect": "Allow", "Principal": {"AWS": SOURCE_ARN},
            "Action": "sts:AssumeRole",
            "Condition": {"StringEquals": {"aws:userid": session}}}


def _policy(session):
    return {"Version": "2012-10-17", "Statement": [{"Effect": "Allow", "Action": "sts:AssumeRole",
            "Resource": handoff.EXPORT_ROLE, "Condition": {"StringEquals": {"aws:userid": session}}}]}


def _grants(aws, repair_id=None):
    """Only exact EC2 sessions; permit a retryable partial write for the selected ID only."""
    base = _iam(aws, "get-role", "--role-name", BASE_ROLE)["Role"]
    export = _iam(aws, "get-role", "--role-name", EXPORT_NAME)["Role"]
    if base.get("RoleId") != SOURCE_ID or base.get("Arn") != SOURCE_ARN or export.get("Arn") != handoff.EXPORT_ROLE:
        raise ValueError("Certificate IAM role identity changed")
    scoped = _doc(_iam(aws, "get-role-policy", "--role-name", EXPORT_NAME,
                       "--policy-name", "StudioIssue43ExactAcMExport")["PolicyDocument"])
    if scoped != {"Version": "2012-10-17", "Statement": [{"Sid": "ExportOnlyOneDcvCertificate",
                  "Effect": "Allow", "Action": "acm:ExportCertificate", "Resource": handoff.CERT_ARN}]}:
        raise ValueError("ACM export is not restricted to the configured certificate")
    if (_iam(aws, "list-role-policies", "--role-name", EXPORT_NAME)["PolicyNames"] != ["StudioIssue43ExactAcMExport"] or
            _iam(aws, "list-attached-role-policies", "--role-name", EXPORT_NAME).get("AttachedPolicies")):
        raise ValueError("Unexpected export-role policies")
    trust = _doc(export["AssumeRolePolicyDocument"])
    statements = trust.get("Statement", [])
    denied = [{"Effect": "Deny", "Principal": {"AWS": SOURCE_ARN}, "Action": "sts:AssumeRole"}]
    sessions = set()
    for statement in ([] if statements == denied else statements):
        session = statement.get("Condition", {}).get("StringEquals", {}).get("aws:userid", "")
        if not re.fullmatch(SOURCE_ID + r":i-[0-9a-f]{8,17}", session) or statement != _statement(session):
            raise ValueError("Unexpected certificate trust; no grant may cover the entire role")
        sessions.add(session.split(":", 1)[1])
    if trust.get("Version") != "2012-10-17" or len(sessions) > 2:
        raise ValueError("Only hostname owner and pending destination may export the certificate")
    policies = {}
    for name in _iam(aws, "list-role-policies", "--role-name", BASE_ROLE)["PolicyNames"]:
        if name not in (CPU_POLICY, GPU_POLICY) and not name.startswith(POLICY_PREFIX):
            continue
        doc = _doc(_iam(aws, "get-role-policy", "--role-name", BASE_ROLE, "--policy-name", name)["PolicyDocument"])
        if len(doc.get("Statement", [])) != 1:
            raise ValueError("Unexpected hostname assume policy")
        statement = dict(doc["Statement"][0])
        sid = statement.pop("Sid", None)
        session = statement.get("Condition", {}).get("StringEquals", {}).get("aws:userid", "")
        if (sid not in (None, "AssumeOnlyIssue43ExportRoleFromExactCpuEc2Session") or
                not re.fullmatch(SOURCE_ID + r":i-[0-9a-f]{8,17}", session) or
                {**doc, "Statement": [statement]} != _policy(session)):
            raise ValueError("Unexpected managed certificate permission")
        iid = session.split(":", 1)[1]
        if iid in policies or (name.startswith(POLICY_PREFIX) and name != POLICY_PREFIX + iid):
            raise ValueError("Duplicate or mismatched certificate policy identity")
        policies[iid] = name
    if (sessions ^ set(policies)) - {repair_id}:
        raise ValueError("Unrelated partial certificate authorization; manual reconciliation required")
    return sessions, policies


def _get_role_state(aws, gpu_id):
    sessions, policies = _grants(aws)
    return gpu_id in sessions and gpu_id in policies


def _role_update(aws, instances):
    statements = [_statement(SOURCE_ID + ":" + iid) for iid in sorted(instances)]
    if not statements:
        statements = [{"Effect": "Deny", "Principal": {"AWS": SOURCE_ARN}, "Action": "sts:AssumeRole"}]
    _iam(aws, "update-assume-role-policy", "--role-name", EXPORT_NAME,
         "--policy-document", json.dumps({"Version": "2012-10-17", "Statement": statements}))


def _install_grant(aws, gpu_id):
    if not re.fullmatch(r"i-[0-9a-f]{8,17}", gpu_id or ""):
        raise ValueError("Exact workstation instance ID required")
    sessions, policies = _grants(aws, repair_id=gpu_id)
    if gpu_id in sessions and gpu_id in policies:
        return False
    if len(sessions | set(policies) | {gpu_id}) > 2:
        raise ValueError("Reconcile stale hostname grants before adding a destination")
    _iam(aws, "put-role-policy", "--role-name", BASE_ROLE,
         "--policy-name", policies.get(gpu_id, POLICY_PREFIX + gpu_id),
         "--policy-document", json.dumps(_policy(SOURCE_ID + ":" + gpu_id)))
    _role_update(aws, sessions | {gpu_id})
    if not _get_role_state(aws, gpu_id):
        raise RuntimeError("Destination certificate authorization not verified")
    return True


def _revoke_grant(aws, gpu_id):
    sessions, policies = _grants(aws, repair_id=gpu_id)
    if gpu_id not in sessions and gpu_id not in policies:
        return False
    _role_update(aws, sessions - {gpu_id})  # Remove trust before the session policy.
    if gpu_id in policies:
        _iam(aws, "delete-role-policy", "--role-name", BASE_ROLE, "--policy-name", policies[gpu_id])
    if _get_role_state(aws, gpu_id):
        raise RuntimeError("Certificate revocation readback failed")
    return True


def _guest_script():
    """Return non-secret, exact-version guest instructions; execute through SSM."""
    root = "/var/lib/studio/issue43-gpu-accept"
    commands = [
        "set -eu",
        "test -f /root/bootstrap/semaphore/linux_vdi_config_host_ready.lock",
        "systemctl is-active gdm3 dcvserver >/dev/null",
        "test ! -e /etc/dcv/dcv.pem && test ! -L /etc/dcv/dcv.pem",
        "test ! -e /etc/dcv/dcv.key && test ! -L /etc/dcv/dcv.key",
        # Refuse pre-existing mismatched files, but allow a safe retry after
        # a partial first installation interrupted by bootstrap/SSM.
        "if test -e /opt/aws/workload-credentials-provider/bin/aws-workload-credentials-provider; then test \"$(sha256sum /opt/aws/workload-credentials-provider/bin/aws-workload-credentials-provider | cut -d' ' -f1)\" = '" + AWCP_SHA256 + "'; fi",
        "if test -e /usr/local/sbin/studio-dcv-acm-activate; then test -f /usr/local/sbin/studio-dcv-acm-activate && test ! -L /usr/local/sbin/studio-dcv-acm-activate; fi",
        "install -d -m 0700 -o root -g root " + root,
    ]
    for name, (url, _) in ARTIFACTS.items():
        commands.append("curl --proto '=https' --tlsv1.2 -fsSL --retry 3 -o "
                        + root + "/" + name + " " + url)
    # Verify Git blob object hashes BEFORE running downloaded code.
    expected = {name: sha for name, (_, sha) in ARTIFACTS.items()}
    commands += [
        "python3 - <<'PY'",
        "import hashlib,pathlib,py_compile,tomllib",
        "base=pathlib.Path('" + root + "')",
        "wanted=" + repr(expected),
        "for name,sha in wanted.items():",
        " b=(base/name).read_bytes()",
        " assert hashlib.sha1(b'blob '+str(len(b)).encode()+b'\\0'+b).hexdigest()==sha",
        "py_compile.compile(str(base/'activate.py'),doraise=True)",
        "c=tomllib.loads((base/'awcp.toml').read_text())['capabilities']['acm']['certificates']",
        "assert len(c)==1",
        "assert c[0]['certificate_arn']=='" + handoff.CERT_ARN + "'",
        "assert c[0]['role_arn']=='" + handoff.EXPORT_ROLE + "'",
        "PY",
        "chmod 0600 " + root + "/*",
        "if test -e /usr/local/sbin/studio-dcv-acm-activate; then cmp -s " + root + "/activate.py /usr/local/sbin/studio-dcv-acm-activate; fi",
        "if test -e /etc/aws-workload-credentials-provider/config.toml; then cmp -s " + root + "/awcp.toml /etc/aws-workload-credentials-provider/config.toml; fi",
        "groupadd -f awscreds",
        "groupadd -f aws-wcp-token",
        "useradd -r -M -d /opt/aws/workload-credentials-provider -s /sbin/nologin -g awscreds -G aws-wcp-token aws-wcp || test \"$?\" -eq 9",
        "install -d -o aws-wcp -g awscreds -m 0700 /var/lib/studio/dcv-acm",
        "install -T -o root -g root -m 0755 " + root + "/activate.py /usr/local/sbin/studio-dcv-acm-activate",
        "AWCP_VERSION=3.1.2 bash -e " + root + "/installer.sh --config " + root + "/awcp.toml --no-start",
        "systemctl disable --now aws-workload-credentials-provider-token.service aws-workload-credentials-provider-sm.service aws-workload-credentials-provider-acm.service",
        "install -d -o root -g root -m 0755 /etc/systemd/system/aws-workload-credentials-provider-acm.service.d",
        "install -T -o root -g root -m 0644 " + root + "/refresh.conf /etc/systemd/system/aws-workload-credentials-provider-acm.service.d/90-studio-dcv-refresh.conf",
        "systemctl daemon-reload",
        "test \"$(sha256sum /opt/aws/workload-credentials-provider/bin/aws-workload-credentials-provider | cut -d' ' -f1)\" = '" + AWCP_SHA256 + "'",
        "test \"$(stat -c '%U:%G %a' /var/lib/studio/dcv-acm)\" = 'aws-wcp:awscreds 700'",
        "test \"$(stat -c '%U:%G %a' /etc/aws-workload-credentials-provider/config.toml)\" = 'root:awscreds 440'",
        "systemctl enable --now aws-workload-credentials-provider-acm.service",
        "for i in $(seq 1 18); do test -s /var/lib/studio/dcv-acm/fullchain.pem && test -s /var/lib/studio/dcv-acm/privkey.pem && break; sleep 2; done",
        "test \"$(stat -c '%U:%G %a' /var/lib/studio/dcv-acm/privkey.pem)\" = 'aws-wcp:awscreds 600'",
        "test \"$(stat -c '%U:%G %a' /var/lib/studio/dcv-acm/fullchain.pem)\" = 'aws-wcp:awscreds 600'",
        "/usr/local/sbin/studio-dcv-acm-activate --enroll-default",
        "systemctl restart aws-workload-credentials-provider-acm.service",
        "systemctl is-active dcvserver aws-workload-credentials-provider-acm.service >/dev/null",
        "echo " + MARKER,
    ]
    return commands


def _ssm(aws, gpu_id, commands, *, sleep=time.sleep):
    progress.stage("Waiting for DCV certificate enrollment")
    r = aws(handoff.REGION, "ssm", "send-command", "--instance-ids", gpu_id,
            "--document-name", "AWS-RunShellScript",
            "--parameters", json.dumps({"commands": commands, "executionTimeout": ["240"]}),
            "--timeout-seconds", "270",
            "--comment", "Studio pinned GPU ACM enrollment (no secrets)")
    cid = r["Command"]["CommandId"]
    for _ in range(60):
        sleep(4)
        try:
            v = aws(handoff.REGION, "ssm", "get-command-invocation",
                    "--command-id", cid, "--instance-id", gpu_id)
        except subprocess.CalledProcessError as e:
            if "InvocationDoesNotExist" not in (e.stderr or ""):
                raise
            continue
        if v.get("Status") == "Success":
            if v.get("ResponseCode") != 0 or MARKER not in v.get("StandardOutputContent", "").splitlines():
                raise RuntimeError("GPU ACM enrollment missing success marker")
            return
        if v.get("Status") in ("Failed", "Cancelled", "TimedOut", "Cancelling"):
            raise RuntimeError("GPU ACM enrollment failed; inspect private guest logs (never export the PEM)")
    raise RuntimeError("GPU ACM enrollment timed out")



def _refresh_script():
    """Restart the already-pinned provider after renewal access is restored; no reinstall."""
    expected = {"/usr/local/sbin/studio-dcv-acm-activate": ARTIFACTS["activate.py"][1],
                "/etc/aws-workload-credentials-provider/config.toml": ARTIFACTS["awcp.toml"][1]}
    return ["set -eu", "python3 - <<'PY'", "import hashlib,pathlib",
            "for name,sha in " + repr(expected) + ".items():",
            " b=pathlib.Path(name).read_bytes()",
            " assert hashlib.sha1(b'blob '+str(len(b)).encode()+b'\\0'+b).hexdigest()==sha", "PY",
            "test \"$(sha256sum /opt/aws/workload-credentials-provider/bin/aws-workload-credentials-provider | cut -d' ' -f1)\" = '" + AWCP_SHA256 + "'",
            "systemctl restart aws-workload-credentials-provider-acm.service",
            "for i in $(seq 1 18); do openssl x509 -in /var/lib/studio/dcv-acm/fullchain.pem -noout -checkend 172800 >/dev/null 2>&1 && break; sleep 2; done",
            "/usr/local/sbin/studio-dcv-acm-activate", "echo " + MARKER]

def prepare_gpu(aws, gpu_id, *, sleep=time.sleep):
    """Explicit hostname destination only; reuse the pinned AWCP/activation installer."""
    progress.stage("Preparing DCV hostname certificate")
    from runtime.workstation_inventory import context
    vm = handoff._items(aws, gpu_id)[gpu_id]
    selected = context(aws, handoff.REGION, handoff._tags(vm)["aws:cloudformation:stack-id"])
    if selected["instance"] != gpu_id or selected["profile"] not in ("cpu", "gpu") or selected["state"] != "running":
        raise ValueError("Certificate destination is not an owned running desktop")
    owner = handoff._eip(aws).get("InstanceId")
    sessions, policies = _grants(aws, repair_id=gpu_id)
    for stale in (sessions | set(policies)) - {owner, gpu_id}:
        try:
            previous = handoff._items(aws, stale, allow_missing=True).get(stale)
            if previous is not None:
                context(aws, handoff.REGION, handoff._tags(previous)["aws:cloudformation:stack-id"])
        except subprocess.CalledProcessError as exc:
            if "InvalidInstanceID.NotFound" not in (exc.stderr or ""):
                raise
        _revoke_grant(aws, stale)
    existing = gpu_id in sessions and gpu_id in policies
    try:
        _install_grant(aws, gpu_id)
        try:
            handoff.check_gpu_tls(aws, gpu_id, sleep=sleep)
        except RuntimeError:
            try:
                _ssm(aws, gpu_id, _refresh_script(), sleep=sleep)
            except RuntimeError:
                _ssm(aws, gpu_id, _guest_script(), sleep=sleep)
        progress.stage("Verifying DCV hostname certificate")
        handoff.check_gpu_tls(aws, gpu_id, sleep=sleep)
        if handoff._eip(aws).get("InstanceId") != owner:
            raise RuntimeError("Hostname owner changed during certificate preparation")
        return {"instance_id": gpu_id, "acm": "verified", "hostname": handoff.HOST}
    except BaseException:
        if not existing and handoff._eip(aws).get("InstanceId") != gpu_id:
            _revoke_grant(aws, gpu_id)
        raise


def revoke_gpu(aws, gpu_id):
    """A non-owner may keep its valid certificate, but cannot renew/export it."""
    progress.stage("Revoking former hostname certificate access")
    if handoff._eip(aws).get("InstanceId") == gpu_id:
        raise ValueError("Cannot revoke certificate renewal from the current hostname owner")
    return {"instance_id": gpu_id, "revoked": _revoke_grant(aws, gpu_id)}
