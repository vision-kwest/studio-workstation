"""End-to-end command journey; AWS and transport are explicitly simulated."""
import json
from test_issue43_cli_lifecycle import CliFixture
from workstation_fake import h

class OfflineLifecycleJourney(CliFixture):
    def test_two_gpu_cpu_publisher_transfer_restart_and_native_teardown(self):
        records=[self.create(profile,name) for profile,name in
                 [('gpu','art'),('gpu','ai'),('cpu','edit'),('publisher','publish')]]
        self.assertEqual(len(json.loads(self.invoke('list'))),4)
        for name in ('art','ai','edit'):
            self.invoke('hostname','assign',name)
            for other in ('art','ai','edit'):
                url=self.invoke('connect',other)
                self.assertEqual(h.HOST in url,other==name)
        for selected in records:
            self.invoke('stop',selected['stack'])
            self.invoke('start',selected['stack'])
        self.invoke('hostname','assign','ai')
        self.invoke('hostname','release')
        for selected in records:
            self.invoke('destroy',selected['stack'])
            self.assertEqual(self.aws.stacks[selected['stack_id']]['StackStatus'],'DELETE_COMPLETE')
        self.assertFalse(self.aws.volumes)
        self.assertFalse(self.aws.schedules)
        self.assertEqual(self.aws.address['AllocationId'],h.EIP_ID)
        self.assertIsNone(self.aws.address.get('InstanceId'))
        self.assertFalse(any(c[1] in ('delete-volume','allocate-address','release-address','change-resource-record-sets') for c in self.aws.calls))
