"""Issue #43 GPU ACM enrollment: exact-scoped IAM + pinned AWCP, no secrets in SSM.

Intentionally a separate explicit CLI operation until Task 3 wires lifecycle.
No EC2 launch, EIP/DNS mutation, or changes to RES bootstrap.
"""
import json
import re
import subprocess
import time

from runtime import issue43_hostname_handoff as handoff

BASE_ROLE = "StudioWorkstationSSMRole"
EXPORT_NAME = "studio-issue43-dcv-acm-export"
CPU_POLICY = "StudioIssue43ExactInstanceAssume"
GPU_POLICY = "StudioIssue43GpuExactInstanceAssume"
SOURCE_ARN = "arn:aws:iam::" + handoff.ACCOUNT + ":role/" + BASE_ROLE
SOURCE_ID = handoff.BASE_ROLE_ID
CPU_SESSION = SOURCE_ID + ":" + handoff.CPU_ID
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
    statement = {"Effect": "Allow", "Action": "sts:AssumeRole",
                 "Resource": handoff.EXPORT_ROLE,
                 "Condition": {"StringEquals": {"aws:userid": session}}}
    if session == CPU_SESSION:
        # Preserve the already-live CPU policy verbatim, including its Sid.
        statement["Sid"] = "AssumeOnlyIssue43ExportRoleFromExactCpuEc2Session"
    return {"Version": "2012-10-17", "Statement": [statement]}


def _get_role_state(aws, gpu_id):
    if not re.fullmatch(r"i-[0-9a-f]{8,17}", gpu_id or ""):
        raise ValueError("Expected exact GPU instance ID")
    gpu_session = SOURCE_ID + ":" + gpu_id
    base = _iam(aws, "get-role", "--role-name", BASE_ROLE)["Role"]
    export = _iam(aws, "get-role", "--role-name", EXPORT_NAME)["Role"]
    if base.get("RoleId") != SOURCE_ID or base.get("Arn") != SOURCE_ARN:
        raise ValueError("Base IAM role identity changed")
    if export.get("Arn") != handoff.EXPORT_ROLE:
        raise ValueError("ACM export IAM role changed")
    cpu = _doc(_iam(aws, "get-role-policy", "--role-name", BASE_ROLE,
                    "--policy-name", CPU_POLICY)["PolicyDocument"])
    if cpu != _policy(CPU_SESSION):
        raise ValueError("Existing CPU certificate authorization differs")
    scoped = _doc(_iam(aws, "get-role-policy", "--role-name", EXPORT_NAME,
                       "--policy-name", "StudioIssue43ExactAcMExport")["PolicyDocument"])
    if scoped != {"Version": "2012-10-17", "Statement": [{
            "Sid": "ExportOnlyOneDcvCertificate", "Effect": "Allow",
            "Action": "acm:ExportCertificate", "Resource": handoff.CERT_ARN}]}:
        raise ValueError("ACM export role no longer restricted to one certificate")
    trust = _doc(export["AssumeRolePolicyDocument"])
    if trust.get("Version") != "2012-10-17":
        raise ValueError("Unexpected export trust document version")
    statements = trust.get("Statement", [])
    if statements not in ([_statement(CPU_SESSION)],
                          [_statement(CPU_SESSION), _statement(gpu_session)]):
        raise ValueError("Unexpected ACM export trust; refuses to overwrite other grants")
    names = _iam(aws, "list-role-policies", "--role-name", BASE_ROLE)["PolicyNames"]
    present = GPU_POLICY in names
    if present:
        existing = _doc(_iam(aws, "get-role-policy", "--role-name", BASE_ROLE,
                            "--policy-name", GPU_POLICY)["PolicyDocument"])
        if existing != _policy(gpu_session):
            raise ValueError("GPU policy belongs to a different session")
    if present != (len(statements) == 2):
        raise ValueError("GPU IAM partial state; manual reconciliation required")
    return present


def _role_update(aws, statements):
    _iam(aws, "update-assume-role-policy", "--role-name", EXPORT_NAME,
         "--policy-document", json.dumps({"Version": "2012-10-17",
                                           "Statement": statements}))


def _install_grant(aws, gpu_id):
    """Add exactly this EC2 session, keeping the existing CPU session."""
    if _get_role_state(aws, gpu_id):
        return False  # Already authorized, safe retry
    gpu_session = SOURCE_ID + ":" + gpu_id
    _iam(aws, "put-role-policy", "--role-name", BASE_ROLE,
         "--policy-name", GPU_POLICY,
         "--policy-document", json.dumps(_policy(gpu_session)))
    try:
        _role_update(aws, [_statement(CPU_SESSION), _statement(gpu_session)])
    except BaseException:
        # A write may have succeeded before the API error: fail closed if
        # rollback is not verified. No EIP is transferred by this function.
        try:
            _role_update(aws, [_statement(CPU_SESSION)])
            _iam(aws, "delete-role-policy", "--role-name", BASE_ROLE,
                 "--policy-name", GPU_POLICY)
        except BaseException as exc:
            raise RuntimeError("GPU IAM setup failed; rollback unverified") from exc
        raise
    if not _get_role_state(aws, gpu_id):
        raise RuntimeError("New GPU authorization was not verified")
    return True


def _revoke_grant(aws, gpu_id):
    if not _get_role_state(aws, gpu_id):
        return False
    # Remove the trust BEFORE dropping the inline session policy.
    _role_update(aws, [_statement(CPU_SESSION)])
    _iam(aws, "delete-role-policy", "--role-name", BASE_ROLE,
         "--policy-name", GPU_POLICY)
    if _get_role_state(aws, gpu_id):
        raise RuntimeError("GPU IAM revocation readback failed")
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


def prepare_gpu(aws, *, sleep=time.sleep):
    """Exact guarded instance; install narrowly-scoped export permission and ACM guest agent."""
    preview = handoff.gpu_preview(aws)
    gpu_id = preview["to"]
    created = _install_grant(aws, gpu_id)
    try:
        # A retry after successful preparation needs no guest modifications.
        try:
            handoff.check_gpu_tls(aws, gpu_id, sleep=sleep)
        except RuntimeError:
            _ssm(aws, gpu_id, _guest_script(), sleep=sleep)
        # A final end-to-end independent guest trust check.
        handoff.check_gpu_tls(aws, gpu_id, sleep=sleep)
        handoff.gpu_preview(aws)  # Guard may have expired during installation.
        return {"instance_id": gpu_id, "acm": "verified", "hostname": handoff.HOST}
    except BaseException:
        if created:
            try:
                # EIP has NOT been handed off, so undo any newly-granted access.
                if handoff._eip(aws).get("InstanceId") != handoff.CPU_ID:
                    raise RuntimeError("EIP owner changed; cannot safely revoke")
                _revoke_grant(aws, gpu_id)
            except BaseException as e:
                raise RuntimeError("GPU enrollment failed; new IAM grant rollback unverified") from e
        raise


def revoke_gpu(aws, gpu_id):
    """Revoke only the matching GPU session, after EIP has returned to CPU."""
    eip = handoff._eip(aws)
    if (eip.get("InstanceId") != handoff.CPU_ID or
            handoff._tags(eip).get("StudioDcvInstanceId") != handoff.CPU_ID):
        raise ValueError("Return EIP and ownership tag to CPU before revoking GPU ACM")
    # An active GPU must not lose its certificate-renewal permission.
    try:
        vm = handoff._items(aws, gpu_id)[gpu_id]
    except subprocess.CalledProcessError as e:
        if "InvalidInstanceID.NotFound" not in (e.stderr or ""):
            raise
    else:
        if vm.get("State", {}).get("Name") not in ("stopped", "terminated"):
            raise ValueError("Stop/terminate GPU before revoking certificate access")
        if (handoff._tags(vm).get("StudioStack") != handoff.GPU_STACK or
                handoff._tags(vm).get("StudioCleanupToken") != handoff.GPU_TOKEN):
            raise ValueError("GPU instance ownership differs")
    revoked = _revoke_grant(aws, gpu_id)
    return {"instance_id": gpu_id, "revoked": revoked, "cpu_certificate_access": "preserved"}
