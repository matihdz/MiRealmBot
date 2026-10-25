"""Distribution boundaries: no private recordings, game files, or personal config."""
from pathlib import Path
import hashlib
import importlib.util
import json
import tempfile
import unittest
import zipfile

from tools.build_portable import (ROOT, REQUIRED_PAYLOAD, RESOURCE_FILES, archive_distribution,
                                  validate_distribution)

launcher_spec = importlib.util.spec_from_file_location("portable_launcher", ROOT / "packaging/launcher.py")
launcher = importlib.util.module_from_spec(launcher_spec)
launcher_spec.loader.exec_module(launcher)


class PortableBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="mirealmbot-portable-")
        self.addCleanup(self.directory.cleanup)
        self.app = Path(self.directory.name) / "MiRealmBot"
        self.app.mkdir()
        for relative in REQUIRED_PAYLOAD:
            path = self.app / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"public test asset")
        (self.app / "autonexus.json").write_bytes((ROOT / "autonexus.example.json").read_bytes())

    def test_public_payload_is_accepted(self):
        validate_distribution(self.app)

    def test_saved_personal_config_is_rejected(self):
        (self.app / "autonexus.json").write_text('{"threshold":0.40}', encoding="utf-8")
        with self.assertRaisesRegex(RuntimeError, "configuración pública"):
            validate_distribution(self.app)

    def test_capture_and_game_binary_are_rejected(self):
        for relative in ("analysis/private.jsonl", "_internal/GameAssembly.dll",
                         "backup.json.bak", "capture.jsonl", ".venv/private.txt",
                         "_internal/examples/demo-combat.jsonl", "_internal/replay_autonexus.py"):
            with self.subTest(relative=relative):
                target = self.app / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text("private", encoding="utf-8")
                with self.assertRaisesRegex(RuntimeError, "no deben publicarse"):
                    validate_distribution(self.app)
                target.unlink()
                # An empty private directory is also forbidden.
                if target.parent.name in ("analysis", ".venv"):
                    target.parent.rmdir()
                if target.parent.name == "examples":
                    target.parent.rmdir()

    def test_missing_release_assets_are_rejected(self):
        selected = tuple(f"_internal/{relative}" for relative in RESOURCE_FILES) + (
            "licenses/OpenSSL-3.0.16-LICENSE.txt", "licenses/OpenSSL-3.0.16-AUTHORS.md")
        for relative in selected:
            with self.subTest(relative=relative):
                asset = self.app / relative
                original = asset.read_bytes()
                asset.unlink()
                with self.assertRaisesRegex(RuntimeError, "incompleta"):
                    validate_distribution(self.app)
                asset.write_bytes(original)

    def write_manifest(self, version="0.2.0"):
        manifest = {
            "format": 1, "application": "MiRealmBot", "version": version,
            "resource_files": list(RESOURCE_FILES),
            "files": {"_internal/" + relative: {
                "bytes": (self.app / "_internal" / relative).stat().st_size,
                "sha256": hashlib.sha256((self.app / "_internal" / relative).read_bytes()).hexdigest(),
            } for relative in RESOURCE_FILES},
        }
        (self.app / "BUILD-MANIFEST.json").write_text(json.dumps(manifest), encoding="utf-8")

    def test_offline_self_test_checks_every_retained_resource_and_version(self):
        self.write_manifest()
        report = launcher.check_release_assets(self.app, self.app / "_internal", "0.2.0")
        self.assertEqual(report["manifest_version"], "0.2.0")
        self.assertEqual(set(report["checked_resources"]), set(RESOURCE_FILES))
        for version in ("0.1.0", "0.3.0"):
            with self.subTest(version=version), self.assertRaisesRegex(RuntimeError, "versión"):
                launcher.check_release_assets(self.app, self.app / "_internal", version)

    def test_offline_self_test_rejects_missing_or_altered_scripts_without_execution(self):
        self.write_manifest()
        for relative in ("updater_worker.ps1", "game_bindings_agent.js", "autoaim_agent.js"):
            with self.subTest(relative=relative):
                asset = self.app / "_internal" / relative
                original = asset.read_bytes()
                asset.unlink()
                with self.assertRaisesRegex(RuntimeError, "Falta el recurso"):
                    launcher.check_release_assets(self.app, self.app / "_internal", "0.2.0")
                asset.write_bytes(b"replacement must never execute")
                with self.assertRaisesRegex(RuntimeError, "no coincide"):
                    launcher.check_release_assets(self.app, self.app / "_internal", "0.2.0")
                asset.write_bytes(original)

    def test_zip_keeps_one_top_folder_and_cannot_replace_existing_asset(self):
        destination = self.app.parent / "download.zip"
        archive_distribution(self.app, destination)
        with zipfile.ZipFile(destination) as archive:
            names = archive.namelist()
        self.assertIn("MiRealmBot/MiRealmBot.exe", names)
        self.assertTrue(all(name.startswith("MiRealmBot/") and ".." not in name for name in names))
        with self.assertRaises(FileExistsError):
            archive_distribution(self.app, destination)


if __name__ == "__main__":
    unittest.main()
