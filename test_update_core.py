"""Offline release validation plus real, isolated PowerShell install transactions."""
from __future__ import annotations

import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
import zipfile

import update_core as updates
from app_paths import ensure_default_config


# Byte-exact script from the v0.3.1 release build asset:
# https://github.com/matihdz/MiRealmBot/releases/download/v0.3.1/MiRealmBot-Windows-x64.zip
# ZIP SHA256: 8c16e46073ec1c1aa536f275b3da2e88d0e8803b8b6ecf873c6e565199cc156d
LEGACY_WORKER_PATH = Path(__file__).parent / "tests/fixtures/updater_worker_v0_3_1.ps1"
LEGACY_WORKER_SHA256 = "3d349a5b7558f3acb212fa0a5c66da9c3badbe730d6d32d3678eacd54d87b90e"


def modern_bundle_files(version, *, executable=b"synthetic exe"):
    resources = ["bot_agent.js", "autonexus_agent.js", "autoaim_agent.js",
                 "debuffs_agent.js", "game_bindings_agent.js", "updater_worker.ps1"]
    files = {"MiRealmBot.exe": executable, "_internal/python312.dll": b"dll",
             "mirealmbot.example.json": b'{"threshold":0.9}',
             "autonexus.json": b'{"threshold":0.9}',
             "BUILD-MANIFEST.json": json.dumps({
                 "application": "MiRealmBot", "version": version,
                 "configuration_source": "mirealmbot.example.json",
                 "resource_files": resources,
             }).encode()}
    files.update({"_internal/" + name: b"test resource; never executed" for name in resources})
    return files


def release_payload(version="0.3.0", archive=b"download"):
    tag = "v" + version
    return {
        "tag_name": tag, "draft": False, "prerelease": False, "body": "Cambios de prueba",
        "assets": [
            {"name": updates.ZIP_NAME, "size": len(archive), "state": "uploaded",
             "browser_download_url": updates.REPOSITORY_URL + "/releases/download/" + tag + "/" + updates.ZIP_NAME,
             "digest": "sha256:" + hashlib.sha256(archive).hexdigest()},
            {"name": updates.CHECKSUM_NAME, "size": 99, "state": "uploaded",
             "browser_download_url": updates.REPOSITORY_URL + "/releases/download/" + tag + "/" + updates.CHECKSUM_NAME},
        ],
    }


class ReleaseTests(unittest.TestCase):
    def test_compares_stable_versions_as_numbers_and_never_downgrades(self):
        with patch.object(updates, "_read_small", return_value=json.dumps(release_payload("0.10.0")).encode()):
            self.assertTrue(updates.check_for_update("0.9.0").available)
            self.assertFalse(updates.check_for_update("0.10.0").available)
            self.assertFalse(updates.check_for_update("1.0.0").available)

    def test_draft_prerelease_missing_asset_and_foreign_download_are_rejected(self):
        cases = []
        for field in ("draft", "prerelease"):
            payload = release_payload()
            payload[field] = True
            cases.append(payload)
        payload = release_payload()
        payload["tag_name"] = "v0.3.0-beta.1"
        cases.append(payload)
        payload = release_payload()
        payload["assets"].pop()
        cases.append(payload)
        payload = release_payload()
        payload["assets"][0]["browser_download_url"] = "https://example.invalid/untrusted.zip"
        cases.append(payload)
        for payload in cases:
            with self.subTest(payload=payload), self.assertRaises(updates.UpdateError):
                updates.parse_release(payload)

    def test_checksum_must_name_the_exact_archive_once(self):
        digest = "a" * 64
        self.assertEqual(digest, updates.checksum_for_archive(f"{digest}  {updates.ZIP_NAME}\n".encode()))
        self.assertEqual(digest, updates.checksum_for_archive(f"{digest} *{updates.ZIP_NAME}\n".encode()))
        for content in (b"invalid", (f"{digest}  other.zip").encode(), (f"{digest}  {updates.ZIP_NAME}\n" * 2).encode()):
            with self.subTest(content=content), self.assertRaises(updates.UpdateError):
                updates.checksum_for_archive(content)

    def test_corrupted_incomplete_and_oversize_downloads_are_not_accepted(self):
        release = updates.parse_release(release_payload(archive=b"expected"))
        for received in (b"modified", b"short", b"too many bytes"):
            with tempfile.TemporaryDirectory() as folder, patch.object(updates, "_request", return_value=io.BytesIO(received)):
                with self.subTest(received=received), self.assertRaises(updates.UpdateError):
                    updates._download_archive(release, Path(folder) / "test.zip", hashlib.sha256(b"expected").hexdigest(), None, None)

    def test_cancel_prevents_any_network_request(self):
        event = threading.Event()
        event.set()
        with patch.object(updates, "_request") as request, self.assertRaises(updates.UpdateCancelled):
            updates.check_for_update(cancel_event=event)
        request.assert_not_called()

    def test_source_checkout_cannot_apply_updates(self):
        with patch.object(updates.sys, "frozen", False, create=True), self.assertRaisesRegex(updates.UpdateError, "código fuente"):
            updates._portable_root(Path(__file__).parent)


class ArchiveTests(unittest.TestCase):
    def test_rejects_traversal_absolute_drive_device_and_case_duplicate_before_writing(self):
        names = ["MiRealmBot/../outside.txt", "/MiRealmBot/outside.txt", "MiRealmBot/C:evil",
                 "MiRealmBot\\outside", "MiRealmBot/NUL.txt", "MiRealmBot/trailing. /x",
                 "Other/evil", "MiRealmBot/analysis/capture.jsonl"]
        for name in names:
            with self.subTest(name=name), tempfile.TemporaryDirectory() as folder:
                root = Path(folder)
                with zipfile.ZipFile(root / "test.zip", "w") as archive:
                    archive.writestr("MiRealmBot/allowed.txt", "first")
                    info = zipfile.ZipInfo(name)
                    # Windows ZipInfo normalizes its constructor argument, so
                    # preserve the raw spelling in this malformed ZIP fixture.
                    info.filename = name
                    archive.writestr(info, "bad")
                with self.assertRaises(updates.UpdateError):
                    updates.extract_archive(root / "test.zip", root / "extracted")
                self.assertFalse((root / "extracted").exists())
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            with zipfile.ZipFile(root / "test.zip", "w") as archive:
                archive.writestr("MiRealmBot/FILE.txt", "first")
                archive.writestr("MiRealmBot/file.TXT", "second")
            with self.assertRaisesRegex(updates.UpdateError, "duplicadas"):
                updates.extract_archive(root / "test.zip", root / "extracted")

    def test_rejects_symlink_and_total_expansion_limit(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            with zipfile.ZipFile(root / "link.zip", "w") as archive:
                link = zipfile.ZipInfo("MiRealmBot/link")
                link.external_attr = (0o120777 << 16)
                archive.writestr(link, "../outside")
            with self.assertRaises(updates.UpdateError):
                updates.extract_archive(root / "link.zip", root / "extracted")
            with zipfile.ZipFile(root / "large.zip", "w") as archive:
                archive.writestr("MiRealmBot/one", b"12345")
                archive.writestr("MiRealmBot/two", b"67890")
            with patch.object(updates, "MAX_EXTRACTED_BYTES", 9), self.assertRaisesRegex(updates.UpdateError, "expandido"):
                updates.extract_archive(root / "large.zip", root / "extracted")
            self.assertFalse((root / "extracted").exists())

    def test_extracts_binary_including_path_with_spaces_and_manifest_version(self):
        release = updates.parse_release(release_payload())
        with tempfile.TemporaryDirectory(prefix="MiRealmBot con espacios ") as folder:
            root = Path(folder)
            files = {"MiRealmBot.exe": b"synthetic exe", "_internal/python312.dll": b"dll",
                     "_internal/autonexus_agent.js": b"agent", "autonexus.json": b"{}",
                     "BUILD-MANIFEST.json": json.dumps({"application": "MiRealmBot", "version": release.version}).encode()}
            with zipfile.ZipFile(root / "test.zip", "w") as archive:
                for name, content in files.items():
                    archive.writestr("MiRealmBot/" + name, content)
            staged = updates.extract_archive(root / "test.zip", root / "extracted")
            updates.validate_staged(staged, release)
            self.assertEqual((staged / "MiRealmBot.exe").read_bytes(), b"synthetic exe")
            (staged / "BUILD-MANIFEST.json").write_text('{"application":"MiRealmBot","version":"0.4.0"}')
            with self.assertRaisesRegex(updates.UpdateError, "no coincide"):
                updates.validate_staged(staged, release)

    def test_modern_layout_requires_public_template_compatibility_and_declared_resources(self):
        release = updates.parse_release(release_payload("0.4.0"))
        with tempfile.TemporaryDirectory() as folder:
            staged = Path(folder)
            for relative, data in modern_bundle_files(release.version).items():
                target = staged / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(data)
            updates.validate_staged(staged, release)
            for relative in ("mirealmbot.example.json", "autonexus.json", "_internal/bot_agent.js",
                             "_internal/autonexus_agent.js", "_internal/debuffs_agent.js"):
                with self.subTest(missing=relative):
                    target = staged / relative
                    data = target.read_bytes()
                    target.unlink()
                    with self.assertRaises(updates.UpdateError):
                        updates.validate_staged(staged, release)
                    target.write_bytes(data)
            manifest_path = staged / "BUILD-MANIFEST.json"
            original_manifest = manifest_path.read_bytes()
            for resources in (None, [], ["../outside"], ["/outside"], ["C:outside"],
                              ["invalid\\windows"], [True]):
                with self.subTest(resources=resources):
                    manifest = json.loads(original_manifest)
                    manifest["resource_files"] = resources
                    manifest_path.write_text(json.dumps(manifest))
                    with self.assertRaises(updates.UpdateError):
                        updates.validate_staged(staged, release)
            manifest_path.write_bytes(original_manifest)
            (staged / "mirealmbot.json").write_text("personal")
            with self.assertRaisesRegex(updates.UpdateError, "personal"):
                updates.validate_staged(staged, release)
            (staged / "mirealmbot.json").unlink()
            (staged / "autonexus.json").write_text("personal")
            with self.assertRaisesRegex(updates.UpdateError, "compatibilidad"):
                updates.validate_staged(staged, release)

    def test_future_release_cannot_silently_use_an_undeclared_legacy_layout(self):
        release = updates.parse_release(release_payload("0.4.0"))
        with tempfile.TemporaryDirectory() as folder:
            staged = Path(folder)
            for relative, data in modern_bundle_files(release.version).items():
                target = staged / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(data)
            (staged / "BUILD-MANIFEST.json").write_text(json.dumps({
                "application": "MiRealmBot", "version": release.version}))
            with self.assertRaisesRegex(updates.UpdateError, "formato"):
                updates.validate_staged(staged, release)


class PreparationTests(unittest.TestCase):
    # These tests simulate an installation and its next release. Neither side
    # depends on the version of the checkout running this test suite.
    INSTALLED_VERSION = "1.0.0"
    RELEASE_VERSION = "1.1.0"

    def setUp(self):
        self.enterContext(patch.object(updates, "VERSION", self.INSTALLED_VERSION))

    def make_bundle(self):
        stream = io.BytesIO()
        with zipfile.ZipFile(stream, "w") as archive:
            files = modern_bundle_files(self.RELEASE_VERSION)
            for name, content in files.items():
                archive.writestr("MiRealmBot/" + name, content)
        data = stream.getvalue()
        checksum = (hashlib.sha256(data).hexdigest() + "  " + updates.ZIP_NAME + "\n").encode()
        payload = release_payload(self.RELEASE_VERSION, archive=data)
        payload["assets"][1]["size"] = len(checksum)
        return data, checksum, updates.parse_release(payload)

    def test_full_offline_prepare_preserves_installation_and_checks_entire_bundle(self):
        data, checksum, release = self.make_bundle()
        with tempfile.TemporaryDirectory() as folder:
            app = Path(folder) / "MiRealmBot"
            app.mkdir()
            (app / "autonexus.json").write_bytes(b"personal config")
            progress = []
            with patch.object(updates, "_portable_root", return_value=app), \
                    patch.object(updates, "_read_small", return_value=checksum), \
                    patch.object(updates, "_request", return_value=io.BytesIO(data)):
                prepared = updates.prepare_update(release, app, progress=lambda *value: progress.append(value))
            self.assertEqual((app / "autonexus.json").read_bytes(), b"personal config")
            self.assertTrue((prepared.staged_root / "MiRealmBot.exe").is_file())
            self.assertEqual(prepared.workspace.parent, app.parent)
            self.assertTrue(progress)

    def test_mismatching_github_and_checksum_hashes_prevent_zip_download(self):
        _, _, release = self.make_bundle()
        checksum = ("a" * 64 + "  " + updates.ZIP_NAME + "\n").encode()
        with tempfile.TemporaryDirectory() as folder, \
                patch.object(updates, "_portable_root", return_value=Path(folder) / "MiRealmBot"), \
                patch.object(updates, "_read_small", return_value=checksum), \
                patch.object(updates, "_request") as request, self.assertRaisesRegex(updates.UpdateError, "no coincide"):
            updates.prepare_update(release)
        request.assert_not_called()

    @unittest.skipUnless(sys.platform == "win32", "The update launcher uses Windows PowerShell")
    def test_failed_worker_launch_allows_explicit_retry(self):
        data, _, release = self.make_bundle()
        with tempfile.TemporaryDirectory() as folder:
            app = Path(folder) / "MiRealmBot"
            app.mkdir()
            workspace = app.parent / ".MiRealmBot-update-retry"
            workspace.mkdir()
            archive = workspace / updates.ZIP_NAME
            archive.write_bytes(data)
            staged = updates.extract_archive(archive, workspace / "staged")
            prepared = updates.PreparedUpdate(workspace, staged, app, release)
            with patch.object(updates, "_portable_root", return_value=app), \
                    patch.object(updates.subprocess, "Popen", side_effect=OSError("synthetic launch failure")):
                with self.assertRaisesRegex(updates.UpdateError, "iniciar"):
                    updates.launch_update(prepared)
            self.assertFalse((workspace / "plan.json").exists())
            self.assertFalse((workspace / "updater_worker.ps1").exists())
            with patch.object(updates, "_portable_root", return_value=app), \
                    patch.object(updates.subprocess, "Popen") as launcher:
                self.assertEqual(updates.launch_update(prepared), workspace / "plan.json")
            launcher.assert_called_once()


@unittest.skipUnless(sys.platform == "win32", "The real update worker uses Windows PowerShell")
class WindowsTransactionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="MiRealmBot updater QA ")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.app = self.root / "MiRealmBot"
        self.app.mkdir()
        (self.app / "MiRealmBot.exe").write_bytes(b"old executable")
        (self.app / "_internal").mkdir()
        (self.app / "_internal/obsolete.dll").write_text("old library")
        (self.app / "autonexus.json").write_text('{"threshold":0.55,"personal":true}')
        self.canonical_bytes = b'\xef\xbb\xbf{\r\n  "threshold":0.42,"autonexus_enabled":false,"personal":"canonical"\r\n}\r\n'
        (self.app / "mirealmbot.json").write_bytes(self.canonical_bytes)
        (self.app / "mirealmbot.example.json").write_text("old public template")
        (self.app / "analysis").mkdir()
        (self.app / "analysis/personal.jsonl").write_text("private log")
        (self.app / "my-notes.txt").write_text("personal notes")
        self.workspace = self.root / ".MiRealmBot-update-test"
        self.staged = self.workspace / "staged/MiRealmBot"
        self.staged.mkdir(parents=True)
        for relative, data in modern_bundle_files("0.4.0", executable=b"not a real executable").items():
            target = self.staged / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
        (self.staged / "_internal/current.dll").write_text("new library")
        self.plan = {"format": 1, "app_root": str(self.app), "workspace": str(self.workspace),
                     "staged_root": str(self.staged), "backup_root": str(self.root / "MiRealmBot.backup-test"),
                     "version": "0.4.0", "parent_pid": 0, "parent_started_ticks": 0, "restart": False}

    def run_worker(self, worker=None):
        plan_path = self.workspace / "plan.json"
        plan_path.write_text(json.dumps(self.plan), encoding="utf-8")
        worker = worker or Path(__file__).with_name("updater_worker.ps1")
        result = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                                 "-File", str(worker), "-Plan", str(plan_path)], capture_output=True, text=True, timeout=15)
        report = json.loads((self.workspace / "result.json").read_text(encoding="utf-8-sig"))
        return result.returncode, report

    def assert_original(self):
        self.assertEqual((self.app / "MiRealmBot.exe").read_bytes(), b"old executable")
        self.assertTrue((self.app / "_internal/obsolete.dll").is_file())
        self.assertEqual((self.app / "autonexus.json").read_text(), '{"threshold":0.55,"personal":true}')
        self.assertEqual((self.app / "mirealmbot.json").read_bytes(), self.canonical_bytes)
        self.assertEqual((self.app / "mirealmbot.example.json").read_text(), "old public template")
        self.assertEqual((self.app / "analysis/personal.jsonl").read_text(), "private log")

    def test_complete_replacement_preserves_config_logs_extras_and_whole_backup(self):
        code, report = self.run_worker()
        self.assertEqual(code, 0, report)
        self.assertTrue(report["ok"])
        self.assertFalse((self.app / "_internal/obsolete.dll").exists())
        self.assertTrue((self.app / "_internal/current.dll").exists())
        self.assertEqual((self.app / "autonexus.json").read_text(), '{"threshold":0.55,"personal":true}')
        self.assertEqual((self.app / "mirealmbot.json").read_bytes(), self.canonical_bytes)
        self.assertEqual((self.app / "mirealmbot.example.json").read_bytes(), b'{"threshold":0.9}')
        self.assertEqual(ensure_default_config(root=self.app).read_bytes(), self.canonical_bytes)
        self.assertEqual(list(self.app.glob("autonexus.json.migration.*.bak")), [])
        self.assertEqual((self.app / "analysis/personal.jsonl").read_text(), "private log")
        self.assertEqual((self.app / "my-notes.txt").read_text(), "personal notes")
        backup = Path(report["backup_root"])
        self.assertEqual((backup / "MiRealmBot.exe").read_bytes(), b"old executable")
        self.assertTrue((backup / "_internal/obsolete.dll").exists())
        self.assertEqual((backup / "mirealmbot.json").read_bytes(), self.canonical_bytes)
        self.assertEqual((backup / "autonexus.json").read_text(), '{"threshold":0.55,"personal":true}')
        self.assertEqual((backup / "mirealmbot.example.json").read_text(), "old public template")

    def test_actual_031_worker_upgrade_then_first_launch_migrates_legacy_bytes_once(self):
        # A 0.3.1 installation has only the older configuration name. The new
        # release must not preseed canonical settings and hide the user's data.
        (self.app / "mirealmbot.json").unlink()
        (self.app / "mirealmbot.example.json").unlink()
        legacy = b'\xef\xbb\xbf{\r\n  "threshold":0.61,"unknown":"keep exact bytes"\r\n}\r\n'
        (self.app / "autonexus.json").write_bytes(legacy)
        self.assertEqual(hashlib.sha256(LEGACY_WORKER_PATH.read_bytes()).hexdigest(), LEGACY_WORKER_SHA256)
        self.assertFalse((self.staged / "mirealmbot.json").exists())
        code, report = self.run_worker(LEGACY_WORKER_PATH)
        self.assertEqual(code, 0, report)
        self.assertFalse((self.app / "mirealmbot.json").exists())
        self.assertEqual((self.app / "mirealmbot.example.json").read_bytes(), b'{"threshold":0.9}')
        migrated = ensure_default_config(root=self.app)
        self.assertEqual(migrated.name, "mirealmbot.json")
        self.assertEqual(migrated.read_bytes(), legacy)
        self.assertEqual((self.app / "autonexus.json").read_bytes(), legacy)
        backups = list(self.app.glob("autonexus.json.migration.*.bak"))
        self.assertEqual(len(backups), 1)
        self.assertEqual(backups[0].read_bytes(), legacy)
        self.assertEqual(ensure_default_config(root=self.app), migrated)
        self.assertEqual(list(self.app.glob("autonexus.json.migration.*.bak")), backups)
        self.assertEqual((Path(report["backup_root"]) / "autonexus.json").read_bytes(), legacy)

    def test_failed_new_executable_rolls_back_original_tree(self):
        # Force a failure after both moves without launching any executable or
        # showing an OS error dialog. The worker's normal rollback runs in full.
        script = Path(__file__).with_name("updater_worker.ps1").read_text(encoding="utf-8")
        injected = self.workspace / "forced-start-failure.ps1"
        injected.write_text(script.replace("if ($planData.restart) {", "if ($true) { throw 'synthetic restart failure';", 1), encoding="utf-8")
        plan_path = self.workspace / "plan.json"
        plan_path.write_text(json.dumps(self.plan), encoding="utf-8")
        result = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                                 "-File", str(injected), "-Plan", str(plan_path)], capture_output=True, text=True, timeout=15)
        report = json.loads((self.workspace / "result.json").read_text(encoding="utf-8-sig"))
        self.assertEqual(result.returncode, 1, report)
        self.assertTrue(report["rollback"], report)
        self.assert_original()
        self.assertTrue((self.workspace / "failed-new-installation/MiRealmBot.exe").is_file())
        self.assertFalse(Path(self.plan["backup_root"]).exists())

    def test_wrong_process_identity_never_changes_files(self):
        self.plan.update(parent_pid=__import__("os").getpid(), parent_started_ticks=1)
        code, report = self.run_worker()
        self.assertEqual(code, 1)
        self.assertIn("identity changed", report["error"])
        self.assert_original()

    def test_bad_staging_path_does_not_mutate_installation(self):
        self.plan["staged_root"] = str(self.root / "outside")
        code, report = self.run_worker()
        self.assertEqual(code, 1)
        self.assertIn("paths", report["error"])
        self.assert_original()

    def test_another_open_executable_cannot_leave_half_replaced_files(self):
        import ctypes
        from ctypes import wintypes
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                                       wintypes.LPVOID, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
        kernel.CreateFileW.restype = wintypes.HANDLE
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel.CloseHandle.restype = wintypes.BOOL
        handle = kernel.CreateFileW(str(self.app / "MiRealmBot.exe"), 0x80000000, 1, None, 3, 0x80, None)
        self.assertNotEqual(handle, wintypes.HANDLE(-1).value)
        try:
            code, report = self.run_worker()
        finally:
            kernel.CloseHandle(handle)
        self.assertEqual(code, 1, report)
        self.assert_original()

    def test_real_parent_identity_late_writes_and_restart_from_path_with_spaces(self):
        # Compile a tiny local WinExe that only writes a marker, without opening
        # any game or GUI. This exercises Start-Process with the actual worker.
        compiler = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "Microsoft.NET/Framework64/v4.0.30319/csc.exe"
        if not compiler.is_file():
            self.skipTest("The optional Windows .NET C# compiler is unavailable")
        source = self.root / "RestartMarker.cs"
        source.write_text('using System; using System.IO; class Marker { static void Main() { '
                          'File.WriteAllText(Path.Combine(AppDomain.CurrentDomain.BaseDirectory, "restarted.txt"), "started"); } }', encoding="utf-8")
        built = subprocess.run([str(compiler), "/nologo", "/target:winexe", "/out:" + str(self.staged / "MiRealmBot.exe"), str(source)],
                               capture_output=True, text=True, timeout=15, creationflags=subprocess.CREATE_NO_WINDOW)
        self.assertEqual(built.returncode, 0, built.stdout + built.stderr)
        child_code = (
            "import json,os,sys; from pathlib import Path; from update_core import _current_process_ticks; "
            "print(json.dumps({'pid':os.getpid(),'ticks':str(_current_process_ticks())}),flush=True); sys.stdin.readline(); "
            "root=Path(sys.argv[1]); (root/'autonexus.json').write_text('late legacy config'); "
            "(root/'mirealmbot.json').write_text('late canonical config'); "
            "(root/'analysis'/'personal.jsonl').write_text('late log tail')"
        )
        parent = subprocess.Popen([sys.executable, "-B", "-c", child_code, str(self.app)],
                                  cwd=Path(__file__).parent, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                  stderr=subprocess.PIPE, text=True, creationflags=subprocess.CREATE_NO_WINDOW)
        self.addCleanup(lambda: parent.kill() if parent.poll() is None else None)
        parent_identity = json.loads(parent.stdout.readline())
        self.plan.update(parent_pid=parent_identity["pid"], parent_started_ticks=parent_identity["ticks"], restart=True)
        plan_path = self.workspace / "plan.json"
        plan_path.write_text(json.dumps(self.plan), encoding="utf-8")
        headless_worker = self.workspace / "headless-worker.ps1"
        headless_worker.write_text(Path(__file__).with_name("updater_worker.ps1").read_text(encoding="utf-8").replace(
            "if ($null -ne $planData -and $planData.restart) {", "if ($false) {"), encoding="utf-8")
        worker = subprocess.Popen(["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                                   "-File", str(headless_worker), "-Plan", str(plan_path)],
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                                  creationflags=subprocess.CREATE_NO_WINDOW)
        self.addCleanup(lambda: worker.kill() if worker.poll() is None else None)
        # Wait until this worker has the current parent handle open. No mutation
        # can occur yet: that parent remains blocked on its own stdin.
        import time
        time.sleep(0.8)
        report_path = self.workspace / "result.json"
        self.assertIsNone(worker.poll(), report_path.read_text(encoding="utf-8-sig") if report_path.is_file() else "")
        self.assertEqual((self.app / "MiRealmBot.exe").read_bytes(), b"old executable")
        parent.communicate("close\n", timeout=5)
        stdout, stderr = worker.communicate(timeout=15)
        report = json.loads((self.workspace / "result.json").read_text(encoding="utf-8-sig"))
        self.assertEqual(worker.returncode, 0, str(report) + stdout + stderr)
        self.assertTrue(report["ok"], report)
        self.assertEqual((self.app / "autonexus.json").read_text(), "late legacy config")
        self.assertEqual((self.app / "mirealmbot.json").read_text(), "late canonical config")
        self.assertEqual((self.app / "mirealmbot.example.json").read_bytes(), b'{"threshold":0.9}')
        self.assertEqual((self.app / "analysis/personal.jsonl").read_text(), "late log tail")
        for _ in range(20):
            if (self.app / "restarted.txt").is_file():
                break
            time.sleep(0.05)
        self.assertEqual((self.app / "restarted.txt").read_text(), "started")


if __name__ == "__main__":
    unittest.main()
