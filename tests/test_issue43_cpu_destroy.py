"""CPU and publisher disks follow the same native disposable instance lifecycle."""
import copy
import json
import unittest
from types import SimpleNamespace
from unittest.mock import Mock
from runtime import issue43_destroy as cleanup, publisher, workstation_inventory as inventory
from workstation_fake import AWS, h


class NativeNonGpuTeardownTests(unittest.TestCase):
    def test_cpu_stop_preserves_disk_and_destroy_deletes_it(self):
        aws=AWS();cpu=aws.desktop('cpu-edit','cpu');disks=copy.deepcopy(aws.volumes)
        aws(h.REGION,'ec2','stop-instances','--instance-ids',cpu['instance'])
        self.assertEqual(aws.volumes,disks)
        cleanup.destroy(aws,Mock(),cpu['stack'],h.REGION,sleep=lambda _:None)
        self.assertFalse(aws.volumes);self.assertFalse(aws.schedules)
        self.assertFalse(any(c[1]=='delete-volume' for c in aws.calls))

    def test_publisher_template_reuses_bootstrap_and_dedicated_profile(self):
        aws=AWS();body=publisher.template(SimpleNamespace(security_group_id='sg-existing'))
        props=body['Resources']['Workstation']['Properties']
        self.assertEqual(set(body['Resources']),{'Workstation'})
        self.assertEqual(props['SecurityGroupIds'],['sg-existing'])
        self.assertTrue(props['BlockDeviceMappings'][0]['Ebs']['DeleteOnTermination'])
        self.assertTrue(props['PropagateTagsToVolumeOnCreation'])
        self.assertIn(publisher.BOOTSTRAP,props['UserData']['Fn::Base64'])
        self.assertNotIn('dcv',props['UserData']['Fn::Base64'].lower())
        aws.native_create('publish',body,{'ImageId':'ami-reviewed','InstanceType':'m7i.large',
            'InstanceProfileName':'StudioEfsCiPublisher','SubnetId':'subnet-reviewed','DiskSizeGiB':30},
            {'StudioManaged':'true','StudioProfile':'publisher'})
        selected=inventory.context(aws,h.REGION,'publish')
        self.assertIsNone(selected['dcv_url'])
        cleanup.destroy(aws,Mock(),'publish',h.REGION,sleep=lambda _:None)
        self.assertFalse(aws.volumes)
        self.assertFalse(any(c[0] in ('iam','scheduler','route53') for c in aws.calls))
