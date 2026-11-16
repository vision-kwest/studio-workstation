"""Read-only hostname status no longer depends on a historical CPU anchor."""
import json
from test_issue43_cli_lifecycle import CliFixture

class HostnameStatusTests(CliFixture):
    def test_unassociated_status_does_not_create_or_start_anything(self):
        result=json.loads(self.invoke('hostname','status'))
        self.assertIsNone(result['owner'])
        self.assertEqual(self.aws.writes,[])

    def test_status_reports_selected_owner_without_a_return_cpu(self):
        a=self.create('gpu','art');self.invoke('hostname','assign','art');self.aws.calls=[]
        result=json.loads(self.invoke('hostname','status'))
        self.assertEqual(result['owner'],a['stack'])
        self.assertEqual(self.aws.writes,[])
