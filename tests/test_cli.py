import unittest
from workstation.cli import describe

class TestDescribe(unittest.TestCase):
    def test_single_instance(self):
        class EC2:
            def describe_instances(self, **kwargs):
                return {'Reservations': [{'Instances': [{'InstanceId': 'i-123', 'State': {'Name': 'stopped'}}]}]}
        self.assertEqual(describe(EC2(), 'i-123')['InstanceId'], 'i-123')

    def test_missing_instance(self):
        class EC2:
            def describe_instances(self, **kwargs):
                return {'Reservations': []}
        with self.assertRaises(RuntimeError):
            describe(EC2(), 'i-123')

if __name__ == '__main__': unittest.main()
