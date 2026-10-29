"""Offline structural tests for GPU component generator."""
import unittest
import yaml
from scripts.generate_gpu_imagebuilder_components import generate

class GpuComponentsTests(unittest.TestCase):
    def test_rejects_unpinned_component_versions(self):
        with self.assertRaises(ValueError):
            generate({"components":[{"name":"wrong","version":"0.5.0"}]})
    def test_desktop_stage_uses_separate_confirmation_step(self):
        from scripts.generate_gpu_imagebuilder_components import script, replace_once
        desktop = {"phases":[{"steps":[
            {"name":"InstallDesktop","inputs":{"commands":["apt-get -s install ubuntu-desktop xserver-xorg-video-dummy\\napt-get install ubuntu-desktop xserver-xorg-video-dummy"]}},
            {"name":"ConfirmDesktopStage","inputs":{"commands":["dpkg-query -W ubuntu-desktop gdm3 xserver-xorg-video-dummy"]}}
        ]}]}
        install,install_commands=script(desktop,"InstallDesktop")
        self.assertEqual(install.count("ubuntu-desktop xserver-xorg-video-dummy"),2)
        confirm,confirm_commands=script(desktop,"ConfirmDesktopStage")
        confirm_commands[0]=replace_once(confirm,
            "dpkg-query -W ubuntu-desktop gdm3 xserver-xorg-video-dummy",
            "dpkg-query -W ubuntu-desktop gdm3")
        self.assertEqual(confirm_commands[0],"dpkg-query -W ubuntu-desktop gdm3")
        self.assertEqual(install_commands[0],install)

    def test_res_utilities_precede_grid_driver(self):
        from pathlib import Path
        source=(Path(__file__).resolve().parents[1]/
                "scripts/generate_gpu_imagebuilder_components.py").read_text()
        self.assertLess(source.index("scripts/common/linux/yq.sh -s"),
                        source.index("scripts/dcv/linux/gpu_drivers.sh -o"))
        self.assertLess(source.index("scripts/common/linux/jq.sh -o"),
                        source.index("scripts/dcv/linux/gpu_drivers.sh -o"))
        self.assertIn("get_string package_config.gpu_driver.nvidia.s3_bucket_path",source)

    def test_rejects_wrong_component_order(self):
        with self.assertRaises(ValueError):
            generate({"components":[{"name":"studio-cpu-dcv-res-stage","version":"0.5.0"}]})

if __name__=="__main__":
    unittest.main()
