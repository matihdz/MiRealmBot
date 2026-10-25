import contextlib
import hashlib
import io
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

from autonexus_controller import Controller, HealthFrame
from autonexus_core import Action, AutoNexus, Decision
from bot_runtime import (MAX_AGE_MS, SUPPORTED_GAMEASSEMBLY_SHA256,
                            TelemetryConsumer, cleanup_runtime, deliver,
                            load_config, main, parse_poll, verify_module_hash,
                            verify_start, _console_health)
from windows_nexus import NexusInputBlocked, NexusInputError, NexusRequestResult


def payload(sequence=1, age_ms=0, **changes):
    result = {"sequence": sequence, "age_ms": age_ms, "map_id": "map:1",
              "map_name": "Realm", "player_hp": 800, "max_hp": 1000,
              "gate_a": False, "gate_b": False, "tracked_hp": None}
    result.update(changes)
    return result


class FakeActuator:
    def __init__(self, error=None):
        self.calls = []
        self.error = error

    def request(self, pid, key):
        self.calls.append((pid, key))
        if self.error is not None:
            raise self.error
        return NexusRequestResult(pid, key)


class BotRuntimeTests(unittest.TestCase):
    def test_parse_freshness_boundaries_and_no_invented_observations(self):
        fresh = parse_poll(payload(age_ms=MAX_AGE_MS), -1)
        self.assertEqual(fresh.status, "fresh")
        self.assertEqual(fresh.frame.player_hp, 800)
        stale = parse_poll(payload(age_ms=MAX_AGE_MS + 0.01), -1)
        self.assertEqual(stale.status, "stale")
        self.assertIsNone(stale.frame.player_hp)
        self.assertIsNone(stale.frame.map_id)
        self.assertIsNone(stale.frame.gate_a)
        absent = parse_poll(None, -1)
        self.assertEqual(absent.status, "missing")
        self.assertIsNone(absent.frame.map_name)
        missing_fields = parse_poll({"sequence": 1, "age_ms": 0}, -1)
        self.assertIsNone(missing_fields.frame.gate_b)
        self.assertIsNone(missing_fields.frame.tracked_hp)
        duplicate = parse_poll(payload(sequence=1), 1)
        self.assertEqual(duplicate.status, "duplicate")
        self.assertIsNone(duplicate.frame)
        self.assertEqual(parse_poll(payload(sequence=1, age_ms=251), 1).status, "stale")

    def test_transport_malformed_sequence_and_age_are_rejected(self):
        for item in ([], True, payload(sequence=True), payload(sequence=-1),
                     payload(sequence=0.5), payload(age_ms=True), payload(age_ms=-1),
                     payload(age_ms=float("nan")), payload(age_ms=float("inf")),
                     payload(age_ms=10**400)):
            with self.subTest(item=item):
                with self.assertRaises(ValueError):
                    parse_poll(item, -1)
        with self.assertRaises(ValueError):
            parse_poll(payload(sequence=1), 2)

    def test_repeat_and_stale_do_not_rearm_or_resend_a_request(self):
        consumer = TelemetryConsumer(Controller(AutoNexus()))
        sample, decision = consumer.consume(payload(player_hp=299))
        self.assertEqual(decision.action, Action.ESCAPE)
        self.assertEqual(consumer.last_sequence, 1)
        sample, decision = consumer.consume(payload(player_hp=299))
        self.assertEqual(sample.status, "duplicate")
        self.assertIsNone(decision)
        sample, decision = consumer.consume(payload(age_ms=251, player_hp=299))
        self.assertIsNone(decision.player_hp)
        self.assertEqual(decision.action, Action.NONE)
        sample, decision = consumer.consume(payload(sequence=2, player_hp=200))
        self.assertEqual(decision.action, Action.NONE)
        consumer.consume(None)
        sample, decision = consumer.consume(payload(sequence=3, player_hp=200))
        self.assertEqual(decision.action, Action.NONE)
        sample, decision = consumer.consume(payload(sequence=4, player_hp=200, map_id="map:2"))
        self.assertEqual(decision.action, Action.ESCAPE)

    def test_malformed_health_is_owned_by_controller_and_invalidates_old_state(self):
        consumer = TelemetryConsumer(Controller(AutoNexus()))
        consumer.consume(payload())
        # The transport does not duplicate int32 or gate semantic validation.
        raw = payload(sequence=2, player_hp=True)
        self.assertIs(parse_poll(raw, 1).frame.player_hp, True)
        with self.assertRaises(ValueError):
            consumer.consume(raw)
        self.assertIsNone(consumer.controller.engine.evaluate().player_hp)
        self.assertEqual(consumer.last_sequence, 1)

    def test_observe_mode_and_delivery_acknowledgement_do_not_confirm_nexus(self):
        request = Decision(Action.ESCAPE, "test request")
        actor = FakeActuator()
        observed = deliver(request, active=False, pid=42, key="R", actuator=actor)
        self.assertEqual(observed["status"], "observe_only")
        self.assertFalse(observed["key_sent"])
        self.assertEqual(actor.calls, [])
        sent = deliver(request, active=True, pid=42, key="R", actuator=actor)
        self.assertEqual(sent["status"], "key_sent")
        self.assertTrue(sent["key_sent"])
        self.assertFalse(sent["nexus_confirmed"])
        self.assertEqual(actor.calls, [(42, "R")])

    def test_delivery_block_and_failure_are_visible_without_retrying(self):
        for error, status in ((NexusInputBlocked("foreground changed"), "blocked"),
                              (NexusInputError("SendInput failed"), "failed")):
            with self.subTest(status=status):
                actor = FakeActuator(error)
                consumer = TelemetryConsumer(Controller(AutoNexus()))
                _, first = consumer.consume(payload(player_hp=100))
                delivery = deliver(first, active=True, pid=42, key="R", actuator=actor)
                self.assertEqual(delivery["status"], status)
                self.assertFalse(delivery["key_sent"])
                self.assertFalse(delivery["nexus_confirmed"])
                self.assertIn("error", delivery)
                _, second = consumer.consume(payload(sequence=2, player_hp=100))
                deliver(second, active=True, pid=42, key="R", actuator=actor)
                self.assertEqual(actor.calls, [(42, "R")])

    def test_cleanup_attempts_all_owned_resources_after_hook_stop_failure(self):
        calls = []

        class FakeScript:
            @property
            def exports_sync(self):
                raise RuntimeError("script transport failed")

            def unload(self):
                calls.append("unload")
                raise RuntimeError("unload failed")

        class FakeSession:
            def detach(self):
                calls.append("detach")

        class FakeIdentity:
            def close(self):
                calls.append("close")

        errors = cleanup_runtime(FakeScript(), FakeSession(), FakeIdentity())
        self.assertEqual(calls, ["unload", "detach", "close"])
        self.assertEqual(len(errors), 2)
        self.assertIn("agent.stop", errors[0])
        self.assertIn("script.unload", errors[1])

    def test_runtime_identity_requires_pid_and_real_module_path(self):
        with tempfile.TemporaryDirectory() as directory:
            expected = Path(directory) / "GameAssembly.dll"
            expected.write_bytes(b"test module")
            verify_start({"pid": 42, "module_path": str(expected)}, 42, expected)
            with self.assertRaises(RuntimeError):
                verify_start({"pid": 43, "module_path": str(expected)}, 42, expected)
            foreign = Path(directory) / "foreign.dll"
            foreign.write_bytes(b"test module")
            with self.assertRaises(RuntimeError):
                verify_start({"pid": 42, "module_path": str(foreign)}, 42, expected)
            digest = hashlib.sha256(b"test module").hexdigest()
            self.assertEqual(verify_module_hash(expected, digest), digest)
            with self.assertRaises(RuntimeError):
                verify_module_hash(expected, "0" * 64)

    def test_config_defaults_percent_override_and_help_without_frida(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "autonexus.json"
            path.write_text("{}", encoding="utf-8")
            config = load_config(path)
            self.assertEqual(config.threshold, 0.30)
            self.assertEqual(config.nexus_key, "R")
            self.assertEqual(config.excluded_maps, ("Nexus",))
            self.assertEqual(config.gameassembly_sha256, SUPPORTED_GAMEASSEMBLY_SHA256)
            self.assertIs(config.use_state_filters, False)
            self.assertIs(config.prediction_enabled, False)
            path.write_text(json.dumps({"threshold": 0.4, "nexus_key": "q",
                                        "use_state_filters": True}), encoding="utf-8")
            config = load_config(path, threshold_percent=25.8)
            self.assertEqual(config.threshold, 0.258)
            self.assertEqual(config.nexus_key, "Q")
            self.assertIs(config.use_state_filters, True)
        with contextlib.redirect_stdout(io.StringIO()), self.assertRaises(SystemExit) as caught:
            main(["--help"])
        self.assertEqual(caught.exception.code, 0)

    def test_cli_migrates_only_when_the_default_config_is_selected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "selected.json"
            path.write_text('{"autonexus_enabled": false, "autoaim": {"enabled": true}}',
                            encoding="utf-8")
            with mock.patch("bot_runtime.ensure_default_config", return_value=path) as migrate, \
                    mock.patch("bot_runtime.run_runtime", return_value=0) as run, \
                    mock.patch("bot_runtime.sys.platform", "win32"):
                self.assertEqual(main(["--config", str(path), "--active"]), 0)
                migrate.assert_not_called()
                self.assertIs(run.call_args.args[0].autonexus_enabled, False)
                self.assertIs(run.call_args.kwargs["active"], True)
                self.assertEqual(main([]), 0)
                migrate.assert_called_once_with()
            with mock.patch("bot_runtime.ensure_default_config") as migrate, \
                    mock.patch("bot_runtime.run_runtime") as run, \
                    contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                main(["--config", str(path.with_name("missing.json"))])
            migrate.assert_not_called()
            run.assert_not_called()

    def test_runtime_rejects_non_boolean_state_policy_and_retains_console_partial_hp(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "autonexus.json"
            for value in (None, 0, "false", []):
                with self.subTest(value=value):
                    path.write_text(json.dumps({"use_state_filters": value}), encoding="utf-8")
                    with self.assertRaises(ValueError):
                        load_config(path)
        observed = HealthFrame(None, "Realm", 250, None, None, None, 240)
        self.assertEqual(_console_health(Decision(Action.NONE, "unknown map"), observed),
                         "HP 250/None, seguido 240")

    def test_host_config_uses_the_actuator_owner_for_control_key_normalization(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "autonexus.json"
            for supplied, expected in (("control", "CTRL"), ("LCTRL", "CTRL"),
                                       ("ctrl", "CTRL"), ("rctrl", "RCTRL"), ("r", "R")):
                with self.subTest(key=supplied):
                    path.write_text(json.dumps({"nexus_key": supplied}), encoding="utf-8")
                    self.assertEqual(load_config(path).nexus_key, expected)
            for invalid in (True, None, "SHIFT", "ALT", "CTRL+R", "ñ"):
                with self.subTest(invalid=invalid):
                    path.write_text(json.dumps({"nexus_key": invalid}), encoding="utf-8")
                    with self.assertRaises(ValueError):
                        load_config(path)

    def test_host_logs_policy_and_each_event_batch_without_actual_attach_or_input(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = root / "autonexus.json"
            config.write_text('{"use_state_filters": false, "prediction_enabled": true}', encoding="utf-8")
            module_path = root / "GameAssembly.dll"
            module_path.write_bytes(b"fake module")
            expected_exe = root / "RotMG Exalt.exe"
            expected_exe.write_bytes(b"fake exe")
            for name in ("bot_agent.js", "game_bindings_agent.js", "autoaim_agent.js", "debuffs_agent.js"):
                (root / name).write_text("// fake script; never evaluated", encoding="utf-8")
            calls = []

            class FakeIdentity:
                created_filetime = 123

                def __init__(self, pid, path):
                    self.pid = pid

                def assert_alive(self):
                    pass

                def close(self):
                    calls.append("identity.close")

            class FakeScript:
                def __init__(self):
                    self.poll_count = 0

                @property
                def exports_sync(self):
                    return self

                def load(self):
                    pass

                def start(self, options):
                    if (options.get("prediction_enabled") is not True or
                            options.get("detect_game_keys") is not True or
                            options.get("autoaim", {}).get("active") is not False or
                            options.get("autoaim", {}).get("enabled") is not False):
                        raise AssertionError(options)
                    return {"pid": 42, "module_path": str(module_path), "bindings": {"test": True}}

                def poll(self):
                    self.poll_count += 1
                    result = payload(sequence=self.poll_count, player_hp=250,
                                     gate_a=None, gate_b=None, diagnostics={"hit_count": 1},
                                     game_bindings={"nexus": {"unity_code": 114, "age_ms": 0}})
                    if self.poll_count == 2:
                        result["events"] = [{"event_id": 1, "kind": "damage", "age_ms": 0,
                                             "frame": {name: result[name] for name in (
                                                 "map_id", "map_name", "player_hp", "max_hp",
                                                 "gate_a", "gate_b", "tracked_hp")},
                                             "amount": 1, "cause": "projectile"}]
                    return result

                def stop(self):
                    calls.append("agent.stop")

                def unload(self):
                    calls.append("script.unload")

            class FakeSession:
                def create_script(self, *args, **kwargs):
                    return FakeScript()

                def detach(self):
                    calls.append("session.detach")

            class FakeDevice:
                def enumerate_processes(self):
                    return [SimpleNamespace(name="RotMG Exalt.exe", pid=42)]

                def attach(self, pid):
                    calls.append("fake.attach")
                    return FakeSession()

            output = io.StringIO()
            fake_frida = SimpleNamespace(get_local_device=lambda: FakeDevice())
            with mock.patch.dict("sys.modules", {"frida": fake_frida}), \
                    mock.patch("bot_runtime.sys.platform", "win32"), \
                    mock.patch("bot_runtime.ROOT", root), \
                    mock.patch("bot_runtime.resource_path", side_effect=lambda name: root / name), \
                    mock.patch("bot_runtime.EXPECTED_EXE", expected_exe), \
                    mock.patch("bot_runtime.ProcessIdentity", FakeIdentity), \
                    mock.patch("bot_runtime.verify_module_hash", return_value=SUPPORTED_GAMEASSEMBLY_SHA256), \
                    mock.patch("bot_runtime.WindowsNexus") as actuator, \
                    mock.patch("bot_runtime.time.sleep", side_effect=[None, KeyboardInterrupt]), \
                    contextlib.redirect_stdout(output):
                self.assertEqual(main(["--config", str(config), "--duration", "1"]), 130)
                actuator.assert_not_called()
            events = [json.loads(line) for line in next((root / "analysis").glob("*.jsonl")).read_text(
                encoding="utf-8").splitlines()]
            start_event = next(event for event in events if event["event"] == "start")
            event = next(event for event in events if event["event"] == "decision")
            self.assertIs(start_event["use_state_filters"], False)
            self.assertIs(start_event["prediction_enabled"], True)
            self.assertEqual(start_event["prediction_scope"], "accepted_local_projectile_hits")
            self.assertEqual(start_event["bindings"], {"test": True})
            self.assertIs(event["use_state_filters"], False)
            self.assertEqual(event["frame"], {
                "map_id": "map:1", "map_name": "Realm", "player_hp": 250,
                "max_hp": 1000, "gate_a": None, "gate_b": None, "tracked_hp": None})
            self.assertEqual(event["decision"]["action"], "escape")
            self.assertEqual(event["delivery"]["status"], "observe_only")
            transport = next(item for item in events if item["event"] == "prediction_transport")
            self.assertEqual(transport["sequence"], 2)
            self.assertEqual(transport["events"][0]["amount"], 1)
            self.assertEqual(transport["diagnostics"], {"hit_count": 1})
            state = next(item for item in events if item["event"] == "prediction_state")
            self.assertEqual(state["prediction"]["tracked_hp"], 249)
            self.assertEqual(event["diagnostics"], {"hit_count": 1})
            self.assertIn("Filtros de estado: OMITIDOS", output.getvalue())
            self.assertIn("HP 250/1000", output.getvalue())
            self.assertEqual(calls, ["fake.attach", "agent.stop", "script.unload",
                                     "session.detach", "identity.close"])


if __name__ == "__main__":
    unittest.main()
