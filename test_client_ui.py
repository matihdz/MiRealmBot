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

from client_ui import App, RuntimeInbox
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

        def runtime(config, *, active, stop_event, on_update):
            self.calls.append((config, active))
            on_update({"event": "lifecycle", "status": "running"})
            on_update({"event": "telemetry", "frame": frame, "game_foreground": False,
                       "prediction": {"enabled": config.prediction_enabled, "tracked_hp": 100}})
            stop_event.wait(3)
            self.cleanup_started.set()
            self.release_cleanup.wait(3)
            on_update({"event": "lifecycle", "status": "finished", "cleanup_complete": True, "code": 0})
            return 0

        self.app = App(self.root, config_path=self.config_path, recording_path=self.recording_path,
                       runtime_runner=runtime)
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


if __name__ == "__main__":
    unittest.main()
