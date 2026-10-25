"""Hidden Tk with fake runtimes/settings/updates; no game, input or network."""
import inspect
import json
from pathlib import Path
import tempfile
import threading
import time
import tkinter as tk
from tkinter import ttk
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from client_ui import App, RuntimeInbox
from game_settings import GameBinding
from live_autonexus import SUPPORTED_GAMEASSEMBLY_SHA256


def descendants(widget):
    for child in widget.winfo_children():
        yield child
        yield from descendants(child)


def belongs_to(widget, ancestor):
    while widget is not None:
        if widget is ancestor:
            return True
        widget = widget.master
    return False


class InboxTests(unittest.TestCase):
    def test_bounded_visual_data_retains_lifecycle_logging_warnings_and_last_damage(self):
        inbox = RuntimeInbox()
        for index in range(500):
            inbox.put({"event": "telemetry", "sequence": index})
            inbox.put({"event": "prediction_transport", "events": [{"kind": "damage", "amount": index}]})
        inbox.put({"event": "lifecycle", "status": "finished", "cleanup_complete": True})
        inbox.put({"event": "logging_warning", "logging_enabled": False, "error": "Disco lleno"})
        critical, telemetry, notes, damage = inbox.drain()
        self.assertEqual([item["event"] for item in critical], ["lifecycle", "logging_warning"])
        self.assertEqual(telemetry["sequence"], 499)
        self.assertEqual(damage, 499)
        self.assertEqual(len(notes), 160)
        self.assertEqual(inbox.drain(), ([], None, [], 499))


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
        self.calls = []
        self.settings_calls = 0
        self.release_cleanup = threading.Event()
        self.cleanup_started = threading.Event()
        self.extra_release_events = []
        self.bindings = {"nexus": GameBinding("nexus", "CTRL", 306, "fixture"),
                         "autofire": GameBinding("autofire", None, None, None, "Sin valor guardado")}
        def settings_reader():
            self.settings_calls += 1
            return self.bindings
        def runtime(config, *, active, stop_event, on_update):
            self.calls.append((config, active))
            on_update({"event": "lifecycle", "status": "running"})
            on_update({"event": "telemetry", "frame": {"player_hp": 100, "max_hp": 100, "map_name": "Nexus"},
                       "game_foreground": False, "prediction": {"enabled": config.prediction_enabled, "tracked_hp": 100}})
            stop_event.wait(3)
            self.cleanup_started.set()
            self.release_cleanup.wait(3)
            on_update({"event": "lifecycle", "status": "finished", "cleanup_complete": True, "code": 0})
            return 0
        self.app = App(self.root, config_path=self.config_path, runtime_runner=runtime,
                       settings_reader=settings_reader)
        self.root.update()

    def tearDown(self):
        if not hasattr(self, "app"):
            return
        for event in self.extra_release_events:
            event.set()
        self.release_cleanup.set()
        if self.app.stop_event:
            self.app.stop_event.set()
        try:
            if self.app.runtime_thread:
                self.pump_until(lambda: self.app.runtime_thread is None)
            if self.root.winfo_exists():
                self.app.close()
                if self.app.updates_panel.busy:
                    self.pump_until(lambda: not self.app.updates_panel.busy)
        except tk.TclError:
            pass

    def pump_until(self, condition, timeout=2):
        deadline = time.monotonic() + timeout
        while not condition() and time.monotonic() < deadline:
            self.root.update()
            time.sleep(.005)
        self.assertTrue(condition())

    def select(self, tab):
        self.app.notebook.select(tab)
        self.root.update()

    def test_opening_is_offline_shows_four_tabs_and_keeps_root_withdrawn(self):
        self.assertEqual([self.app.notebook.tab(tab, "text") for tab in self.app.notebook.tabs()],
                         ["Juego en vivo", "AutoNexus", "AutoAim", "Actualizaciones"])
        self.assertEqual(self.app.notebook.select(), str(self.app.live_tab))
        self.assertEqual(self.root.state(), "withdrawn")
        self.assertIsNone(self.app.runtime_thread)
        self.assertFalse(self.app.updates_panel.busy)
        self.assertEqual(self.calls, [])
        self.assertEqual(self.settings_calls, 1)
        self.assertNotIn("recording_path", inspect.signature(App).parameters)
        self.assertFalse(hasattr(self.app, "sidebar"))
        self.assertFalse(hasattr(self.app, "replay_tab"))
        self.assertEqual(list(Path(self.directory.name).iterdir()), [self.config_path])
        self.assertEqual(self.config_path.read_text(encoding="utf-8"), self.initial_config)

    def test_start_stop_and_mode_controls_exist_only_in_live_page(self):
        for control in (self.app.start_button, self.app.stop_button, self.app.observe_radio, self.app.active_radio):
            self.assertTrue(belongs_to(control, self.app.live_tab))
        buttons = [widget for widget in descendants(self.root) if isinstance(widget, ttk.Button)]
        runtime_buttons = [widget for widget in buttons if widget.cget("text").startswith(("Iniciar", "Detener"))]
        self.assertEqual(set(runtime_buttons), {self.app.start_button, self.app.stop_button})
        saves = [widget for widget in buttons if widget.cget("text") == "Guardar configuración"]
        self.assertEqual(len(saves), 2)
        self.assertTrue(any(belongs_to(widget, self.app.nexus_tab) for widget in saves))
        self.assertTrue(any(belongs_to(widget, self.app.aim_tab) for widget in saves))
        self.assertTrue(belongs_to(self.app.detect_keys_check, self.app.nexus_tab))
        self.assertTrue(belongs_to(self.app.prioritize_bosses_check, self.app.aim_tab))

    def test_navigation_uses_one_draft_and_never_starts_runtime_from_config_or_updates(self):
        draft = self.app.draft
        draft.threshold.set("70")
        draft.aim_enabled.set(True)
        draft.extrapolation.set("120")
        for tab in (self.app.nexus_tab, self.app.aim_tab, self.app.updates_panel):
            self.select(tab)
            self.assertFalse(self.app.start_live())
            self.assertEqual(draft.validate().threshold, .7)
            self.assertEqual(draft.validate().autoaim.extrapolation, 120)
        self.select(self.app.live_tab)
        self.assertEqual(self.calls, [])
        self.assertFalse(hasattr(self.app, "active_context"))

    def test_save_validates_both_pages_and_retains_unknown_keys_without_starting(self):
        self.select(self.app.nexus_tab)
        draft = self.app.draft
        draft.extrapolation.set("nan")
        with patch("client_ui.messagebox.showerror"):
            self.assertFalse(self.app.save_parameters())
        self.assertEqual(self.config_path.read_text(encoding="utf-8"), self.initial_config)
        draft.extrapolation.set("12,5")
        draft.threshold.set("40")
        draft.aim_enabled.set(True)
        draft.prioritize_bosses.set(True)
        self.assertTrue(self.app.save_parameters())
        saved = json.loads(self.config_path.read_text(encoding="utf-8"))
        self.assertEqual(saved["threshold"], .4)
        self.assertEqual(saved["extra_user_setting"], "preservar")
        self.assertEqual(saved["autoaim"]["extrapolation"], 12.5)
        self.assertTrue(saved["autoaim"]["prioritize_bosses"])
        self.assertEqual(len(list(self.config_path.parent.glob("*.bak"))), 1)
        self.select(self.app.aim_tab)
        draft.threshold.set("no")
        with patch("client_ui.messagebox.showerror"):
            self.assertFalse(self.app.save_parameters())
        self.assertEqual(self.calls, [])

    def test_config_defaults_and_independent_filters_are_saved_and_restored(self):
        draft = self.app.draft
        self.assertFalse(draft.aim_enabled.get())
        self.assertFalse(draft.prioritize_bosses.get())
        self.assertFalse(draft.ignore_walls.get())
        self.assertEqual(draft.extrapolation.get(), "302")
        self.assertEqual(draft.mouse_radius.get(), "4")
        self.select(self.app.aim_tab)
        for bosses, walls in ((True, False), (False, True), (True, True), (False, False)):
            with self.subTest(bosses=bosses, walls=walls):
                draft.prioritize_bosses.set(bosses)
                draft.ignore_walls.set(walls)
                self.assertTrue(self.app.save_parameters())
                window = tk.Toplevel(self.root)
                window.withdraw()
                restored = App(window, config_path=self.config_path, settings_reader=lambda: self.bindings)
                try:
                    self.assertIs(restored.draft.prioritize_bosses.get(), bosses)
                    self.assertIs(restored.draft.ignore_walls.get(), walls)
                finally:
                    restored.close()
        self.assertEqual(self.calls, [])

    def test_invalid_parameters_never_start_runtime(self):
        draft = self.app.draft
        for variable, invalid, valid in ((draft.threshold, "nan", "30"),
                (draft.extrapolation, "invalid", "302"), (draft.mouse_radius, "0", "4")):
            with self.subTest(invalid=invalid), patch("client_ui.messagebox.showerror"):
                variable.set(invalid)
                self.assertFalse(self.app.start_live())
                variable.set(valid)
        self.assertEqual(self.calls, [])

    def test_runtime_gets_immutable_joint_snapshot_and_locks_all_configuration_pages(self):
        draft = self.app.draft
        draft.threshold.set("60")
        draft.aim_enabled.set(True)
        draft.prioritize_bosses.set(True)
        draft.ignore_walls.set(True)
        draft.extrapolation.set("150")
        self.app.mode_var.set("active")
        self.assertTrue(self.app.start_live())
        self.pump_until(lambda: self.app.status_var.get() == "Activo")
        config, active = self.calls[0]
        self.assertTrue(active)
        self.assertEqual(config.threshold, .6)
        self.assertTrue(config.autoaim.prioritize_bosses)
        self.assertTrue(config.autoaim.ignore_walls)
        self.assertEqual(config.autoaim.activation, "autofire")
        self.assertEqual(config.autoaim.extrapolation, 150)
        for widget in self.app.config_widgets:
            self.assertEqual(str(widget.cget("state")), "disabled")
        self.assertEqual(str(self.app.observe_radio.cget("state")), "disabled")
        self.assertFalse(self.app.save_parameters())
        draft.threshold.set("10")  # Programmatic edits cannot alter the worker's snapshot.
        self.assertEqual(config.threshold, .6)
        self.assertIs(self.app.applied_config, config)
        self.assertEqual(self.config_path.read_text(encoding="utf-8"), self.initial_config)

    def test_applied_status_stays_visible_while_visiting_configuration_tabs(self):
        self.app.draft.detect_game_keys.set(False)
        self.app.refresh_game_bindings()
        self.app.mode_var.set("active")
        self.assertTrue(self.app.start_live())
        self.pump_until(lambda: self.app.status_var.get() == "Activo")
        for tab in (self.app.nexus_tab, self.app.aim_tab, self.app.updates_panel):
            self.select(tab)
            self.assertIn("ACTIVO", self.app.banner_var.get())
            self.assertIn("CTRL", self.app.banner_var.get())
            self.assertIs(self.app.banner.master, self.root)
        self.assertEqual(len(self.calls), 1)

    def test_close_waits_for_real_runtime_cleanup_and_stops_before_destroying_window(self):
        self.assertTrue(self.app.start_live())
        self.pump_until(lambda: self.app.status_var.get() == "Observando")
        self.app.close()
        self.assertTrue(self.app.stop_event.is_set())
        self.assertIsNotNone(self.app.runtime_thread)
        self.assertFalse(self.app.cleanup_complete)
        self.assertTrue(self.root.winfo_exists())
        self.release_cleanup.set()
        self.pump_until(lambda: self.app.runtime_thread is None)
        self.assertTrue(self.app.cleanup_complete)

    def test_cleanup_failure_keeps_error_and_blocks_restart_and_install(self):
        def incomplete(config, *, active, stop_event, on_update):
            on_update({"event": "lifecycle", "status": "finished", "code": 1,
                       "cleanup_complete": False, "errors": ["No se confirmó detach"]})
            return 1
        self.app.runtime_runner = incomplete
        self.assertTrue(self.app.start_live())
        self.pump_until(lambda: self.app.runtime_thread is None)
        self.assertFalse(self.app.cleanup_complete)
        self.assertEqual(self.app.status_var.get(), "Error")
        self.assertIn("detach", self.app.live_vars["error"].get())
        self.assertFalse(self.app.start_live())
        self.assertFalse(self.app._can_install_update()[0])

    def test_missing_final_handshake_does_not_count_as_cleanup(self):
        self.app.runtime_runner = lambda config, **kwargs: 0
        self.assertTrue(self.app.start_live())
        self.pump_until(lambda: self.app.runtime_thread is None)
        self.assertFalse(self.app.cleanup_complete)
        self.assertIn("sin confirmar", self.app.live_hint_var.get())
        self.assertFalse(self.app.start_live())

    def test_final_lifecycle_status_wins_over_pending_telemetry_in_same_batch(self):
        for complete, expected in ((False, "La limpieza quedó incompleta"), (True, "Sesión cerrada")):
            with self.subTest(complete=complete):
                self.app.runtime_inbox.put({"event": "telemetry", "nexus_binding_status": "manual"})
                self.app.runtime_inbox.put({"event": "lifecycle", "status": "finished", "code": 0,
                    "cleanup_complete": complete, "errors": [] if complete else ["Fixture detach pendiente"]})
                self.app._consume_runtime_inbox()
                self.assertIn(expected, self.app.live_hint_var.get())
                self.assertNotIn("Vuelve", self.app.live_hint_var.get())
                self.assertIs(self.app.cleanup_complete, complete)
                self.assertEqual(self.app.status_var.get(), "Detenido" if complete else "Error")

    def test_final_handshake_arriving_after_drain_is_consumed_before_close_decision(self):
        inbox = self.app.runtime_inbox
        class LateThread:
            def is_alive(self):
                inbox.put({"event": "lifecycle", "status": "finished", "cleanup_complete": True, "code": 0})
                return False
            def join(self):
                pass
        self.app.runtime_thread = LateThread()
        self.app.stop_event = threading.Event()
        self.app.cleanup_complete = False
        self.app.close()
        self.pump_until(lambda: self.app.runtime_thread is None)
        self.assertTrue(self.app.cleanup_complete)
        self.assertTrue(self.app.closing)

    def test_session_activity_view_keeps_only_recent_lines(self):
        for index in range(300):
            self.app._append_log(f"Nota {index}")
        lines = self.app.live_log.get("1.0", "end").strip().splitlines()
        self.assertEqual(len(lines), 160)
        self.assertIn("Nota 299", lines[-1])
        self.assertNotIn("Nota 0\n", "\n".join(lines))

    def test_large_errors_and_activity_messages_have_bounded_display_length(self):
        message = "x" * 20000
        self.app._handle_runtime_record({"event": "error", "error": message})
        self.assertLessEqual(len(self.app.last_error), 600)
        self.assertLessEqual(len(self.app.live_vars["error"].get()), 600)
        for _ in range(3):
            self.app._append_log(message)
        text = self.app.live_log.get("1.0", "end").strip()
        self.assertLessEqual(len(text.splitlines()), 160)
        self.assertLessEqual(len(text), 4 * 820)

    def test_unknown_saved_binding_waits_for_internal_read_and_manual_mode_is_explicit(self):
        self.select(self.app.nexus_tab)
        self.bindings["nexus"] = GameBinding("nexus", None, None, None, "Ausente")
        self.app.draft.nexus_key.set("W")
        self.app.refresh_game_bindings()
        self.assertIn("se leerá dentro del juego", self.app.nexus_binding_var.get())
        self.assertEqual(str(self.app.key_combo.cget("state")), "disabled")
        self.app.draft.detect_game_keys.set(False)
        self.app.refresh_game_bindings()
        reads = self.settings_calls
        self.assertIn("tecla manual", self.app.nexus_binding_var.get())
        self.assertEqual(str(self.app.key_combo.cget("state")), "readonly")
        self.select(self.app.live_tab)
        self.assertTrue(self.app.start_live())
        self.pump_until(lambda: len(self.calls) == 1)
        config, _ = self.calls[0]
        self.assertFalse(config.detect_game_keys)
        self.assertEqual(config.nexus_key, "W")
        self.assertEqual(self.settings_calls, reads)

    def test_registry_failure_keeps_internal_detection_enabled(self):
        def fail():
            raise OSError("Fixture sin registro")
        self.app.settings_reader = fail
        self.app.refresh_game_bindings()
        self.assertIn("dentro del juego", self.app.nexus_binding_var.get())
        self.assertTrue(self.app.draft.detect_game_keys.get())

    def test_fresh_runtime_bindings_update_applied_key_but_never_edit_locked_draft(self):
        self.app.mode_var.set("active")
        self.assertTrue(self.app.start_live())
        self.pump_until(lambda: len(self.calls) == 1)
        record = {"event": "telemetry", "nexus_binding_status": "waiting"}
        self.app._handle_runtime_record(record)
        self.assertEqual(self.app.live_vars["decision"].get(), "Esperando tecla Nexus del juego")
        self.assertIn("pendiente", self.app.live_vars["applied"].get())
        record["nexus_binding_status"] = "unsupported"
        self.app._handle_runtime_record(record)
        self.assertIn("no admitida", self.app.live_vars["decision"].get())
        record.update(nexus_binding_status="detected", game_bindings={
            "nexus": {"unity_code": 114, "age_ms": 2}, "autofire": {"unity_code": 105, "age_ms": 2}})
        self.app._handle_runtime_record(record)
        self.assertEqual(self.app.applied_key, "R")
        self.assertEqual(self.app.draft.nexus_key.get(), "CTRL")
        self.assertIn("Autofire: I", self.app.autofire_binding_var.get())
        self.assertIn("· R ·", self.app.live_vars["applied"].get())
        self.assertIn("R", self.app.banner_var.get())
        record["game_bindings"]["nexus"] = {"unity_code": 119, "age_ms": 251}
        record["nexus_binding_status"] = "waiting"
        self.app._handle_runtime_record(record)
        self.assertEqual(self.app.applied_key, "R")
        self.assertIn("sin lectura reciente", self.app.nexus_binding_var.get())

    def test_autoaim_telemetry_preserves_unknown_autofire_and_boss_identity(self):
        for boss, expected in ((True, "Boss"), (False, "Enemigo"), (None, "Tipo sin confirmar"), (1, "Tipo sin confirmar")):
            with self.subTest(boss=boss):
                self.app._handle_runtime_record({"event": "telemetry", "autoaim": {
                    "enabled": True, "active": False, "autofire": None, "target_id": 0, "target_is_boss": boss}})
                text = self.app.live_vars["autoaim"].get()
                self.assertIn(expected, text)
                self.assertIn("sin confirmar", text)
                self.assertIn("Observación", text)
        self.app._handle_runtime_record({"event": "telemetry", "autoaim": {
            "enabled": True, "active": True, "paused": True, "autofire": True}})
        self.assertIn("Pausado", self.app.live_vars["autoaim"].get())

    def test_logging_warnings_do_not_report_bot_stopped_and_preserve_path(self):
        self.app.status_var.set("Activo")
        self.app._handle_runtime_record({"event": "start", "log_path": "fixture/current.jsonl"})
        self.app._handle_runtime_record({"event": "logging_warning", "logging_enabled": True,
                                        "reason": "legacy_cleanup_pending", "error": "Archivo ocupado"})
        self.assertIn("Registro activo", self.app.live_vars["logging"].get())
        self.app._handle_runtime_record({"event": "logging_warning", "logging_enabled": False,
                                        "error": "Disco lleno"})
        self.assertIn("sigue funcionando", self.app.live_vars["logging"].get())
        self.assertIn("Disco lleno", self.app.live_vars["logging"].get())
        self.assertEqual(self.app.live_vars["log"].get(), "fixture/current.jsonl")
        self.assertEqual(self.app.status_var.get(), "Activo")

    def test_update_install_waits_for_cleanup_and_launch_failure_keeps_window(self):
        panel = self.app.updates_panel
        panel.portable = True
        panel.prepared = object()
        self.app.cleanup_complete = False
        with patch("client_ui.launch_update") as launch:
            self.assertFalse(panel.install())
            launch.assert_not_called()
        self.app.cleanup_complete = True
        with patch("client_ui.launch_update", side_effect=OSError("No se pudo iniciar")):
            self.assertFalse(panel.install())
        self.assertFalse(panel.installing)
        self.assertTrue(self.root.winfo_exists())

    def test_update_worker_starts_before_the_application_is_closed(self):
        panel = self.app.updates_panel
        panel.portable = True
        panel.prepared = prepared = object()
        calls = []
        def launch(value):
            self.assertIs(value, prepared)
            self.assertTrue(self.root.winfo_exists())
            calls.append("launched")
        with patch("client_ui.launch_update", side_effect=launch):
            self.assertTrue(panel.install())
        self.assertEqual(calls, ["launched"])

    def test_close_waits_for_cancelled_network_worker_as_well_as_runtime(self):
        release, cancel_seen = threading.Event(), threading.Event()
        self.extra_release_events.append(release)
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
