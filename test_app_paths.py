"""Portable paths must not save user data into bundled temporary resources."""
from pathlib import Path
import runpy
import tempfile
import unittest
from unittest import mock

import app_paths


class AppPathsTests(unittest.TestCase):
    def test_source_paths_keep_configuration_and_assets_in_the_project(self):
        with mock.patch("sys.frozen", False, create=True):
            paths = runpy.run_path(str(Path(app_paths.__file__)))
        project = Path(app_paths.__file__).resolve().parent
        self.assertEqual(paths["RESOURCE_ROOT"], project)
        self.assertEqual(paths["APP_ROOT"], project)
        self.assertEqual(paths["DEFAULT_CONFIG_PATH"], project / "autonexus.json")
        self.assertEqual(paths["LOG_DIR"], project / "analysis")

    def test_frozen_data_stays_beside_executable_and_assets_stay_in_internal(self):
        with tempfile.TemporaryDirectory(prefix="MiRealmBot portable ") as directory:
            release = Path(directory)
            internal = release / "_internal"
            internal.mkdir()
            module = internal / "app_paths.py"
            module.write_bytes(Path(app_paths.__file__).read_bytes())
            asset = internal / "autonexus_agent.js"
            asset.write_text("// bundled test resource", encoding="utf-8")
            executable = release / "MiRealmBot.exe"
            executable.touch()
            with mock.patch("sys.frozen", True, create=True), \
                    mock.patch("sys.executable", str(executable)):
                paths = runpy.run_path(str(module))
            self.assertEqual(paths["APP_ROOT"], release.resolve())
            self.assertEqual(paths["RESOURCE_ROOT"], internal.resolve())
            self.assertEqual(paths["DEFAULT_CONFIG_PATH"], release / "autonexus.json")
            self.assertEqual(paths["LOG_DIR"], release / "analysis")
            self.assertEqual(paths["resource_path"]("autonexus_agent.js"), asset)
            self.assertEqual(paths["resource_path"](Path("autonexus_agent.js")).read_text(encoding="utf-8"),
                             "// bundled test resource")
            self.assertFalse(paths["DEFAULT_CONFIG_PATH"].is_relative_to(internal))
            self.assertFalse(paths["LOG_DIR"].is_relative_to(internal))


if __name__ == "__main__":
    unittest.main()
