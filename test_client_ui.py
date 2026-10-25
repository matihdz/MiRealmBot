"""Exercise UI ownership with an offline fixture and fake runtime only."""
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import tempfile
import threading
import time
import tkinter as tk
import unittest
from unittest.mock import patch
from types import SimpleNamespace

from client_ui import App, RuntimeInbox
from game_settings import GameBinding
from live_autonexus import SUPPORTED_GAMEASSEMBLY_SHA256


class InboxTests(unittest.TestCase):
    def test_visual_telemetry_is_bounded_but_lifecycle_and_damage_are_retained(self):
        inbox = RuntimeInbox()
        for index in range(500):
            inbox.put({"event": "telemetry", "sequence": index})
        inbox.put({"event": "prediction_transport", "events": [{"kind": "damage", "amount": 65}]})
        inbox.put({"event": "lifecycle", "status": "finished", "cleanup_complete": True})
        critical, telemetry, notes, damage = inbox.drain()
        self.assertEqual(len(critical), 1)
        self.assertEqual(telemetry["sequence"], 499)
        self.assertEqual(damage, 65)
        self.assertIn("65", notes[0])
        self.assertIsNone(inbox.drain()[1])


class ClientTests(unittest.TestCase):
    def setUp(self):
        try:
            self.root = tk.Tk()
        except tk.TclError as error:
            self.skipTest(f"Tk no disponible: {error}")
        self.root.withdraw()
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.config_path = Path(self.directory.name) / "autonexus.json"
        self.initial_config = json.dumps({"threshold": .3, "prediction_enabled": False,
            "use_state_filters": False, "nexus_key": "CTRL", "excluded_maps": ["Nexus"],
            "gameassembly_sha256": SUPPORTED_GAMEASSEMBLY_SHA256, "extra_user_setting": "preservar"})
        self.config_path.write_text(self.initial_config, encoding="utf-8")
        self.recording_path = Path(self.directory.name) / "offline.jsonl"
        start = datetime(2026, 10, 24, tzinfo=timezone.utc)
        frame = {"map_id": "world:1", "map_name": "Realm", "player_hp": 100, "max_hp": 100,
                 "gate_a": None, "gate_b": None, "tracked_hp": None}
        rows = [dict(event="start", active=False, captured_at_utc=start.isoformat()),
            dict(event="decision", status="fresh", sequence=1, age_ms=0, frame=frame,
                 captured_at_utc=(start + timedelta(seconds=.1)).isoformat()),
            dict(event="prediction_transport", sequence=2, age_ms=0, events_lost=False,
                 events=[dict(event_id=1, kind="damage", amount=65, age_ms=0,
                              cause="projectile", frame=frame)],
                 captured_at_utc=(start + timedelta(seconds=1)).isoformat()),
            dict(event="cleanup", complete=True, errors=[],
                 captured_at_utc=(start + timedelta(seconds=2)).isoformat())]
        self.recording_path.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")
        self.calls = []
        self.release_cleanup = threading.Event()
        self.cleanup_started = threading.Event()
        self.settings_calls = 0
        self.bindings = {
            "nexus": GameBinding("nexus", "CTRL", 306, "fixture"),
            "autofire": GameBinding("autofire", None, None, None, "Sin valor guardado")}
        def settings_reader():
            self.settings_calls += 1
            return self.bindings

        def runtime(config, *, active, stop_event, on_update):
            self.calls.append((config, active))
            on_update({"event": "lifecycle", "status": "running"})
            on_update({"event": "telemetry", "frame": frame, "game_foreground": False,
                       "nexus_binding_status": "detected",
                       "game_bindings": {"nexus": {"unity_code": 306, "age_ms": 0}},
                       "prediction": {"enabled": config.prediction_enabled, "tracked_hp": 100}})
            stop_event.wait(3)
            self.cleanup_started.set()
            self.release_cleanup.wait(3)
            on_update({"event": "lifecycle", "status": "finished", "cleanup_complete": True, "code": 0})
            return 0

        self.app = App(self.root, config_path=self.config_path, recording_path=self.recording_path,
                       runtime_runner=runtime, settings_reader=settings_reader)
        self.root.update()

    def tearDown(self):
        if not hasattr(self, "app"):
            return
        self.release_cleanup.set()
        if self.app.stop_event:
            self.app.stop_event.set()
        if self.app.runtime_thread:
            self.pump_until(lambda: self.app.runtime_thread is None)
        try:
            if self.root.winfo_exists():
                self.app.close()
        except tk.TclError:
            pass

    def pump_until(self, condition, timeout=2):
        deadline = time.monotonic() + timeout
        while not condition() and time.monotonic() < deadline:
            self.root.update()
            time.sleep(.01)
        self.assertTrue(condition())

    def select_live(self):
        self.app.notebook.select(self.app.live_tab)
        self.root.update()

    def test_opening_replays_only_and_keeps_live_defaults_independent(self):
        self.assertTrue(self.app.prediction_var.get())
        self.assertFalse(self.app.live_draft["prediction_enabled"])
        self.assertEqual(self.calls, [])
        self.assertIn("SIN CONEXIÓN", self.app.banner_var.get())
        self.assertEqual(self.app.replay_position, 1)
        self.assertEqual(self.config_path.read_text(encoding="utf-8"), self.initial_config)

    def test_replay_parameter_changes_recompute_and_copy_only_a_draft(self):
        self.assertIsNone(self.app.replay_result.summary["first_escape"])
        self.app.threshold_var.set("40")
        self.app.recompute_replay()
        self.assertEqual(self.app.replay_result.summary["first_escape"]["predicted_hp"], 35)
        self.app.apply_replay_to_live()
        self.root.update()
        self.assertEqual(self.app.active_context, "live")
        self.assertEqual(self.app.live_draft["threshold_percent"], 40)
        self.assertTrue(self.app.prediction_var.get())
        self.assertEqual(self.calls, [])
        self.assertEqual(self.config_path.read_text(encoding="utf-8"), self.initial_config)

    def test_invalid_threshold_never_starts_runtime(self):
        self.select_live()
        self.app.threshold_var.set("nan")
        with patch("client_ui.messagebox.showerror"):
            self.assertFalse(self.app.start_live())
        self.assertEqual(self.calls, [])

    def test_direct_replay_refresh_cancels_the_superseded_debounce(self):
        self.app.threshold_var.set("40")
        pending = self.app._update_after
        self.assertIn(pending, self.root.tk.call("after", "info"))
        self.app.recompute_replay()
        self.assertIsNone(self.app._update_after)
        self.assertNotIn(pending, self.root.tk.call("after", "info"))

    def test_live_worker_owns_config_and_stops_before_window_closes(self):
        self.select_live()
        self.app.mode_var.set("active")
        self.assertTrue(self.app.start_live())
        self.pump_until(lambda: self.app.live_status == "Activo")
        self.assertEqual(str(self.app.threshold_input.cget("state")), "disabled")
        self.assertIn("Vuelve", self.app.live_vars["focus"].get())
        self.app.notebook.select(self.app.replay_tab)
        self.root.update()
        self.assertIn("PUEDE ENVIAR CTRL", self.app.banner_var.get())
        self.assertNotIn("SIN CONEXIÓN", self.app.banner_var.get())
        self.app.prediction_var.set(True)
        self.assertFalse(self.calls[0][0].prediction_enabled)
        self.app.close()
        self.assertTrue(self.app.stop_event.is_set())
        self.assertIsNotNone(self.app.runtime_thread)
        self.assertFalse(self.app.cleanup_complete)
        self.assertTrue(self.root.winfo_exists())
        self.release_cleanup.set()
        self.pump_until(lambda: self.app.runtime_thread is None)
        self.assertTrue(self.app.cleanup_complete)

    def test_save_uses_owner_preserves_extra_keys_and_does_not_start(self):
        self.select_live()
        self.app.threshold_var.set("40")
        self.app.save_parameters()
        saved = json.loads(self.config_path.read_text(encoding="utf-8"))
        self.assertEqual(saved["threshold"], .4)
        self.assertEqual(saved["extra_user_setting"], "preservar")
        self.assertEqual(self.calls, [])
        self.assertEqual(len(list(self.config_path.parent.glob("*.bak"))), 1)

    def test_cleanup_failure_keeps_error_and_blocks_restart(self):
        def incomplete(config, *, active, stop_event, on_update):
            on_update({"event": "lifecycle", "status": "finished", "code": 1,
                       "cleanup_complete": False, "errors": ["No se confirmó detach"]})
            return 1
        self.app.runtime_runner = incomplete
        self.select_live()
        self.assertTrue(self.app.start_live())
        self.pump_until(lambda: self.app.runtime_thread is None)
        self.assertFalse(self.app.cleanup_complete)
        self.assertEqual(self.app.status_var.get(), "Error")
        self.assertIn("detach", self.app.live_vars["error"].get())
        self.assertFalse(self.app.start_live())

    def select_aim(self):
        self.app.notebook.select(self.app.aim_tab)
        self.root.update()

    def test_autoaim_defaults_and_tab_drafts_are_independent_of_updates_and_replay(self):
        self.assertFalse(self.app.aim_enabled_var.get())
        self.assertEqual(self.app.extrapolation_var.get(), "302")
        self.assertEqual(self.app.aim_radius_var.get(), "4")
        self.assertEqual(self.settings_calls, 1)
        self.assertIn("se leerá dentro del juego", self.app.bindings_var.get())
        self.app.threshold_var.set("40")
        self.select_aim()
        self.assertEqual(self.app.active_context, "live")
        self.app.threshold_var.set("70")
        self.app.extrapolation_var.set("120")
        self.app.notebook.select(self.app.updates_panel)
        self.root.update()
        self.assertEqual(self.app.live_draft["threshold_percent"], 70)
        self.assertEqual(self.app.replay_draft["threshold_percent"], 40)
        self.assertFalse(self.app.start_live())
        self.app.notebook.select(self.app.replay_tab)
        self.root.update()
        self.assertEqual(self.app.threshold_var.get(), "40")
        self.select_aim()
        self.assertEqual(self.app.threshold_var.get(), "70")
        self.assertEqual(self.app.extrapolation_var.get(), "120")
        self.assertEqual(self.calls, [])

    def test_invalid_autoaim_numbers_never_start_a_session(self):
        self.select_aim()
        self.app.extrapolation_var.set("nan")
        with patch("client_ui.messagebox.showerror"):
            self.assertFalse(self.app.start_live())
        self.app.extrapolation_var.set("302")
        self.app.aim_radius_var.set("0")
        with patch("client_ui.messagebox.showerror"):
            self.assertFalse(self.app.start_live())
        self.assertEqual(self.calls, [])

    def test_saving_autoaim_retains_unknown_config_and_uses_the_owned_parser(self):
        self.select_aim()
        self.app.aim_enabled_var.set(True)
        self.app.extrapolation_var.set("12,5")
        self.app.aim_radius_var.set("3")
        self.app.save_parameters()
        saved = json.loads(self.config_path.read_text(encoding="utf-8"))
        self.assertEqual(saved["autoaim"], {"enabled": True, "mode": "close_to_mouse",
            "activation": "autofire", "extrapolation": 12.5, "mouse_radius": 3.0})
        self.assertTrue(saved["detect_game_keys"])
        self.assertEqual(saved["extra_user_setting"], "preservar")
        self.assertEqual(self.calls, [])

    def test_start_from_autoaim_rereads_nexus_and_keeps_observe_authority(self):
        self.select_aim()
        self.bindings["nexus"] = GameBinding("nexus", "R", 114, "fixture")
        self.bindings["autofire"] = GameBinding("autofire", "I", 105, "fixture")
        self.app.aim_enabled_var.set(True)
        self.app.extrapolation_var.set("150")
        self.assertTrue(self.app.start_live())
        self.pump_until(lambda: len(self.calls) == 1)
        config, active = self.calls[0]
        self.assertEqual(config.nexus_key, "R")
        self.assertTrue(config.autoaim.enabled)
        self.assertEqual(config.autoaim.activation, "autofire")
        self.assertEqual(config.autoaim.extrapolation, 150)
        self.assertFalse(active)
        self.assertIn("Autofire: I", self.app.bindings_var.get())
        self.assertEqual(str(self.app.extrapolation_input.cget("state")), "disabled")

    def test_unknown_saved_key_waits_for_game_and_manual_mode_does_not_read(self):
        self.select_aim()
        self.app.key_var.set("W")
        self.bindings["nexus"] = GameBinding("nexus", None, None, None, "Ausente")
        self.app.refresh_game_bindings()
        self.assertIn("se leerá dentro del juego", self.app.bindings_var.get())
        self.assertNotIn("manual", self.app.bindings_var.get())
        self.assertEqual(str(self.app.key_combo.cget("state")), "disabled")
        self.app.detect_keys_var.set(False)
        self.app.refresh_game_bindings()
        self.assertIn("tecla manual", self.app.bindings_var.get())
        self.assertEqual(str(self.app.key_combo.cget("state")), "readonly")
        reads = self.settings_calls
        self.assertTrue(self.app.start_live())
        self.pump_until(lambda: len(self.calls) == 1)
        config, _ = self.calls[0]
        self.assertFalse(config.detect_game_keys)
        self.assertEqual(config.nexus_key, "W")
        self.assertEqual(self.settings_calls, reads)

    def test_registry_failure_still_promises_internal_read_instead_of_manual_fallback(self):
        self.select_aim()
        def fail():
            raise OSError("Fixture sin registro")
        self.app.settings_reader = fail
        self.app.refresh_game_bindings()
        self.assertIn("Se comprobarán dentro del juego", self.app.bindings_var.get())
        self.assertNotIn("manual", self.app.bindings_var.get())
        self.assertTrue(self.app.detect_keys_var.get())

    def test_waiting_and_unsupported_nexus_bindings_have_specific_live_status(self):
        self.select_live()
        self.assertTrue(self.app.start_live())
        self.pump_until(lambda: len(self.calls) == 1)
        record = {"event": "telemetry", "nexus_binding_status": "waiting",
                  "decision": {"action": "none", "reason": "AutoNexus is disabled"}}
        self.app._handle_runtime_record(record)
        self.assertEqual(self.app.live_vars["decision"].get(), "Esperando tecla Nexus del juego")
        self.assertIn("pendiente de lectura", self.app.live_vars["applied"].get())
        record["nexus_binding_status"] = "unsupported"
        self.app._handle_runtime_record(record)
        self.assertIn("no admitida", self.app.live_vars["decision"].get())
        self.assertIn("Detén la sesión", self.app.live_hint_var.get())
        record["nexus_binding_status"] = "detected"
        record["game_bindings"] = {"nexus": {"unity_code": 306, "age_ms": 2}}
        record["decision"] = {"action": "none", "reason": "health is below the limit"}
        self.app._handle_runtime_record(record)
        self.assertEqual(self.app.live_vars["decision"].get(), "Vida bajo el límite")
        self.assertIn("· CTRL ·", self.app.live_vars["applied"].get())

    def test_autoaim_telemetry_preserves_unknown_autofire_and_observe_mode(self):
        self.app._handle_runtime_record({"event": "telemetry", "autoaim": {
            "enabled": True, "active": False, "autofire": None, "target_id": 0,
            "angle": 1.2}})
        self.assertIn("sin confirmar", self.app.aim_status_var.get())
        self.assertIn("Observación", self.app.aim_status_var.get())
        self.assertIn("enemigo 0", self.app.aim_status_var.get())
        self.app._handle_runtime_record({"event": "telemetry", "autoaim": {
            "enabled": True, "active": True, "autofire": True, "paused": True}})
        self.assertIn("Pausado", self.app.aim_status_var.get())
        self.assertIn("encendido", self.app.aim_status_var.get())

    def test_runtime_bindings_only_change_applied_key_when_fresh_and_detection_enabled(self):
        self.select_live()
        self.assertTrue(self.app.start_live())
        self.pump_until(lambda: len(self.calls) == 1)
        record = {"event": "telemetry", "game_bindings": {
            "nexus": {"unity_code": 114, "age_ms": 10, "source": "fixture"},
            "autofire": {"unity_code": 105, "age_ms": 10, "source": "fixture"}}}
        self.app._handle_runtime_record(record)
        self.assertEqual(self.app.applied_key, "R")
        self.assertEqual(self.app.key_var.get(), "R")
        self.assertIn("Autofire: I", self.app.bindings_var.get())
        self.assertIn("· R ·", self.app.live_vars["applied"].get())
        record["game_bindings"]["nexus"] = {"unity_code": 119, "age_ms": 251}
        self.app._handle_runtime_record(record)
        self.assertEqual(self.app.applied_key, "R")
        self.assertIn("sin lectura reciente", self.app.bindings_var.get())
        self.app.applied_detect_keys = False
        record["game_bindings"]["nexus"] = {"unity_code": 119, "age_ms": 0}
        self.app._handle_runtime_record(record)
        self.assertEqual(self.app.applied_key, "R")

    def test_update_install_requires_complete_cleanup_and_failed_launch_keeps_window(self):
        self.app.updates_panel.portable = True
        self.app.updates_panel.prepared = object()
        self.app.cleanup_complete = False
        with patch("client_ui.launch_update") as launch:
            self.assertFalse(self.app.updates_panel.install())
            launch.assert_not_called()
        self.app.cleanup_complete = True
        with patch("client_ui.launch_update", side_effect=OSError("No se pudo iniciar")):
            self.assertFalse(self.app.updates_panel.install())
        self.assertFalse(self.app.updates_panel.installing)
        self.assertTrue(self.root.winfo_exists())

    def test_update_launch_success_closes_only_after_worker_is_started(self):
        self.app.updates_panel.portable = True
        self.app.updates_panel.prepared = prepared = object()
        calls = []
        def launch(value):
            self.assertIs(value, prepared)
            self.assertTrue(self.root.winfo_exists())
            calls.append("launched")
        with patch("client_ui.launch_update", side_effect=launch):
            self.assertTrue(self.app.updates_panel.install())
        self.assertEqual(calls, ["launched"])

    def test_close_waits_for_cancelled_network_worker_in_addition_to_runtime(self):
        release, cancel_seen = threading.Event(), threading.Event()
        def check(version, *, cancel_event):
            cancel_event.wait(2)
            cancel_seen.set()
            release.wait(2)
            return SimpleNamespace(release=SimpleNamespace(version="9.0.0", notes="demo"), available=True)
        self.app.updates_panel.service = SimpleNamespace(check_for_update=check)
        self.assertTrue(self.app.updates_panel.check())
        self.app.close()
        self.assertTrue(cancel_seen.wait(1))
        self.assertTrue(self.app.updates_panel.busy)
        self.assertTrue(self.root.winfo_exists())
        release.set()
        self.pump_until(lambda: not self.app.updates_panel.busy)


if __name__ == "__main__":
    unittest.main()
