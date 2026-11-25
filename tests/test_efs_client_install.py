"""Native EFS provisioning remains owned by the stack after the client exits."""
import json
from pathlib import Path
import subprocess
from tempfile import TemporaryDirectory
from unittest.mock import patch

from scripts import studio_workstation_cli as cli
from runtime import workstation_inventory as inventory
from test_issue43_cli_lifecycle import CliFixture
from workstation_fake import h


class EfsClientInstallTests(CliFixture):
    def test_all_desktops_include_pinned_independent_association(self):
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
                    for step, action in zip((s for s in steps if s["action"] == "aws:runDocument"),
                                            ("Install", "Uninstall", "Install")):
                        self.assertEqual(step["inputs"]["documentPath"], "AWS-ConfigureAWSPackage")
                        self.assertEqual(json.loads(step["inputs"]["documentParameters"]), {
                            "action": action, "name": "AmazonEFSUtils", "version": "3.4.0-1",
                            "installationType": "Uninstall and reinstall"})
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

    def test_native_steps_handle_healthy_missing_and_interrupted_package_install(self):
        selected = self.create("cpu", "flow")
        document = next(r["PhysicalResourceId"] for r in selected["resources"]
                        if r["LogicalResourceId"] == "EfsClientDocument")
        steps = self.aws.documents[document]["Content"]["mainSteps"]
        with TemporaryDirectory() as directory:
            ready, helper = Path(directory) / "ready", Path(directory) / "mount.efs"
            def execute(outcomes):
                calls = []
                for step in steps:
                    inputs = step["inputs"]
                    if step["action"] == "aws:runShellScript":
                        command = "\n".join(inputs["runCommand"]).replace(
                            "/root/bootstrap/semaphore/linux_vdi_config_host_ready.lock", str(ready))
                        command = command.replace("/usr/sbin/mount.efs", str(helper))
                        ok = subprocess.run(["bash", "-c", command], capture_output=True).returncode == 0
                    else:
                        calls.append(json.loads(inputs["documentParameters"])["action"])
                        status, installed = next(outcomes)
                        if status == "Interrupted":
                            return status, calls
                        if installed:
                            helper.write_text("#!/bin/sh\necho mount.efs3.4.0\n")
                            helper.chmod(0o755)
                        ok = status == "Success"
                    if not ok:
                        control = inputs.get("onFailure")
                        self.assertIn(control, ("exit", "successAndExit"))
                        return ("Success" if control == "successAndExit" else "Failed"), calls
                return "Success", calls
            self.assertEqual(execute(iter(())), ("Failed", []))  # RES has not finished.
            ready.touch()
            self.assertEqual(execute(iter([("Interrupted", False)])), ("Interrupted", ["Install"]))
            # Model the verified Distributor bug: resumed Install reports Success
            # while the helper is still absent; the native repair must run now.
            self.assertEqual(execute(iter([("Success", False), ("Success", False), ("Success", True)])),
                             ("Success", ["Install", "Uninstall", "Install"]))
            self.assertEqual(execute(iter(())), ("Success", []))  # Healthy helper is preserved.
            helper.write_text("#!/bin/sh\nexit 42\n")
            self.assertEqual(execute(iter([("Success", False), ("Success", False), ("Success", True)])),
                             ("Success", ["Install", "Uninstall", "Install"]))
            helper.unlink()
            self.assertEqual(execute(iter([("Failed", False)])), ("Failed", ["Install"]))
            self.assertEqual(execute(iter([("TimedOut", False)])), ("Failed", ["Install"]))
            self.assertEqual(execute(iter([("Success", True)])), ("Success", ["Install"]))
            helper.unlink()
            self.assertEqual(execute(iter([("Success", False)] * 3)),
                             ("Failed", ["Install", "Uninstall", "Install"]))  # Final verification fails closed.

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
