"""Distribution boundaries: no private recordings, game files, or personal config."""
from pathlib import Path
import tempfile
import unittest
import zipfile

from tools.build_portable import (ROOT, REQUIRED_PAYLOAD, archive_distribution,
                                  validate_distribution)


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
                         "backup.json.bak", "capture.jsonl", ".venv/private.txt"):
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

    def test_missing_agent_is_rejected(self):
        (self.app / "_internal/autonexus_agent.js").unlink()
        with self.assertRaisesRegex(RuntimeError, "incompleta"):
            validate_distribution(self.app)

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
