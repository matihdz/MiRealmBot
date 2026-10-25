"""Public worker lifecycle and config persistence, with no game attachment."""
import contextlib
import io
import json
from pathlib import Path
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest import mock

from live_autonexus import (SUPPORTED_GAMEASSEMBLY_SHA256, config_from_mapping,
                            config_to_mapping, load_config, run_runtime, save_config)
from windows_nexus import NexusRequestResult


class ConfigPersistenceTests(unittest.TestCase):
    def test_validation_happens_before_writing_and_preserves_original_bytes(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "autonexus.json"
            original = b'{"future_feature": {"value": 12}, "threshold": 0.3}\n'
            path.write_bytes(original)
            invalid = config_from_mapping({})
            object.__setattr__(invalid, "threshold", 1.0)
            with self.assertRaises(ValueError):
                save_config(path, invalid)
            self.assertEqual(path.read_bytes(), original)
            self.assertEqual(list(path.parent.glob("*.bak")), [])

    def test_save_retains_unknown_keys_backs_up_exact_bytes_and_is_idempotent(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "autonexus.json"
            original = b'\xef\xbb\xbf{"future_feature": {"names": ["A", "B"]}}\r\n'
            path.write_bytes(original)
            config = config_from_mapping({"nexus_key": "control", "threshold": 0.5,
                                          "prediction_enabled": True})
            backup = save_config(path, config)
            self.assertEqual(backup.read_bytes(), original)
            result = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(result["future_feature"], {"names": ["A", "B"]})
            self.assertEqual(result["nexus_key"], "CTRL")
            self.assertEqual(load_config(path), config)
            self.assertIsNone(save_config(path, config))
            self.assertEqual(len(list(path.parent.glob("*.bak"))), 1)

    def test_atomic_replace_failure_leaves_original_and_backup_available(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "autonexus.json"
            original = b'{"threshold": 0.3}'
            path.write_bytes(original)
            with mock.patch("live_autonexus.os.replace", side_effect=OSError("file locked")):
                with self.assertRaises(OSError):
                    save_config(path, config_from_mapping({"threshold": 0.5}))
            self.assertEqual(path.read_bytes(), original)
            self.assertEqual(next(path.parent.glob("*.bak")).read_bytes(), original)
            self.assertEqual(list(path.parent.glob("*.tmp")), [])

    def test_config_mapping_uses_existing_semantic_owners(self):
        for changes in ({"threshold": True}, {"threshold": float("nan")},
                        {"threshold": 0}, {"threshold": 1},
                        {"excluded_maps": [""]}, {"excluded_maps": [12]},
                        {"prediction_enabled": 1}, {"nexus_key": "ALT"}):
            with self.subTest(changes=changes), self.assertRaises((ValueError, TypeError)):
                config_from_mapping(changes)
        for value in (True, float("inf"), 10**400):
            with self.subTest(percent=value), self.assertRaises(ValueError):
                config_from_mapping({}, threshold_percent=value)
        supplied = {"threshold": 0.45, "nexus_key": "rctrl", "excluded_maps": ["Nexus", "Vault"],
                    "prediction_enabled": True, "use_state_filters": True}
        config = config_from_mapping(supplied)
        self.assertEqual(config_to_mapping(config)["nexus_key"], "RCTRL")
        self.assertEqual(config_from_mapping(config_to_mapping(config)), config)


class WorkerLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.exe = self.root / "RotMG Exalt.exe"
        self.module = self.root / "GameAssembly.dll"
        self.exe.write_bytes(b"fake exe")
        self.module.write_bytes(b"fake module")
        (self.root / "autonexus_agent.js").write_text("// not executable", encoding="utf-8")
        self.calls = []
        self.updates = []
        self.stop = threading.Event()
        self.fail_cleanup = False
        self.stop_at = None
        self.bad_callback = False
        owner = self

        class Identity:
            created_filetime = 123

            def __init__(self, pid, expected):
                owner.calls.append("identity.open")

            def assert_alive(self):
                pass

            def close(self):
                owner.calls.append("identity.close")

        class Script:
            exports_sync = property(lambda self: self)

            def load(self):
                owner.calls.append("script.load")
                if owner.stop_at == "load":
                    owner.stop.set()

            def start(self, options):
                owner.calls.append("agent.start")
                return {"pid": 42, "module_path": str(owner.module), "bindings": {}}

            def poll(self):
                owner.calls.append("agent.poll")
                if owner.stop_at == "poll":
                    owner.stop.set()
                return {"sequence": 1, "age_ms": 0, "map_id": "Realm:1", "map_name": "Realm",
                        "game_bindings": {"nexus": {"unity_code": 114, "age_ms": 0}},
                        "player_hp": 20, "max_hp": 100, "gate_a": None, "gate_b": None,
                        "tracked_hp": None}

            def stop(self):
                owner.calls.append("agent.stop")
                if owner.fail_cleanup:
                    raise RuntimeError("stop failed")

            def unload(self):
                owner.calls.append("script.unload")

        class Session:
            def create_script(self, *args, **kwargs):
                owner.calls.append("script.create")
                if owner.stop_at == "create":
                    owner.stop.set()
                return Script()

            def detach(self):
                owner.calls.append("session.detach")

        class Device:
            def enumerate_processes(self):
                owner.calls.append("enumerate")
                return [SimpleNamespace(pid=42, name="RotMG Exalt.exe")]

            def attach(self, pid):
                owner.calls.append("session.attach")
                if owner.stop_at == "attach":
                    owner.stop.set()
                return Session()

        class InputBackend:
            def foreground_process_id(self):
                return 42

        class Actuator:
            def __init__(self, backend):
                pass

            def request(self, pid, key):
                owner.calls.append("key.request")
                return NexusRequestResult(pid, key)

        self.patchers = [mock.patch.dict("sys.modules", {"frida": SimpleNamespace(get_local_device=lambda: Device())}),
                         mock.patch("live_autonexus.sys.platform", "win32"),
                         mock.patch("live_autonexus.ROOT", self.root),
                         mock.patch("live_autonexus.EXPECTED_EXE", self.exe),
                         mock.patch("live_autonexus.ProcessIdentity", Identity),
                         mock.patch("live_autonexus.verify_module_hash", return_value=SUPPORTED_GAMEASSEMBLY_SHA256),
                         mock.patch("live_autonexus.Win32InputBackend", InputBackend),
                         mock.patch("live_autonexus.WindowsNexus", Actuator)]
        for patcher in self.patchers:
            patcher.start()
            self.addCleanup(patcher.stop)

    def update(self, event):
        self.updates.append(event)
        if event["event"] == "telemetry":
            self.stop.set()
            if self.bad_callback:
                raise RuntimeError("broken widget")

    def run_worker(self, *, active=False):
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            return run_runtime(config_from_mapping({}), active=active,
                               stop_event=self.stop, on_update=self.update)

    def test_cancel_before_discovery_and_at_installation_boundaries(self):
        for boundary, absent in (("preset", "enumerate"), ("attach", "script.create"),
                                 ("create", "script.load"), ("load", "agent.start")):
            with self.subTest(boundary=boundary):
                self.calls.clear()
                self.updates.clear()
                self.stop.clear()
                self.stop_at = boundary
                if boundary == "preset":
                    self.stop.set()
                self.assertEqual(self.run_worker(active=True), 0)
                self.assertNotIn(absent, self.calls)
                self.assertNotIn("key.request", self.calls)
                terminal = self.updates[-1]
                self.assertEqual((terminal["event"], terminal["status"]), ("lifecycle", "finished"))
                self.assertIs(terminal["cleanup_complete"], True)
                if boundary != "preset":
                    self.assertEqual(self.calls[-1], "identity.close")
                    self.assertIn("session.detach", self.calls)

    def test_stop_received_during_poll_prevents_first_action(self):
        self.stop_at = "poll"
        self.assertEqual(self.run_worker(active=True), 0)
        self.assertIn("agent.poll", self.calls)
        self.assertNotIn("key.request", self.calls)
        self.assertEqual(self.calls[-4:], ["agent.stop", "script.unload", "session.detach", "identity.close"])

    def test_structured_telemetry_and_terminal_cleanup_are_observable_without_print_parsing(self):
        self.assertEqual(self.run_worker(), 0)
        telemetry = next(event for event in self.updates if event["event"] == "telemetry")
        self.assertEqual(telemetry["frame"]["player_hp"], 20)
        self.assertEqual(telemetry["decision"]["action"], "escape")
        self.assertEqual(telemetry["delivery"]["status"], "observe_only")
        self.assertEqual(telemetry["foreground_pid"], 42)
        self.assertIs(telemetry["game_foreground"], True)
        self.assertEqual(telemetry["pid"], 42)
        self.assertTrue(Path(telemetry["log_path"]).is_file())
        statuses = [event["status"] for event in self.updates if event["event"] == "lifecycle"]
        self.assertEqual(statuses, ["connecting", "running", "stopping", "finished"])
        self.assertEqual(self.updates[-1]["code"], 0)
        self.assertNotIn("key.request", self.calls)

    def test_cleanup_failure_is_reported_and_does_not_skip_other_resources(self):
        self.fail_cleanup = True
        self.assertEqual(self.run_worker(), 1)
        terminal = self.updates[-1]
        self.assertFalse(terminal["cleanup_complete"])
        self.assertIn("agent.stop", terminal["errors"][0])
        self.assertEqual(self.calls[-4:], ["agent.stop", "script.unload", "session.detach", "identity.close"])

    def test_callback_failure_cannot_skip_cleanup(self):
        self.bad_callback = True
        self.assertEqual(self.run_worker(), 1)
        self.assertEqual(self.calls[-4:], ["agent.stop", "script.unload", "session.detach", "identity.close"])


if __name__ == "__main__":
    unittest.main()
