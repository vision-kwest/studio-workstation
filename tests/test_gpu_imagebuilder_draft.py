"""Offline checks for GPU Image Builder draft; no AWS resources."""
import unittest
from scripts.derive_gpu_imagebuilder import generate

class TestGpuDraft(unittest.TestCase):
    def test_preserves_cpu_source_and_changes_only_gpu_builder(self):
        audit={
            "pipeline":{"arn":"cpu-pipeline"},
            "infrastructure":{"instanceTypes":["m7i.xlarge"],"instanceProfileName":"builder-role",
                              "terminateInstanceOnFailure":True,"logging":{"s3Logs":{}}},
            "distribution":{"distributions":[{"region":"us-east-1","amiDistributionConfiguration":{"name":"studio-cpu-dcv-{{ imagebuilder:buildDate }}"}}]},
            "recipe":{"arn":"cpu-recipe","name":"studio-cpu-ubuntu2404-dcv",
                      "version":"0.5.0","parentImage":"ami-base"},
            "components":[{"name":n} for n in (
                "studio-cpu-gdm-first","studio-cpu-desktop-res-stage",
                "studio-cpu-dcv-res-stage","studio-cpu-dcv-smoke-test-res")]
        }
        result=generate(audit)
        self.assertEqual(result["status"],"REVIEW_ONLY_NOT_DEPLOYABLE")
        self.assertEqual(result["gpu_infrastructure"]["instanceTypes"],["g4dn.xlarge"])
        self.assertEqual(result["gpu_infrastructure"]["instanceProfileName"],"builder-role")
        self.assertEqual(result["gpu_distribution"]["distributions"][0]["amiDistributionConfiguration"]["name"],
                         "studio-gpu-dcv-{{ imagebuilder:buildDate }}")
        self.assertEqual(audit["infrastructure"]["instanceTypes"],["m7i.xlarge"])
        self.assertEqual(audit["distribution"]["distributions"][0]["amiDistributionConfiguration"]["name"],
                         "studio-cpu-dcv-{{ imagebuilder:buildDate }}")
    def test_rejects_unrecognized_cpu_recipe(self):
        with self.assertRaises((ValueError,KeyError)):
            generate({"pipeline":{},"recipe":{"name":"other","version":"0.5.0"}})

if __name__=="__main__":
    unittest.main()
