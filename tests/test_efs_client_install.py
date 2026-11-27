"""Native EFS provisioning remains owned by the stack after the client exits."""
import fcntl
import os
from pathlib import Path
import subprocess
from tempfile import TemporaryDirectory
from unittest.mock import patch

from scripts import studio_workstation_cli as cli
from runtime import workstation_inventory as inventory
from test_issue43_cli_lifecycle import CliFixture
from workstation_fake import h


class EfsClientInstallTests(CliFixture):
    def test_all_desktops_include_res_independent_association(self):
        for profile in ("cpu", "gpu"):
            for software in (True, False):
                with self.subTest(profile=profile, software=software):
                    selected = self.create(profile, f"{profile}-{software}",
                                           *([] if software else ["--no-studio-software"]))
                    resources = {r["LogicalResourceId"]: r for r in selected["resources"]}
                    association = self.aws.associations[resources["EfsClientAssociation"]["PhysicalResourceId"]]
                    document = resources["EfsClientDocument"]["PhysicalResourceId"]
                    self.assertEqual(association["Name"], document)
                    steps = self.aws.documents[document]["Content"]["mainSteps"]
                    self.assertEqual([s["name"] for s in steps],
                                     ["resReady", "needsInstallation", "install", "verifyHelper"])
                    self.assertTrue(all(s["action"] == "aws:runShellScript" for s in steps))
                    self.assertEqual(steps[2]["inputs"]["timeoutSeconds"], "1200")
                    self.assertEqual(association["Targets"], [{"Key": "tag:StudioStackId",
                                                              "Values": [selected["stack_id"]]}])
                    self.assertEqual(association["ScheduleExpression"], "rate(30 minutes)")
                    self.assertNotIn("WaitForSuccessTimeoutSeconds", association)
                    self.assertNotIn("DocumentVersion", association)
                    self.assertNotIn("ApplyOnlyAtCronInterval", association)
                    role = resources["EfsClientAssociationRole"]["PhysicalResourceId"]
                    self.assertEqual(association["AssociationDispatchAssumeRole"], self.aws.roles[role]["Arn"])
                    policy = self.aws.policies[role]["InstallEfsClient"]["Statement"]
                    self.assertEqual(policy, [{"Effect": "Allow", "Action": "ssm:SendCommand", "Resource": [
                        f"arn:aws:ssm:{h.REGION}:{h.ACCOUNT}:document/{document}",
                        f"arn:aws:ec2:{h.REGION}:{h.ACCOUNT}:instance/{selected['instance']}"]}])
                    creates = [c for c in self.aws.calls if c[:2] == ("cloudformation", "create-stack")
                               and c[c.index("--stack-name") + 1] == selected["stack"]]
                    capability = creates[0][creates[0].index("--capabilities") + 1]
                    self.assertIn(capability, ("CAPABILITY_IAM", "CAPABILITY_NAMED_IAM"))
        self.assertFalse(any(c[:2] == ("ssm", "send-command") and "AWS-ConfigureAWSPackage" in c
                             for c in self.aws.calls))

    def test_generated_shell_builds_res_packages_and_repairs_only_when_needed(self):
        selected = self.create("cpu", "flow")
        document = next(r["PhysicalResourceId"] for r in selected["resources"]
                        if r["LogicalResourceId"] == "EfsClientDocument")
        steps = self.aws.documents[document]["Content"]["mainSteps"]
        with TemporaryDirectory() as directory:
            root = Path(directory)
            ready, helper, log = root / "ready", root / "mount.efs", root / "calls"
            native = root / "scripts/common/linux/debian/nfs_utils.sh"
            native.parent.mkdir(parents=True)
            (root / "scripts/virtual-desktop-host").mkdir()
            binaries, builds = root / "bin", root / "builds"
            binaries.mkdir()
            builds.mkdir()
            native.write_text(r"""
get_stunnel_package_name() { echo studio-test-stunnel; }
install_stunnel_impl() { printf 'stunnel\n' >> "$EFS_TEST_LOG"; }
native_build() {
    local _old_path=$PATH
    # RES installs this RETURN trap in the inner build function; the outer
    # return runs it again after _old_path has gone out of scope.
    trap 'PATH="$_old_path"' RETURN
    test "$BASE_OS" = ubuntu2404
    test "$NEEDRESTART_SUSPEND" = 1
    printf 'build\t%s\t%s\n' "$EFS_TEST_VERSION" "$PWD" >> "$EFS_TEST_LOG"
    test ! -e efs-utils
    mkdir -p efs-utils/build
    printf '%s\n' "$EFS_TEST_VERSION" > "efs-utils/build/amazon-efs-utils-$EFS_TEST_VERSION.deb"
    if test "$EFS_TEST_OUTCOME" = fail; then return 42; fi
    if test "$EFS_TEST_OUTCOME" = install; then
        printf '#!/bin/sh\necho mount.efs-%s\n' "$EFS_TEST_VERSION" > "$EFS_TEST_HELPER"
        chmod +x "$EFS_TEST_HELPER"
    fi
}
install_efs_mount_helper_impl() { native_build; }
""")
            apt = binaries / "apt"
            apt.write_text(r"""#!/bin/sh
set -e
printf 'reinstall\t%s\n' "$*" >> "$EFS_TEST_LOG"
test "$#" = 4 && test "$1" = install && test "$2" = -y && test "$3" = --reinstall
case "$4" in "$TMPDIR"/*/efs-utils/build/amazon-efs-utils*deb) ;; *) exit 91 ;; esac
version=$(cat "$4")
test "$version" = "$EFS_TEST_VERSION"
test "$EFS_TEST_OUTCOME" != apt-fail
if test "$EFS_TEST_OUTCOME" = repair; then
    printf '#!/bin/sh\necho mount.efs-%s\n' "$version" > "$EFS_TEST_HELPER"
    chmod +x "$EFS_TEST_HELPER"
fi
""")
            apt.chmod(0o755)
            # Watchdog presence must not hide a missing or broken mount helper.
            watchdog = binaries / "amazon-efs-mount-watchdog"
            watchdog.write_text("#!/bin/sh\nexit 0\n")
            watchdog.chmod(0o755)
            workspaces = []

            def execute(outcome="install", version="8.2.7"):
                log.unlink(missing_ok=True)
                environment = {**os.environ, "PATH": f"{binaries}:{os.environ['PATH']}",
                               "TMPDIR": str(builds), "EFS_TEST_LOG": str(log),
                               "EFS_TEST_HELPER": str(helper), "EFS_TEST_VERSION": version,
                               "EFS_TEST_OUTCOME": outcome}
                status = "Success"
                for step in steps:
                    inputs = step["inputs"]
                    command = "\n".join(inputs["runCommand"]).replace(
                        "/root/bootstrap/semaphore/linux_vdi_config_host_ready.lock", str(ready))
                    command = command.replace("/root/bootstrap/latest", str(root)).replace(
                        "/usr/sbin/mount.efs", str(helper))
                    command = command.replace("/run/studio-efs-install.lock", str(root / "install.lock"))
                    if step["name"] == "install" and outcome == "race":
                        helper.write_text("#!/bin/sh\nexit 0\n")
                        helper.chmod(0o755)
                    result = subprocess.run(["/bin/sh", "-c", command], env=environment,
                                            capture_output=True, text=True, timeout=10)
                    if result.returncode:
                        control = inputs.get("onFailure")
                        self.assertIn(control, ("exit", "successAndExit"))
                        status = "Success" if control == "successAndExit" else "Failed"
                        break
                calls = log.read_text().splitlines() if log.exists() else []
                for call in calls:
                    if call.startswith("build\t"):
                        _, selected_version, workspace = call.split("\t")
                        self.assertEqual(selected_version, version)
                        self.assertNotIn(workspace, workspaces)
                        workspaces.append(workspace)
                        self.assertFalse(Path(workspace).exists(), "Build workspace must be cleaned")
                return status, [call.split("\t")[0] for call in calls]

            self.assertEqual(execute(), ("Failed", []))  # RES has not finished.
            ready.touch()
            with (root / "install.lock").open("w") as lock:
                fcntl.flock(lock, fcntl.LOCK_EX)
                self.assertEqual(execute(), ("Failed", []))  # Another native invocation owns the build.
            self.assertEqual(execute("race"), ("Success", []))  # Recheck after acquiring the lock.
            helper.unlink()
            for version in ("8.2.7", "9.1.0"):
                with self.subTest(version=version):
                    self.assertEqual(execute("fail", version), ("Failed", ["stunnel", "build"]))
                    self.assertEqual(execute("install", version), ("Success", ["stunnel", "build"]))
                    self.assertIn(version, helper.read_text())
                    self.assertEqual(execute("fail", "99.0"), ("Success", []))  # No healthy upgrade.
                    helper.write_text("#!/bin/sh\nexit 42\n")
                    self.assertEqual(execute("repair", version),
                                     ("Success", ["stunnel", "build", "reinstall"]))
                    self.assertIn(version, helper.read_text())
                    helper.unlink()
                    self.assertEqual(execute("apt-fail", version),
                                     ("Failed", ["stunnel", "build", "reinstall"]))
                    self.assertEqual(execute("no-helper", version),
                                     ("Failed", ["stunnel", "build", "reinstall"]))
            self.assertEqual(list(builds.iterdir()), [])

    def test_client_exit_during_readiness_leaves_native_setup_and_desktop_running(self):
        for profile in ("cpu", "gpu"):
            name = "interrupted-" + profile
            with self.subTest(profile=profile), patch.object(cli, "wait_gpu_bootstrap", side_effect=KeyboardInterrupt):
                with self.assertRaises(KeyboardInterrupt):
                    self.invoke("create", "--profile", profile, "--name", name)
            selected = inventory.context(self.aws, h.REGION, name)
            self.assertEqual(selected["state"], "running")
            resource = next(r for r in selected["resources"] if r["LogicalResourceId"] == "EfsClientAssociation")
            self.assertIn(resource["PhysicalResourceId"], self.aws.associations)
        self.assertFalse(any(c[1] == "stop-instances" for c in self.aws.calls))

    def test_native_destroy_removes_own_association_and_preserves_other_desktop(self):
        removed = self.create("gpu", "remove")
        retained = self.create("cpu", "keep")
        retained_associations = {r["PhysicalResourceId"]: self.aws.associations[r["PhysicalResourceId"]]
                                 for r in retained["resources"] if r["ResourceType"] == "AWS::SSM::Association"}
        self.invoke("destroy", removed["stack"], "--delete-stack")
        self.assertEqual(self.aws.associations, retained_associations)
        self.assertEqual(set(self.aws.documents), {v["Name"] for v in retained_associations.values()})
        self.assertEqual(inventory.context(self.aws, h.REGION, retained["stack"])["state"], "running")
