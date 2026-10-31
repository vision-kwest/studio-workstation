import importlib.util
from pathlib import Path
import unittest
from unittest.mock import Mock, call

spec=importlib.util.spec_from_file_location("launcher",Path(__file__).resolve().parents[1]/"runtime"/"task7_safe_launcher.py")
m=importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

class SafeLauncherTests(unittest.TestCase):
    def setUp(self):
        self.t={"Parameters":{"InstanceType":{"AllowedValues":["g4dn.xlarge"]},
                               "ClientCidr":{"Default":"127.0.0.1/32"}},
          "Resources":{"Workstation":{"Properties":{
            "MetadataOptions":{"HttpTokens":"required"},
            "InstanceInitiatedShutdownBehavior":"terminate",
            "BlockDeviceMappings":[{"Ebs":{"DeleteOnTermination":True}}],
            "Tags":[{"Key":"Task7","Value":"PR61"},{"Key":"Issue","Value":"60"}],
            "UserData":{"Fn::Base64":{"Fn::Sub":"STUDIO_STANDALONE=true -g NVIDIA s3://x/releases/task7-pr61-efsfix-aab7a466/res-2026.06/installation-scripts.tar.gz virtual-desktop.tar.gz"}}}}}}
        self.ec2=Mock()
        self.ec2.create_security_group.return_value={"GroupId":"sg-test"}
        self.ec2.run_instances.return_value={"Instances":[{"InstanceId":"i-test"}]}
        self.s=Mock()
        self.s.create_schedule.return_value={"ScheduleArn":"arn:test"}
        def readback(**kw):
            params=self.s.create_schedule.call_args.kwargs
            return {"State":"ENABLED","ScheduleExpression":params["ScheduleExpression"],
                    "ScheduleExpressionTimezone":"UTC","Target":params["Target"]}
        self.s.get_schedule.side_effect=readback

    def test_success_schedules_exact_instance_before_return(self):
        result=m.launch(self.ec2,self.s,self.t,30)
        self.assertEqual(result["instance_id"],"i-test")
        self.assertEqual(self.s.create_schedule.call_args.kwargs["Name"],"studio-task7-pr61-i-test")
        self.ec2.terminate_instances.assert_not_called()
        self.assertEqual(self.ec2.run_instances.call_args.kwargs["SecurityGroupIds"],["sg-test"])

    def test_schedule_creation_failure_terminates(self):
        self.s.create_schedule.side_effect=RuntimeError("scheduler denied")
        with self.assertRaisesRegex(RuntimeError,"scheduler denied"):
            m.launch(self.ec2,self.s,self.t)
        self.ec2.terminate_instances.assert_called_once_with(InstanceIds=["i-test"])

    def test_schedule_readback_failure_terminates(self):
        self.s.get_schedule.return_value={"State":"DISABLED","ScheduleExpression":"bad","Target":{}}
        self.s.get_schedule.side_effect=None
        with self.assertRaisesRegex(RuntimeError,"readback mismatch"):
            m.launch(self.ec2,self.s,self.t)
        self.ec2.terminate_instances.assert_called_once_with(InstanceIds=["i-test"])

    def test_launch_failure_deletes_unused_security_group(self):
        self.ec2.run_instances.side_effect=RuntimeError("capacity")
        with self.assertRaisesRegex(RuntimeError,"capacity"):
            m.launch(self.ec2,self.s,self.t)
        self.ec2.delete_security_group.assert_called_once_with(GroupId="sg-test")

    def test_unapproved_deadline_rejected(self):
        with self.assertRaises(ValueError):
            m.launch(self.ec2,self.s,self.t,300)
        self.ec2.run_instances.assert_not_called()

if __name__=="__main__":
    unittest.main()
