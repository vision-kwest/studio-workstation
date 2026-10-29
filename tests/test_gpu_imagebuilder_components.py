"""Offline structural tests for GPU component generator."""
import unittest
from scripts.generate_gpu_imagebuilder_components import generate

class GpuComponentsTests(unittest.TestCase):
    def test_rejects_unpinned_component_versions(self):
        with self.assertRaises(ValueError):
            generate({"components":[{"name":"wrong","version":"0.5.0"}]})
    def test_rejects_wrong_component_order(self):
        with self.assertRaises(ValueError):
            generate({"components":[{"name":"studio-cpu-dcv-res-stage","version":"0.5.0"}]})

if __name__=="__main__":
    unittest.main()
