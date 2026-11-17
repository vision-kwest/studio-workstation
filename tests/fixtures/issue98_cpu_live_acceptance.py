"""One-shot test fixture, not installed product code; exact disposable CPU only."""
import base64
import hashlib
import io
import json
import re
import subprocess
import sys
import time

import boto3
from botocore.exceptions import ClientError
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from PIL import Image, ImageStat
from playwright.sync_api import sync_playwright

NAME = "studio-issue98-auto-20261117-cpu"
EXPECTED = "i-00751d145b5721455"
REGION = "us-east-1"
ssm = boto3.client("ssm", region_name=REGION)
ec2 = boto3.client("ec2", region_name=REGION)


def cli(*args):
    proc = subprocess.run([sys.executable, "studio", "workstation", *args],
                          text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    print("CLI", args, "EXIT", proc.returncode, flush=True)
    print(proc.stdout, flush=True)
    if proc.returncode:
        raise RuntimeError("CLI failed: " + repr(args))
    return proc.stdout


def guest(commands, *, secret_output=False):
    for attempt in range(24):
        try:
            cid = ssm.send_command(
                InstanceIds=[EXPECTED], DocumentName="AWS-RunShellScript",
                Comment="Issue98 exact CPU acceptance; no plaintext secrets",
                TimeoutSeconds=90,
                Parameters={"commands": commands, "executionTimeout": ["120"]},
            )["Command"]["CommandId"]
            break
        except ClientError as exc:
            if exc.response["Error"]["Code"] not in ("InvalidInstanceId", "TargetNotConnected"):
                raise
            time.sleep(5)
    else:
        raise RuntimeError("CPU SSM unavailable")
    for attempt in range(90):
        time.sleep(2)
        try:
            reply = ssm.get_command_invocation(CommandId=cid, InstanceId=EXPECTED)
        except ssm.exceptions.InvocationDoesNotExist:
            continue
        if reply["Status"] in ("Success", "Failed", "Cancelled", "TimedOut"):
            print("SSM", cid, reply["Status"], flush=True)
            if not secret_output:
                print(reply.get("StandardOutputContent", ""), flush=True)
                print(reply.get("StandardErrorContent", ""), flush=True)
            if reply["Status"] != "Success":
                raise RuntimeError("CPU guest test failed; inspect command " + cid)
            return reply["StandardOutputContent"]
    raise RuntimeError("CPU SSM timed out")


def disk_ids():
    vm = ec2.describe_instances(InstanceIds=[EXPECTED])["Reservations"][0]["Instances"][0]
    assert vm["InstanceId"] == EXPECTED
    assert all(m["Ebs"]["DeleteOnTermination"] for m in vm["BlockDeviceMappings"] if "Ebs" in m)
    return sorted(m["Ebs"]["VolumeId"] for m in vm["BlockDeviceMappings"] if "Ebs" in m)


def browser_login(url, password, trusted=False):
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True, args=["--no-sandbox"])
            context = browser.new_context(ignore_https_errors=not trusted,
                                          viewport={"width": 1280, "height": 800})
            page = context.new_page()
            page.goto(url + "/#console", wait_until="domcontentloaded", timeout=45000)
            page.locator("input[type=password]").wait_for(state="visible", timeout=60000)
            print("DCV_LOGIN_FORM", page.locator("input").evaluate_all(
                "nodes => nodes.map(x => ({type:x.type,id:x.id,name:x.name}))"), flush=True)
            page.locator("input[type=text],input:not([type]),input[type=email]").first.fill("workstation")
            page.locator("input[type=password]").fill(password)
            button = page.get_by_role("button", name=re.compile("login|log in|sign in|connect", re.I))
            if button.count():
                button.first.click()
            else:
                page.locator("input[type=password]").press("Enter")
            page.wait_for_function("""() => !Array.from(document.querySelectorAll('input[type=password]')).some(x => x.offsetParent !== null)
                && Array.from(document.querySelectorAll('canvas')).some(x => x.width >= 640 && x.height >= 360)""",
                timeout=90000)
            canvas = page.locator("canvas").evaluate_all("nodes => nodes.map(x => ({width:x.width,height:x.height}))")
            output = guest(["set -eu", "dcv describe-session console --json | python3 -c 'import json,sys; s=json.load(sys.stdin); assert s.get(\"owner\")==\"workstation\" and s.get(\"num-of-connections\",0)>0; print(json.dumps({k:s.get(k) for k in (\"id\",\"owner\",\"num-of-connections\")}))'"])
            shot = page.screenshot()
            image = Image.open(io.BytesIO(shot)).convert("RGB")
            variance = ImageStat.Stat(image).var
            assert max(variance) > 10, "Browser image is unexpectedly uniform"
            print("AUTHENTICATED_DCV_FRAME", json.dumps({"url":url,"trusted":trusted,"canvas":canvas,
                  "screenshot_sha256":hashlib.sha256(shot).hexdigest(),"pixel_variance":variance}), flush=True)
            context.close()
            browser.close()
    except Exception as exc:
        # Playwright diagnostics can echo input values. Never print the password.
        message = str(exc).replace(password, "[REDACTED]")
        raise RuntimeError("DCV browser test: " + message[:1200]) from None


state = json.loads(cli("status", NAME))
assert state["instance"] == EXPECTED and state["state"] == "running"
cli("check-ready", NAME)
before_disks = disk_ids()
# Existing native setup may finish after DCV; no ad-hoc mount or installation here.
guest(["set -eu", "for i in $(seq 1 90); do test -x /usr/local/bin/studio-rez-env && break; sleep 1; done",
       "test -x /usr/local/bin/studio-rez-env",
       "timeout 20 cat /opt/studio/packages/prod/issue98_smoke_20261117/0.1/payload.txt",
       "findmnt -rn -T /opt/studio/packages/prod/issue98_smoke_20261117/0.1/payload.txt -o SOURCE,FSTYPE,OPTIONS",
       "grep '^fs-054fd0b72429f4569:/ /opt/studio/packages efs .*ro,tls,iam' /etc/fstab",
       "timeout 30 runuser -u workstation -- /usr/local/bin/studio-rez-env issue98_smoke_20261117-0.1 -- sh -c 'cat \"$STUDIO_ISSUE98_SMOKE\"'",
       "runuser -u workstation -- python3 - <<'PY'\nfrom pathlib import Path\np=Path('/opt/studio/packages/prod/issue98_smoke_20261117/0.1/.consumer-write-probe')\nassert not p.exists()\ntry:\n with p.open('x') as f:f.write('probe')\nexcept OSError as e:\n assert e.errno in (13,30),e;print('PACKAGE_WRITE_DENIED',e.errno)\nelse:\n p.unlink();raise SystemExit('Unexpected consumer write permission')\nPY"])
# Only this disposable CPU gets a random test password. Transfer it encrypted,
# with the private key retained solely in this short-lived runner process.
private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
public = private.public_key().public_bytes(serialization.Encoding.PEM,
                                          serialization.PublicFormat.SubjectPublicKeyInfo).decode()
password_script = "\n".join([
    "import base64,secrets,subprocess,tempfile",
    "from pathlib import Path",
    "password='Aa1!'+secrets.token_urlsafe(32)",
    "subprocess.run(['chpasswd'],input='workstation:'+password+'\\n',text=True,capture_output=True,check=True)",
    "with tempfile.TemporaryDirectory(prefix='issue98-public-') as directory:",
    " p=Path(directory)/'public.pem';p.write_text(" + repr(public) + ")",
    " encrypted=subprocess.run(['openssl','pkeyutl','-encrypt','-pubin','-inkey',str(p),'-pkeyopt','rsa_padding_mode:oaep','-pkeyopt','rsa_oaep_md:sha256','-pkeyopt','rsa_mgf1_md:sha256'],input=password.encode(),capture_output=True,check=True).stdout",
    " print(base64.b64encode(encrypted).decode())",
])
encrypted = guest(["set -eu", "python3 - <<'PY'\n" + password_script + "\nPY"], secret_output=True).strip()
password = private.decrypt(base64.b64decode(encrypted), padding.OAEP(mgf=padding.MGF1(hashes.SHA256()),
                                                                  algorithm=hashes.SHA256(), label=None)).decode()
browser_results = []
try:
    browser_login(state["dcv_url"], password)
    browser_results.append("before:passed")
except RuntimeError as exc:
    print(str(exc), flush=True)
    browser_results.append("before:failed")
baseline = guest(["set -eu", "python3 - <<'PY'\nimport hashlib,os,json\nfrom pathlib import Path\np=Path('/var/tmp/issue98-auto-cpu-marker')\nwith p.open('x') as f:f.write('issue98-auto-cpu-20261117\\n')\np.chmod(0o600);os.sync();print(json.dumps({'hash':hashlib.sha256(p.read_bytes()).hexdigest(),'boot':Path('/proc/sys/kernel/random/boot_id').read_text().strip()}))\nPY"])
baseline = json.loads(baseline)
cli("stop", NAME)
assert json.loads(cli("status", NAME))["state"] == "stopped"
assert disk_ids() == before_disks
cli("start", NAME)
cli("check-ready", NAME)
after = json.loads(cli("status", NAME))
assert after["instance"] == EXPECTED and after["state"] == "running" and disk_ids() == before_disks
observed = guest(["set -eu", "python3 - <<'PY'\nimport hashlib,json\nfrom pathlib import Path\np=Path('/var/tmp/issue98-auto-cpu-marker');print(json.dumps({'hash':hashlib.sha256(p.read_bytes()).hexdigest(),'boot':Path('/proc/sys/kernel/random/boot_id').read_text().strip()}))\nPY"])
observed = json.loads(observed)
assert observed["hash"] == baseline["hash"] and observed["boot"] != baseline["boot"]
guest(["set -eu", "timeout 30 runuser -u workstation -- /usr/local/bin/studio-rez-env issue98_smoke_20261117-0.1 -- sh -c 'cat \"$STUDIO_ISSUE98_SMOKE\"'",
       "findmnt -rn -T /opt/studio/packages/prod/issue98_smoke_20261117/0.1/payload.txt -o SOURCE,FSTYPE,OPTIONS"])
print("CPU_STOP_START_EFS_REZ_PERSISTENCE_PASS", json.dumps({"instance":EXPECTED,"disks":before_disks,"before":baseline,"after":observed}), flush=True)
try:
    browser_login(after["dcv_url"], password)
    browser_results.append("after:passed")
except RuntimeError as exc:
    print(str(exc), flush=True)
    browser_results.append("after:failed")
# Never retain or print the generated password or browser authentication state.
del password, private
print("CPU_BROWSER_RESULTS", browser_results, flush=True)
raise SystemExit(0 if all(x.endswith(":passed") for x in browser_results) else 1)
