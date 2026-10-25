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

from bot_runtime import (SUPPORTED_GAMEASSEMBLY_SHA256, config_from_mapping,
                         config_to_mapping, load_config, resolve_game_nexus_key,
                         run_runtime, save_config, RuntimeConfig)
from runtime_logs import LOG_BASENAME
from runtime_control import RuntimeControl
from windows_nexus import NexusRequestResult


class RuntimeControlTests(unittest.TestCase):
    def test_pending_snapshots_coalesce_and_each_revision_is_consumed_once(self):
        control = RuntimeControl()
        self.assertIsNone(control.take_latest())
        configs = [config_from_mapping({"threshold": threshold}) for threshold in (0.2, 0.4, 0.6)]
        self.assertEqual([control.request(config) for config in configs], [1, 2, 3])
        self.assertEqual(control.take_latest(), (3, configs[-1]))
        self.assertIsNone(control.take_latest())
        self.assertEqual(control.request(configs[0]), 4)
        self.assertEqual(control.take_latest(), (4, configs[0]))

    def test_concurrent_requests_have_one_consistent_latest_revision(self):
        control = RuntimeControl()
        barrier = threading.Barrier(12)
        requested = {}
        requested_lock = threading.Lock()

        def producer(index):
            config = config_from_mapping({"threshold": 0.2 + index / 100})
            barrier.wait(timeout=5)
            revision = control.request(config)
            with requested_lock:
                requested[revision] = config

        threads = [threading.Thread(target=producer, args=(index,)) for index in range(12)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=5)
            self.assertFalse(thread.is_alive())
        self.assertEqual(sorted(requested), list(range(1, 13)))
        self.assertEqual(control.take_latest(), (12, requested[12]))
        self.assertIsNone(control.take_latest())


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
            with mock.patch("bot_runtime.os.replace", side_effect=OSError("file locked")):
                with self.assertRaises(OSError):
                    save_config(path, config_from_mapping({"threshold": 0.5}))
            self.assertEqual(path.read_bytes(), original)
            self.assertEqual(next(path.parent.glob("*.bak")).read_bytes(), original)
            self.assertEqual(list(path.parent.glob("*.tmp")), [])

    def test_repeated_live_saves_keep_only_one_backup_of_the_previous_settings(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "mirealmbot.json"
            path.write_bytes(b'{"future_feature": {"value": 12}, "threshold": 0.3}\n')
            for threshold in (0.4, 0.5, 0.6, 0.7):
                previous = path.read_bytes()
                backup = save_config(path, config_from_mapping({"threshold": threshold}))
                self.assertEqual(backup, path.with_name("mirealmbot.json.bak"))
                self.assertEqual(backup.read_bytes(), previous)
                self.assertEqual(list(path.parent.glob("*.bak")), [backup])
                self.assertEqual(json.loads(path.read_text(encoding="utf-8"))["future_feature"], {"value": 12})

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

    def test_autonexus_toggle_preserves_legacy_defaults_and_independent_modules(self):
        legacy = RuntimeConfig(0.3, "R", ("Nexus",), SUPPORTED_GAMEASSEMBLY_SHA256)
        self.assertIs(legacy.autonexus_enabled, True)
        self.assertIs(config_from_mapping({}).autonexus_enabled, True)
        for nexus, aim in ((False, False), (False, True), (True, False), (True, True)):
            with self.subTest(nexus=nexus, aim=aim):
                config = config_from_mapping({"autonexus_enabled": nexus,
                                              "autoaim": {"enabled": aim}})
                self.assertIs(config.autonexus_enabled, nexus)
                self.assertIs(config.autoaim.enabled, aim)
                self.assertEqual(config_from_mapping(config_to_mapping(config)), config)
        for value in (None, 0, 1, "false", [], {}):
            with self.subTest(value=value), self.assertRaises(ValueError):
                config_from_mapping({"autonexus_enabled": value})

    def test_disabled_autonexus_does_not_wait_for_or_resolve_a_game_key(self):
        config = config_from_mapping({"autonexus_enabled": False, "detect_game_keys": True})
        for value in (None, {}, {"game_bindings": {"nexus": {"unity_code": 306, "age_ms": 0}}}):
            self.assertEqual(resolve_game_nexus_key(value, config), (None, "disabled"))


class WorkerLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.exe = self.root / "RotMG Exalt.exe"
        self.module = self.root / "GameAssembly.dll"
        self.exe.write_bytes(b"fake exe")
        self.module.write_bytes(b"fake module")
        for name in ("bot_agent.js", "game_bindings_agent.js", "autoaim_agent.js", "debuffs_agent.js"):
            (self.root / name).write_text("// not executable", encoding="utf-8")
        self.calls = []
        self.updates = []
        self.stop = threading.Event()
        self.fail_cleanup = False
        self.stop_at = None
        self.bad_callback = False
        self.agent_options = None
        self.nexus_binding = {"unity_code": 114, "age_ms": 0}
        self.configure_options = []
        self.configure_result = {"configured": True}
        self.configure_error = None
        self.poll_count = 0
        self.on_poll = None
        self.on_record = None
        self.stop_after_telemetry = True
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
                owner.agent_options = options
                return {"pid": 42, "module_path": str(owner.module), "bindings": {}}

            def poll(self):
                owner.calls.append("agent.poll")
                owner.poll_count += 1
                if owner.stop_at == "poll":
                    owner.stop.set()
                payload = {"sequence": owner.poll_count, "age_ms": 0, "map_id": "Realm:1", "map_name": "Realm",
                        "game_bindings": {"nexus": owner.nexus_binding},
                        "player_hp": 20, "max_hp": 100, "gate_a": None, "gate_b": None,
                        "tracked_hp": None}
                if owner.on_poll is not None:
                    owner.on_poll(payload)
                return payload

            def configure(self, options):
                owner.calls.append("agent.configure")
                owner.configure_options.append(options)
                if owner.stop_at == "configure":
                    owner.stop.set()
                if owner.configure_error is not None:
                    raise owner.configure_error
                return owner.configure_result

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
                owner.calls.append("actuator.create")

            def request(self, pid, key):
                owner.calls.append("key.request")
                return NexusRequestResult(pid, key)

        self.patchers = [mock.patch.dict("sys.modules", {"frida": SimpleNamespace(get_local_device=lambda: Device())}),
                         mock.patch("bot_runtime.sys.platform", "win32"),
                         mock.patch("bot_runtime.ROOT", self.root),
                         mock.patch("bot_runtime.resource_path", side_effect=lambda name: self.root / name),
                         mock.patch("bot_runtime.EXPECTED_EXE", self.exe),
                         mock.patch("bot_runtime.ProcessIdentity", Identity),
                         mock.patch("bot_runtime.verify_module_hash", return_value=SUPPORTED_GAMEASSEMBLY_SHA256),
                         mock.patch("bot_runtime.Win32InputBackend", InputBackend),
                         mock.patch("bot_runtime.WindowsNexus", Actuator)]
        for patcher in self.patchers:
            patcher.start()
            self.addCleanup(patcher.stop)

    def update(self, event):
        self.updates.append(event)
        if self.on_record is not None:
            self.on_record(event)
        if event["event"] == "telemetry" and self.stop_after_telemetry:
            self.stop.set()
            if self.bad_callback:
                raise RuntimeError("broken widget")

    def run_worker(self, *, active=False, config=None, control=None):
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            return run_runtime(config_from_mapping({}) if config is None else config, active=active,
                               stop_event=self.stop, on_update=self.update, control=control)

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

    def test_autoaim_alone_never_enables_autonexus_or_its_prediction(self):
        config = config_from_mapping({"autonexus_enabled": False, "prediction_enabled": True,
                                      "autoaim": {"enabled": True}})
        for binding in (None, {"unity_code": 306, "age_ms": 0}):
            with self.subTest(binding=binding):
                self.calls.clear()
                self.updates.clear()
                self.stop.clear()
                self.nexus_binding = binding
                self.assertEqual(self.run_worker(active=True, config=config), 0)
                self.assertNotIn("key.request", self.calls)
                self.assertIs(self.agent_options["prediction_enabled"], False)
                self.assertIs(self.agent_options["autoaim"]["active"], True)
                telemetry = next(event for event in self.updates if event["event"] == "telemetry")
                self.assertEqual(telemetry["decision"]["action"], "none")
                self.assertEqual(telemetry["decision"]["reason"], "AutoNexus is disabled")
                self.assertEqual(telemetry["nexus_binding_status"], "disabled")
                self.assertIs(telemetry["prediction"]["enabled"], False)

    def test_no_modules_is_saveable_but_rejected_before_game_discovery(self):
        config = config_from_mapping({"autonexus_enabled": False, "autoaim": {"enabled": False}})
        path = self.root / "all-disabled.json"
        save_config(path, config)
        self.assertEqual(load_config(path), config)
        self.assertEqual(self.run_worker(active=True, config=config), 1)
        self.assertNotIn("enumerate", self.calls)
        self.assertNotIn("session.attach", self.calls)
        error = next(event for event in self.updates if event["event"] == "error")
        self.assertIn("Activa al menos un módulo", error["error"])

    def test_initial_configuration_is_acknowledged_before_running(self):
        self.assertEqual(self.run_worker(), 0)
        initial = next(event for event in self.updates if event["event"] == "config_applied")
        running = next(event for event in self.updates if event.get("status") == "running")
        self.assertEqual(initial["revision"], 0)
        self.assertEqual(initial["configuration"], config_to_mapping(config_from_mapping({})))
        self.assertLess(self.updates.index(initial), self.updates.index(running))

    def test_live_threshold_change_applies_between_polls_without_rearming_escape(self):
        control = RuntimeControl()
        changed = config_from_mapping({"threshold": 0.9, "prediction_enabled": True})
        self.stop_after_telemetry = False

        def poll(payload):
            if self.poll_count == 1:
                control.request(changed)
            elif self.poll_count == 3:
                self.stop.set()

        self.on_poll = poll
        self.assertEqual(self.run_worker(active=True, control=control), 0)
        self.assertEqual(self.calls.count("key.request"), 1)
        self.assertEqual(self.calls.count("session.attach"), 1)
        self.assertEqual(self.calls.count("agent.start"), 1)
        self.assertEqual(len(self.configure_options), 1)
        self.assertIs(self.configure_options[0]["prediction_enabled"], True)
        applied = [event for event in self.updates if event["event"] == "config_applied"]
        self.assertEqual([event["revision"] for event in applied], [0, 1])
        self.assertEqual(applied[1]["configuration"], config_to_mapping(changed))
        configure_index = self.calls.index("agent.configure")
        self.assertEqual(self.calls[configure_index + 1], "agent.poll")
        self.assertEqual(self.calls[-4:], ["agent.stop", "script.unload", "session.detach", "identity.close"])

    def test_enabling_autonexus_during_autoaim_creates_input_only_after_ack(self):
        control = RuntimeControl()
        control.request(config_from_mapping({"autoaim": {"enabled": True}}))
        initial = config_from_mapping({"autonexus_enabled": False, "autoaim": {"enabled": True}})
        self.assertEqual(self.run_worker(active=True, config=initial, control=control), 0)
        self.assertEqual(self.calls.count("actuator.create"), 1)
        self.assertLess(self.calls.index("agent.configure"), self.calls.index("actuator.create"))
        self.assertLess(self.calls.index("actuator.create"), self.calls.index("key.request"))
        self.assertIs(self.agent_options["autoaim"]["active"], True)
        self.assertIs(self.configure_options[0]["autoaim"]["active"], True)

    def test_disabling_autonexus_live_prevents_the_next_low_health_request(self):
        control = RuntimeControl()
        initial = config_from_mapping({"threshold": 0.1, "autoaim": {"enabled": True}})
        changed = config_from_mapping({"autonexus_enabled": False, "threshold": 0.1,
                                       "prediction_enabled": True, "autoaim": {"enabled": True}})
        self.stop_after_telemetry = False

        def poll(payload):
            if self.poll_count == 1:
                control.request(changed)
            elif self.poll_count == 2:
                payload["player_hp"] = 1
            else:
                self.stop.set()

        self.on_poll = poll
        self.assertEqual(self.run_worker(active=True, config=initial, control=control), 0)
        self.assertNotIn("key.request", self.calls)
        self.assertIs(self.configure_options[0]["prediction_enabled"], False)
        self.assertIs(self.configure_options[0]["autoaim"]["active"], True)

    def test_disabling_all_modules_acknowledges_then_stops_without_another_poll(self):
        control = RuntimeControl()
        disabled = config_from_mapping({"autonexus_enabled": False})
        control.request(disabled)
        self.assertEqual(self.run_worker(active=True, control=control), 0)
        self.assertNotIn("agent.poll", self.calls)
        self.assertNotIn("agent.configure", self.calls)
        applied = [event for event in self.updates if event["event"] == "config_applied"]
        self.assertEqual(applied[-1]["revision"], 1)
        self.assertEqual(applied[-1]["configuration"], config_to_mapping(disabled))
        stopping = next(event for event in self.updates if event["event"] == "stop_requested")
        self.assertEqual(stopping["reason"], "no_enabled_modules")
        self.assertLess(self.updates.index(applied[-1]), self.updates.index(stopping))
        self.assertIs(self.updates[-1]["cleanup_complete"], True)

    def test_live_map_policy_changes_are_rejected_before_touching_native_settings(self):
        control = RuntimeControl()
        control.request(config_from_mapping({"excluded_maps": ["Nexus", "Vault"]}))
        self.assertEqual(self.run_worker(control=control), 0)
        self.assertNotIn("agent.configure", self.calls)
        error = next(event for event in self.updates if event["event"] == "config_error")
        self.assertEqual(error["revision"], 1)
        self.assertEqual(error["reason"], "invalid_configuration")
        self.assertEqual([event["revision"] for event in self.updates if event["event"] == "config_applied"], [0])
        self.assertIn("agent.poll", self.calls)

    def test_native_configuration_failure_or_missing_ack_stops_and_cleans_up(self):
        for result, error in ((None, RuntimeError("native partially changed")),
                              (None, None), ({"configured": 1}, None)):
            with self.subTest(result=result, error=error):
                self.calls.clear()
                self.updates.clear()
                self.stop.clear()
                self.configure_error = error
                self.configure_result = result
                control = RuntimeControl()
                control.request(config_from_mapping({"threshold": 0.7}))
                self.assertEqual(self.run_worker(active=True, control=control), 1)
                self.assertNotIn("agent.poll", self.calls)
                config_error = next(event for event in self.updates if event["event"] == "config_error")
                self.assertEqual(config_error["reason"], "configure_failed")
                self.assertEqual([event["revision"] for event in self.updates if event["event"] == "config_applied"], [0])
                self.assertEqual(self.calls[-4:], ["agent.stop", "script.unload", "session.detach", "identity.close"])

    def test_stop_during_configuration_acknowledges_but_cannot_poll_or_act(self):
        self.stop_at = "configure"
        control = RuntimeControl()
        control.request(config_from_mapping({"threshold": 0.7}))
        self.assertEqual(self.run_worker(active=True, control=control), 0)
        self.assertNotIn("agent.poll", self.calls)
        self.assertNotIn("key.request", self.calls)
        self.assertEqual([event["revision"] for event in self.updates if event["event"] == "config_applied"], [0, 1])
        self.assertIs(self.updates[-1]["cleanup_complete"], True)

    def test_prediction_toggle_resets_pending_damage_but_preserves_transport_cursors(self):
        control = RuntimeControl()
        initial = config_from_mapping({"threshold": 0.1, "prediction_enabled": True})
        without_prediction = config_from_mapping({"threshold": 0.1, "prediction_enabled": False})
        self.stop_after_telemetry = False

        def poll(payload):
            payload["player_hp"] = 100
            if self.poll_count in (1, 3):
                payload["events"] = [{"event_id": 1 if self.poll_count == 1 else 2,
                                      "kind": "damage", "age_ms": 0,
                                      "frame": {key: payload[key] for key in (
                                          "map_id", "map_name", "player_hp", "max_hp",
                                          "gate_a", "gate_b", "tracked_hp")},
                                      "amount": 25 if self.poll_count == 1 else 10,
                                      "cause": "projectile"}]
            if self.poll_count == 1:
                control.request(without_prediction)
            elif self.poll_count == 2:
                control.request(initial)
            elif self.poll_count == 4:
                self.stop.set()

        self.on_poll = poll
        self.assertEqual(self.run_worker(active=True, config=initial, control=control), 0)
        states = [event["prediction"] for event in self.updates if event["event"] == "prediction_state"]
        self.assertEqual([(state["last_event_id"], state["tracked_hp"]) for state in states], [(1, 75), (2, 90)])
        self.assertEqual([options["prediction_enabled"] for options in self.configure_options], [False, True])
        self.assertEqual([event["revision"] for event in self.updates if event["event"] == "config_applied"], [0, 1, 2])
        self.assertNotIn("key.request", self.calls)

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

    def test_log_open_failure_warns_once_and_still_delivers_active_protection(self):
        with mock.patch("bot_runtime.BoundedRuntimeLog", side_effect=OSError("folder read only")):
            self.assertEqual(self.run_worker(active=True), 0)
        self.assertIn("key.request", self.calls)
        warnings = [event for event in self.updates if event["event"] == "logging_warning"]
        self.assertEqual(len(warnings), 1)
        self.assertIs(warnings[0]["logging_enabled"], False)
        self.assertEqual(warnings[0]["reason"], "logging_disabled")
        self.assertEqual(self.updates[-1]["status"], "finished")
        self.assertIs(self.updates[-1]["cleanup_complete"], True)

    def test_log_write_failure_disables_only_logging_and_does_not_skip_cleanup(self):
        logger = mock.Mock(path=self.root / "analysis" / f"{LOG_BASENAME}.jsonl",
                           legacy_files_retained=0)
        logger.record.side_effect = OSError("disk full")
        with mock.patch("bot_runtime.BoundedRuntimeLog", return_value=logger):
            self.assertEqual(self.run_worker(active=True), 0)
        self.assertIn("key.request", self.calls)
        self.assertEqual(logger.record.call_count, 1)
        logger.close.assert_called_once()
        warnings = [event for event in self.updates if event["event"] == "logging_warning"]
        self.assertEqual(len(warnings), 1)
        self.assertIn("disk full", warnings[0]["error"])
        self.assertEqual(self.calls[-4:], ["agent.stop", "script.unload", "session.detach", "identity.close"])

    def test_log_close_failure_does_not_turn_successful_cleanup_into_session_failure(self):
        logger = mock.Mock(path=self.root / "analysis" / f"{LOG_BASENAME}.jsonl",
                           legacy_files_retained=0)
        logger.close.side_effect = OSError("close failed")
        with mock.patch("bot_runtime.BoundedRuntimeLog", return_value=logger):
            self.assertEqual(self.run_worker(), 0)
        warnings = [event for event in self.updates if event["event"] == "logging_warning"]
        self.assertEqual(len(warnings), 1)
        self.assertIs(self.updates[-1]["cleanup_complete"], True)
        self.assertEqual(self.updates[-1]["code"], 0)

    def test_legacy_cleanup_pending_is_visible_and_logging_remains_enabled(self):
        logger = mock.Mock(path=self.root / "analysis" / f"{LOG_BASENAME}.jsonl",
                           legacy_files_retained=2)
        with mock.patch("bot_runtime.BoundedRuntimeLog", return_value=logger):
            self.assertEqual(self.run_worker(), 0)
        warnings = [event for event in self.updates if event["event"] == "logging_warning"]
        self.assertEqual(len(warnings), 1)
        self.assertEqual(warnings[0]["reason"], "legacy_cleanup_pending")
        self.assertIs(warnings[0]["logging_enabled"], True)
        self.assertEqual(warnings[0]["retained_files"], 2)
        self.assertGreater(logger.record.call_count, 1)


if __name__ == "__main__":
    unittest.main()
