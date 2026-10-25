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
        self.assertEqual(paths["DEFAULT_CONFIG_PATH"], project / "mirealmbot.json")
        self.assertEqual(paths["LEGACY_CONFIG_PATH"], project / "autonexus.json")
        self.assertEqual(paths["CONFIG_TEMPLATE_PATH"], project / "mirealmbot.example.json")
        self.assertEqual(paths["LOG_DIR"], project / "analysis")

    def test_frozen_data_stays_beside_executable_and_assets_stay_in_internal(self):
        with tempfile.TemporaryDirectory(prefix="MiRealmBot portable ") as directory:
            release = Path(directory)
            internal = release / "_internal"
            internal.mkdir()
            module = internal / "app_paths.py"
            module.write_bytes(Path(app_paths.__file__).read_bytes())
            asset = internal / "bot_agent.js"
            asset.write_text("// bundled test resource", encoding="utf-8")
            executable = release / "MiRealmBot.exe"
            executable.touch()
            with mock.patch("sys.frozen", True, create=True), \
                    mock.patch("sys.executable", str(executable)):
                paths = runpy.run_path(str(module))
            self.assertEqual(paths["APP_ROOT"], release.resolve())
            self.assertEqual(paths["RESOURCE_ROOT"], internal.resolve())
            self.assertEqual(paths["DEFAULT_CONFIG_PATH"], release / "mirealmbot.json")
            self.assertEqual(paths["LOG_DIR"], release / "analysis")
            self.assertEqual(paths["resource_path"]("bot_agent.js"), asset)
            self.assertEqual(paths["resource_path"](Path("bot_agent.js")).read_text(encoding="utf-8"),
                             "// bundled test resource")
            self.assertFalse(paths["DEFAULT_CONFIG_PATH"].is_relative_to(internal))
            self.assertFalse(paths["LOG_DIR"].is_relative_to(internal))

    def test_legacy_configuration_migrates_exact_bytes_with_backup_once(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            legacy = root / "autonexus.json"
            original = b'\xef\xbb\xbf{"threshold": 0.25, "unknown": {"kept": true}}\r\n'
            legacy.write_bytes(original)
            (root / "mirealmbot.example.json").write_bytes(b'{"threshold": 0.9}')
            canonical = app_paths.ensure_default_config(root=root)
            self.assertEqual(canonical, root / "mirealmbot.json")
            self.assertEqual(canonical.read_bytes(), original)
            self.assertEqual(legacy.read_bytes(), original)
            backups = list(root.glob("*.bak"))
            self.assertEqual(len(backups), 1)
            self.assertEqual(backups[0].read_bytes(), original)
            self.assertEqual(app_paths.ensure_default_config(root=root), canonical)
            self.assertEqual(list(root.glob("*.bak")), backups)

    def test_canonical_file_wins_over_legacy_and_missing_template(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            canonical = root / "mirealmbot.json"
            canonical.write_bytes(b'{"autonexus_enabled": false}')
            (root / "autonexus.json").write_bytes(b'{"threshold": 0.25}')
            self.assertEqual(app_paths.ensure_default_config(root=root), canonical)
            self.assertEqual(canonical.read_bytes(), b'{"autonexus_enabled": false}')
            self.assertEqual(list(root.glob("*.bak")), [])

    def test_first_install_copies_public_template_and_never_uses_current_directory(self):
        with tempfile.TemporaryDirectory(prefix="MiRealmBot config ") as directory:
            root = Path(directory)
            template = root / "mirealmbot.example.json"
            original = b'{"autonexus_enabled": true, "threshold": 0.9}\n'
            template.write_bytes(original)
            with mock.patch.object(app_paths, "DEFAULT_CONFIG_PATH", root / "mirealmbot.json"), \
                    mock.patch.object(app_paths, "LEGACY_CONFIG_PATH", root / "autonexus.json"), \
                    mock.patch.object(app_paths, "CONFIG_TEMPLATE_PATH", template):
                canonical = app_paths.ensure_default_config()
            self.assertEqual(canonical.read_bytes(), original)
            self.assertEqual(template.read_bytes(), original)
            self.assertEqual(list(root.glob("*.bak")), [])

    def test_failed_initial_write_preserves_legacy_and_backup(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            legacy = root / "autonexus.json"
            legacy.write_bytes(b'{"threshold": 0.25}')
            original_open = Path.open

            def denied(path, *args, **kwargs):
                if path.name == "mirealmbot.json":
                    raise PermissionError("read-only installation")
                return original_open(path, *args, **kwargs)

            with mock.patch.object(Path, "open", denied), self.assertRaises(PermissionError):
                app_paths.ensure_default_config(root=root)
            self.assertEqual(legacy.read_bytes(), b'{"threshold": 0.25}')
            self.assertFalse((root / "mirealmbot.json").exists())
            self.assertEqual(len(list(root.glob("*.bak"))), 1)


if __name__ == "__main__":
    unittest.main()
