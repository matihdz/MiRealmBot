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

from client_ui import App, RuntimeInbox, debuff_observation_text
from debuffs_settings import QUIET_SUPPORTED, QUIET_UNSUPPORTED_REASON
from game_settings import GameBinding
from bot_runtime import SUPPORTED_GAMEASSEMBLY_SHA256, config_to_mapping


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
        inbox.put({"event": "quiet_observation", "quiet_observation": {"revision": 2, "last_episode": {"state": "ended"}}})
        critical, telemetry, notes, damage = inbox.drain()
        self.assertEqual([item["event"] for item in critical], ["lifecycle", "logging_warning"])
        self.assertEqual(telemetry["sequence"], 499)
        self.assertEqual(damage, 499)
        self.assertEqual(len(notes), 160)
        self.assertIn("Registro de Quiet", notes[-1])
        self.assertEqual(inbox.drain(), ([], None, [], 499))

    def test_debuff_diagnostic_burst_is_bounded_and_cannot_block_critical_delivery(self):
        inbox = RuntimeInbox()
        with patch.object(inbox.critical, "put", side_effect=AssertionError("Diagnostics must not wait on the critical queue")):
            for revision in range(500):
                for field in ("quiet_observation", "confuse_observation", "blind_observation", "silenced_observation"):
                    inbox.put({"event": field, field: {
                        "enabled": True, "revision": revision, "fresh": False,
                        "detections": revision, "error": "x" * 2000}})
        inbox.put({"event": "cleanup", "complete": True})
        inbox.put({"event": "lifecycle", "status": "finished", "cleanup_complete": True})
        critical, _, notes, _ = inbox.drain()
        self.assertEqual([item["event"] for item in critical], ["cleanup", "lifecycle"])
        self.assertEqual(len(notes), 160)
        self.assertIn("intervalos detectados 499", notes[-1])
        self.assertIn("Registro de Quiet", notes[-4])
        self.assertIn("Registro de Confuse", notes[-3])
        self.assertIn("Registro de Blind", notes[-2])
        self.assertIn("Registro de Silenced", notes[-1])
        self.assertTrue(all(len(note) < 800 for note in notes))

    def test_final_diagnostic_warning_is_a_bounded_note(self):
        inbox = RuntimeInbox()
        inbox.put({"event": "diagnostic_warning", "module": "Debuffs", "error": "x" * 2000})
        critical, _, notes, _ = inbox.drain()
        self.assertEqual(critical, [])
        self.assertEqual(len(notes), 1)
        self.assertIn("Aviso del registro de Debuffs", notes[0])
        self.assertLess(len(notes[0]), 300)


class ClientTests(unittest.TestCase):
    def setUp(self):
        try:
            self.root = tk.Tk()
        except tk.TclError as error:
            self.skipTest(f"Tk no disponible: {error}")
        self.root.withdraw()
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.config_path = Path(self.directory.name) / "mirealmbot.json"
        self.initial_config = json.dumps({"threshold": .3, "prediction_enabled": False,
            "use_state_filters": False, "nexus_key": "CTRL", "excluded_maps": ["Nexus"],
            "gameassembly_sha256": SUPPORTED_GAMEASSEMBLY_SHA256, "extra_user_setting": "preservar"})
        self.config_path.write_text(self.initial_config, encoding="utf-8")
        self.calls = []
        self.settings_calls = 0
        self.release_cleanup = threading.Event()
        self.cleanup_started = threading.Event()
        self.extra_release_events = []
        self.requests = []
        self.allow_apply = threading.Event()
        self.allow_apply.set()
        self.worker_ids = []
        self.bindings = {"nexus": GameBinding("nexus", "CTRL", 306, "fixture"),
                         "autofire": GameBinding("autofire", None, None, None, "Sin valor guardado")}
        def settings_reader():
            self.settings_calls += 1
            return self.bindings
        def runtime(config, *, active, stop_event, on_update, control):
            self.calls.append((config, active))
            self.worker_ids.append(threading.get_ident())
            on_update({"event": "config_applied", "revision": 0, "configuration": config_to_mapping(config)})
            on_update({"event": "lifecycle", "status": "running"})
            on_update({"event": "telemetry", "frame": {"player_hp": 100, "max_hp": 100, "map_name": "Nexus"},
                       "game_foreground": False, "prediction": {"enabled": config.prediction_enabled, "tracked_hp": 100}})
            while not stop_event.wait(.01):
                request = control.take_latest() if self.allow_apply.is_set() else None
                if request is not None:
                    revision, configured = request
                    self.requests.append(request)
                    self.worker_ids.append(threading.get_ident())
                    on_update({"event": "config_applied", "revision": revision,
                               "configuration": config_to_mapping(configured)})
                    if not (configured.autonexus_enabled or configured.autoaim.enabled or configured.debuffs.has_work):
                        on_update({"event": "stop_requested", "reason": "no_enabled_modules"})
                        break
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

    def test_opening_is_offline_shows_five_tabs_and_keeps_root_withdrawn(self):
        self.assertEqual([self.app.notebook.tab(tab, "text") for tab in self.app.notebook.tabs()],
                         ["Juego en vivo", "AutoNexus", "AutoAim", "Debuffs", "Actualizaciones"])
        self.assertEqual(self.app.notebook.select(), str(self.app.live_tab))
        self.assertEqual(self.root.state(), "withdrawn")
        self.assertIsNone(self.app.runtime_thread)
        self.assertEqual(self.app.status_var.get(), "Detenido")
        self.assertFalse(self.app.updates_panel.busy)
        self.assertEqual(self.calls, [])
        self.assertEqual(self.settings_calls, 1)
        self.assertNotIn("recording_path", inspect.signature(App).parameters)
        self.assertFalse(hasattr(self.app, "sidebar"))
        self.assertFalse(hasattr(self.app, "replay_tab"))
        self.assertEqual(list(Path(self.directory.name).iterdir()), [self.config_path])
        self.assertEqual(self.config_path.read_text(encoding="utf-8"), self.initial_config)

    def test_start_stop_exist_only_in_live_page_and_no_mode_selector_is_exposed(self):
        for control in (self.app.start_button, self.app.stop_button):
            self.assertTrue(belongs_to(control, self.app.live_tab))
        self.assertEqual(self.app.start_button.cget("text"), "Iniciar bot")
        self.assertFalse(any(isinstance(widget, ttk.Radiobutton) for widget in descendants(self.root)))
        self.assertFalse(hasattr(self.app, "mode_var"))
        self.assertFalse(hasattr(self.app, "live_active"))
        buttons = [widget for widget in descendants(self.root) if isinstance(widget, ttk.Button)]
        runtime_buttons = [widget for widget in buttons if widget.cget("text").startswith(("Iniciar", "Detener"))]
        self.assertEqual(set(runtime_buttons), {self.app.start_button, self.app.stop_button})
        self.assertFalse(any("Guardar" in widget.cget("text") for widget in buttons))
        self.assertTrue(belongs_to(self.app.detect_keys_check, self.app.nexus_tab))
        self.assertTrue(belongs_to(self.app.prioritize_bosses_check, self.app.aim_tab))
        self.assertTrue(belongs_to(self.app.nexus_enabled_check, self.app.nexus_tab))
        self.assertTrue(belongs_to(self.app.quiet_check, self.app.debuffs_tab))
        quiet_checks = [widget for widget in descendants(self.app.debuffs_tab)
                        if isinstance(widget, ttk.Checkbutton) and widget.cget("text").startswith("Quiet")]
        self.assertEqual(quiet_checks, [self.app.quiet_check])
        self.assertEqual(self.app.quiet_check.cget("text"), "Quiet")
        self.assertEqual(self.app.confuse_check.cget("text"), "Confuse")
        confuse_checks = [widget for widget in descendants(self.app.debuffs_tab)
                          if isinstance(widget, ttk.Checkbutton) and widget.cget("text").startswith("Confuse")]
        self.assertEqual(confuse_checks, [self.app.confuse_check])
        blind_checks = [widget for widget in descendants(self.app.debuffs_tab)
                        if isinstance(widget, ttk.Checkbutton) and widget.cget("text").startswith("Blind")]
        self.assertEqual(blind_checks, [self.app.blind_check])
        self.assertEqual(self.app.blind_check.cget("text"), "Blind")
        silenced_checks = [widget for widget in descendants(self.app.debuffs_tab)
                           if isinstance(widget, ttk.Checkbutton) and widget.cget("text").startswith("Silenced")]
        self.assertEqual(silenced_checks, [self.app.silenced_check])
        self.assertEqual(self.app.silenced_check.cget("text"), "Silenced")
        help_text = " ".join(str(widget.cget("text"))
                             for widget in descendants(self.app.debuffs_tab)
                             if isinstance(widget, ttk.Label))
        self.assertIn("No bloquea Quiet", help_text)
        self.assertIn("Silenced es experimental", help_text)

    def test_navigation_uses_one_draft_and_never_starts_runtime_from_config_or_updates(self):
        draft = self.app.draft
        draft.threshold.set("70")
        draft.aim_enabled.set(True)
        draft.extrapolation.set("120")
        for tab in (self.app.nexus_tab, self.app.aim_tab, self.app.debuffs_tab, self.app.updates_panel):
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
            self.assertFalse(self.app.flush_configuration())
        self.assertEqual(self.config_path.read_text(encoding="utf-8"), self.initial_config)
        draft.extrapolation.set("12,5")
        draft.threshold.set("40")
        draft.aim_enabled.set(True)
        draft.prioritize_bosses.set(True)
        self.assertTrue(self.app.flush_configuration())
        saved = json.loads(self.config_path.read_text(encoding="utf-8"))
        self.assertEqual(saved["threshold"], .4)
        self.assertEqual(saved["extra_user_setting"], "preservar")
        self.assertEqual(saved["autoaim"]["extrapolation"], 12.5)
        self.assertTrue(saved["autoaim"]["prioritize_bosses"])
        self.assertEqual(len(list(self.config_path.parent.glob("*.bak"))), 1)
        self.select(self.app.aim_tab)
        draft.threshold.set("no")
        with patch("client_ui.messagebox.showerror"):
            self.assertFalse(self.app.flush_configuration())
        self.assertEqual(self.calls, [])

    def test_config_defaults_and_independent_filters_are_saved_and_restored(self):
        draft = self.app.draft
        self.assertTrue(draft.nexus_enabled.get())
        self.assertFalse(draft.aim_enabled.get())
        self.assertFalse(draft.debuffs_enabled.get())
        self.assertTrue(draft.confuse.get())
        self.assertFalse(draft.blind.get())
        self.assertFalse(draft.silenced.get())
        self.assertFalse(hasattr(draft, "quiet"))
        self.assertFalse(draft.record_quiet.get())
        self.assertFalse(draft.prioritize_bosses.get())
        self.assertFalse(draft.ignore_walls.get())
        self.assertEqual(draft.extrapolation.get(), "302")
        self.assertEqual(draft.mouse_radius.get(), "4")
        self.select(self.app.aim_tab)
        for bosses, walls in ((True, False), (False, True), (True, True), (False, False)):
            with self.subTest(bosses=bosses, walls=walls):
                draft.prioritize_bosses.set(bosses)
                draft.ignore_walls.set(walls)
                self.assertTrue(self.app.flush_configuration())
                window = tk.Toplevel(self.root)
                window.withdraw()
                restored = App(window, config_path=self.config_path, settings_reader=lambda: self.bindings)
                try:
                    self.assertIs(restored.draft.prioritize_bosses.get(), bosses)
                    self.assertIs(restored.draft.ignore_walls.get(), walls)
                finally:
                    restored.close()
        self.assertEqual(self.calls, [])

    def test_default_config_migration_is_only_requested_when_no_path_was_selected(self):
        for explicit in (True, False):
            with self.subTest(explicit=explicit), patch("client_ui.ensure_default_config", return_value=self.config_path) as ensure:
                window = tk.Toplevel(self.root)
                window.withdraw()
                opened = App(window, config_path=self.config_path if explicit else None,
                             settings_reader=lambda: self.bindings)
                try:
                    self.assertEqual(opened.config_path, self.config_path)
                    ensure.assert_not_called() if explicit else ensure.assert_called_once_with()
                finally:
                    opened.close()

    def test_disabled_modules_can_be_saved_but_cannot_start_any_worker(self):
        draft = self.app.draft
        draft.nexus_enabled.set(False)
        draft.aim_enabled.set(False)
        draft.debuffs_enabled.set(False)
        self.assertEqual(str(self.app.start_button.cget("state")), "disabled")
        self.assertIn("Activa al menos un módulo", self.app.live_hint_var.get())
        self.assertFalse(self.app.start_live())
        self.assertEqual(self.calls, [])
        self.select(self.app.debuffs_tab)
        self.assertTrue(self.app.flush_configuration())
        saved = json.loads(self.config_path.read_text(encoding="utf-8"))
        self.assertFalse(saved["autonexus_enabled"])
        self.assertFalse(saved["autoaim"]["enabled"])
        self.assertFalse(saved["debuffs"]["enabled"])
        self.assertEqual(saved["extra_user_setting"], "preservar")

    def test_each_module_can_start_independently_from_the_other_two(self):
        draft = self.app.draft
        for selected in ("nexus_enabled", "aim_enabled", "debuffs_enabled", "blind", "silenced"):
            with self.subTest(module=selected):
                draft.nexus_enabled.set(selected == "nexus_enabled")
                draft.aim_enabled.set(selected == "aim_enabled")
                draft.debuffs_enabled.set(selected in ("debuffs_enabled", "blind", "silenced"))
                draft.confuse.set(selected == "debuffs_enabled")
                draft.blind.set(selected == "blind")
                draft.silenced.set(selected == "silenced")
                self.assertEqual(str(self.app.start_button.cget("state")), "normal")
                self.assertTrue(self.app.start_live())
                self.pump_until(lambda: self.app.status_var.get() == "Activo")
                config, active = self.calls[-1]
                self.assertTrue(active)
                self.assertEqual(config.autonexus_enabled, selected == "nexus_enabled")
                self.assertEqual(config.autoaim.enabled, selected == "aim_enabled")
                self.assertEqual(config.debuffs.enabled, selected in ("debuffs_enabled", "blind", "silenced"))
                self.assertEqual(config.debuffs.confuse, selected == "debuffs_enabled")
                self.assertEqual(config.debuffs.blind, selected == "blind")
                self.assertEqual(config.debuffs.silenced, selected == "silenced")
                self.assertEqual(str(self.app.threshold_input.cget("state")), "normal")
                self.app.stop_live()
                self.release_cleanup.set()
                self.pump_until(lambda: self.app.runtime_thread is None)

    def test_debuff_choices_are_saved_loaded_and_do_not_change_aim_or_nexus(self):
        self.select(self.app.debuffs_tab)
        draft = self.app.draft
        draft.nexus_enabled.set(False)
        draft.aim_enabled.set(True)
        draft.debuffs_enabled.set(True)
        draft.confuse.set(False)
        draft.blind.set(True)
        draft.silenced.set(True)
        draft.record_quiet.set(True)
        self.assertTrue(self.app.flush_configuration())
        window = tk.Toplevel(self.root)
        window.withdraw()
        restored = App(window, config_path=self.config_path, settings_reader=lambda: self.bindings)
        try:
            config = restored.draft.validate()
            self.assertFalse(config.autonexus_enabled)
            self.assertTrue(config.autoaim.enabled)
            self.assertTrue(config.debuffs.enabled)
            self.assertFalse(config.debuffs.confuse)
            self.assertTrue(config.debuffs.blind)
            self.assertTrue(config.debuffs.silenced)
            self.assertFalse(config.debuffs.quiet)
            self.assertTrue(config.debuffs.record_quiet)
        finally:
            restored.close()

    def test_aim_only_session_ignores_nexus_binding_wait_and_unsupported_status(self):
        draft = self.app.draft
        draft.nexus_enabled.set(False)
        draft.aim_enabled.set(True)
        self.assertTrue(self.app.start_live())
        self.pump_until(lambda: self.app.status_var.get() == "Activo")
        for binding_status in ("waiting", "unsupported", "disabled"):
            with self.subTest(binding_status=binding_status):
                self.app._handle_runtime_record({"event": "telemetry", "nexus_binding_status": binding_status,
                    "decision": {"action": "escape", "reason": "health is below the limit"},
                    "game_bindings": {"nexus": {"unity_code": 308, "age_ms": 2}}})
                self.assertEqual(self.app.live_vars["decision"].get(), "AutoNexus desactivado")
                self.assertNotIn("PUEDE ENVIAR", self.app.banner_var.get())
                self.assertNotIn("ESPERA", self.app.banner_var.get())
                self.assertNotIn("NO ADMITIDA", self.app.banner_var.get())
                self.assertNotIn("pendiente", self.app.live_vars["applied"].get())
                self.assertNotIn("tecla Nexus", self.app.live_hint_var.get())
                self.assertIn("AutoAim sí", self.app.live_vars["applied"].get())

    def test_debuff_telemetry_keeps_unknown_state_and_error_visible(self):
        self.app._handle_runtime_record({"event": "telemetry", "debuffs": {
            "enabled": True, "active": None, "confuse": True, "quiet": False,
            "record_quiet": True, "quiet_supported": False, "quiet_reason": QUIET_UNSUPPORTED_REASON,
            "checks": 8, "blocked": 2, "error": "Fixture pendiente"}})
        self.assertIn("Estado sin confirmar", self.app.live_vars["debuffs"].get())
        self.assertIn("Confuse sí", self.app.live_vars["debuffs"].get())
        self.assertIn("Quiet registro sí", self.app.live_vars["debuffs"].get())
        self.assertNotIn("comprobaciones", self.app.live_vars["debuffs"].get())
        self.assertNotIn("bloqueos", self.app.live_vars["debuffs"].get())
        self.assertNotIn(QUIET_UNSUPPORTED_REASON, self.app.live_vars["debuffs"].get())
        self.assertIn("Fixture pendiente", self.app.live_vars["debuffs"].get())
        self.assertIn("Esperando datos del registro", self.app.live_vars["confuse"].get())

    def test_quiet_checkbox_records_without_enabling_unsupported_protection(self):
        draft = self.app.draft
        self.assertFalse(QUIET_SUPPORTED)
        self.assertFalse(draft.record_quiet.get())
        self.assertEqual(str(self.app.quiet_check.cget("state")), "normal")
        self.assertEqual(self.app.quiet_reason_var.get(), QUIET_UNSUPPORTED_REASON)
        draft.nexus_enabled.set(False)
        draft.aim_enabled.set(False)
        draft.debuffs_enabled.set(True)
        draft.confuse.set(False)
        self.assertFalse(draft.any_module_enabled())
        self.assertEqual(str(self.app.start_button.cget("state")), "disabled")
        self.assertFalse(self.app.start_live())
        self.assertTrue(self.app.flush_configuration())
        self.app.quiet_check.invoke()
        self.assertTrue(draft.record_quiet.get())
        self.assertTrue(draft.any_module_enabled())
        self.assertFalse(draft.validate().debuffs.quiet)
        self.assertFalse(draft.validate().debuffs.has_protection)
        self.assertEqual(str(self.app.start_button.cget("state")), "normal")
        self.assertEqual(str(self.app.quiet_check.cget("state")), "normal")
        self.assertEqual(self.calls, [])

    def test_quiet_recording_alone_can_start_and_apply_live_without_enabling_protection(self):
        draft = self.app.draft
        draft.nexus_enabled.set(False)
        draft.aim_enabled.set(False)
        draft.debuffs_enabled.set(True)
        draft.confuse.set(False)
        draft.record_quiet.set(True)
        self.assertTrue(draft.any_module_enabled())
        self.assertTrue(self.app.start_live())
        self.pump_until(lambda: self.app.status_var.get() == "Activo")
        worker = self.app.runtime_thread
        self.assertTrue(self.app.applied_config.debuffs.record_quiet)
        self.assertFalse(self.app.applied_config.debuffs.has_protection)
        self.assertIn("no protege", self.app.live_vars["quiet"].get())
        draft.confuse.set(True)
        self.assertTrue(self.app.flush_configuration())
        self.pump_until(lambda: self.app.applied_config.debuffs.confuse)
        draft.blind.set(True)
        draft.confuse.set(False)
        self.assertTrue(self.app.flush_configuration())
        self.pump_until(lambda: self.app.applied_config.debuffs.blind and not self.app.applied_config.debuffs.confuse)
        draft.silenced.set(True)
        draft.blind.set(False)
        self.assertTrue(self.app.flush_configuration())
        self.pump_until(lambda: self.app.applied_config.debuffs.silenced and not self.app.applied_config.debuffs.blind)
        draft.record_quiet.set(False)
        self.assertTrue(self.app.flush_configuration())
        self.pump_until(lambda: not self.app.applied_config.debuffs.record_quiet)
        self.assertIs(worker, self.app.runtime_thread)
        self.assertEqual(len(self.calls), 1)
        self.assertFalse(json.loads(self.config_path.read_text(encoding="utf-8"))["debuffs"]["record_quiet"])

    def test_quiet_observation_shows_fresh_and_historical_facts_without_claiming_protection(self):
        observation = {"enabled": True, "available": True, "fresh": True, "age_ms": 3,
                       "revision": 1, "detections": 1,
                       "current": {"quiet": True, "mp": 0, "mp_sync": 305, "mp_override": True},
                       "last_episode": {"state": "active", "latest": {"mp": 0, "mp_sync": 305}}}
        self.app._handle_runtime_record({"event": "telemetry", "debuffs": {"quiet_observation": observation}})
        current = self.app.live_vars["quiet"].get()
        self.assertIn("Quiet detectado", current)
        self.assertIn("episodios 1", current)
        self.assertIn("MP 0", current)
        self.assertIn("MP de respaldo 305", current)
        self.assertIn("override MP sí", current)
        self.assertIn("no protege", current)
        stale = dict(observation, fresh=False, current=None, age_ms=None, error="Lectura pendiente",
                     last_episode={"state": "interrupted", "end_reason": "map_changed", "latest": {"mp": 0}})
        self.app._handle_runtime_record({"event": "telemetry", "debuffs": {"quiet_observation": stale}})
        historical = self.app.live_vars["quiet"].get()
        self.assertIn("Esperando datos recientes", historical)
        self.assertIn("histórica", historical)
        self.assertIn("interrumpido", historical)
        self.assertIn("Lectura pendiente", historical)
        self.assertNotIn("Quiet detectado", historical)
        self.app.runtime_inbox.put({"event": "quiet_observation", "quiet_observation": observation})
        self.app._consume_runtime_inbox()
        self.assertEqual(self.app.live_vars["quiet"].get(), historical)
        self.assertIn("Registro de Quiet", self.app.live_log.get("1.0", "end"))
        self.assertIn("no disponible", debuff_observation_text({"enabled": True, "available": False, "error": "Sin campo MP"}, "Quiet"))
        self.assertIn("desactivado", debuff_observation_text({"enabled": False}, "Quiet"))
        self.app._handle_runtime_record({"event": "lifecycle", "status": "finished", "cleanup_complete": True, "code": 0})
        self.assertIn("Último estado de sesión (histórico)", self.app.live_vars["quiet"].get())

    def test_confuse_observation_separates_detected_intervals_and_movement_blocks(self):
        observation = {"enabled": True, "available": True, "fresh": True, "age_ms": 3,
                       "revision": 3, "detections": 2,
                       "current": {"confuse": True, "movement_checks": 128,
                                   "movement_detections": 3, "movement_blocks": 0},
                       "last_episode": {"state": "active"}}
        self.app._handle_runtime_record({"event": "telemetry", "debuffs": {"confuse_observation": observation}})
        current = self.app.live_vars["confuse"].get()
        self.assertIn("Confuse detectado", current)
        self.assertIn("intervalos detectados 2", current)
        self.assertIn("comprobaciones 128", current)
        self.assertIn("detecciones 3", current)
        self.assertIn("bloqueos 0", current)
        self.assertNotIn("MP", current)
        self.assertNotIn("protección activa", current)
        stale = dict(observation, fresh=False, current=None, age_ms=None, last_episode={
            "state": "interrupted", "end_reason": "stale", "latest": observation["current"]})
        self.app._handle_runtime_record({"event": "telemetry", "debuffs": {"confuse_observation": stale}})
        historical = self.app.live_vars["confuse"].get()
        self.assertIn("Esperando datos recientes", historical)
        self.assertIn("histórica", historical)
        self.assertIn("interrumpido", historical)
        self.assertNotIn("Confuse detectado", historical)
        self.app.runtime_inbox.put({"event": "confuse_observation", "confuse_observation": observation})
        self.app._consume_runtime_inbox()
        self.assertEqual(self.app.live_vars["confuse"].get(), historical)
        self.assertIn("Registro de Confuse", self.app.live_log.get("1.0", "end"))
        self.app._handle_runtime_record({"event": "lifecycle", "status": "finished", "cleanup_complete": True, "code": 0})
        self.assertIn("Último estado de sesión (histórico)", self.app.live_vars["confuse"].get())

    def test_blind_observation_separates_visual_calls_from_detected_intervals(self):
        observation = {"enabled": True, "available": True, "fresh": True, "detections": 2,
                       "current": {"blind": True, "visual_checks": 250, "visual_detections": 4, "visual_blocks": 0,
                                   "movement_checks": 999, "movement_detections": 888, "movement_blocks": 777},
                       "last_episode": {"state": "active"}}
        self.app._handle_runtime_record({"event": "telemetry", "debuffs": {"blind_observation": observation}})
        current = self.app.live_vars["blind"].get()
        self.assertIn("Blind detectado", current)
        self.assertIn("intervalos detectados 2", current)
        self.assertIn("visión: comprobaciones 250", current)
        self.assertIn("detecciones 4", current)
        self.assertIn("bloqueos 0", current)
        self.assertNotIn("999", current)
        self.assertNotIn("movimiento", current)
        self.assertNotIn("MP", current)
        self.assertNotIn("protección activa", current)
        stale = dict(observation, fresh=False, current=None, last_episode={
            "state": "interrupted", "end_reason": "player_changed", "latest": observation["current"]})
        self.app._handle_runtime_record({"event": "telemetry", "debuffs": {"blind_observation": stale}})
        historical = self.app.live_vars["blind"].get()
        self.assertIn("Esperando datos recientes", historical)
        self.assertIn("histórica", historical)
        self.assertIn("interrumpido", historical)
        self.assertNotIn("Blind detectado", historical)
        self.app.runtime_inbox.put({"event": "blind_observation", "blind_observation": observation})
        self.app._consume_runtime_inbox()
        self.assertEqual(self.app.live_vars["blind"].get(), historical)
        self.assertIn("Registro de Blind", self.app.live_log.get("1.0", "end"))
        self.app._handle_runtime_record({"event": "lifecycle", "status": "finished", "cleanup_complete": True, "code": 0})
        self.assertIn("Último estado de sesión (histórico)", self.app.live_vars["blind"].get())

    def test_silenced_observation_measures_only_local_gate_consultations_and_bypasses(self):
        observation = {"enabled": True, "available": True, "fresh": True, "detections": 2,
                       "current": {"silenced": True, "condition_word1": 65536,
                                   "ability_gate_checks": 13, "ability_gate_detections": 4, "ability_gate_bypasses": 0,
                                   "visual_checks": 999, "movement_blocks": 888},
                       "last_episode": {"state": "active"}}
        self.app._handle_runtime_record({"event": "telemetry", "debuffs": {"silenced_observation": observation}})
        current = self.app.live_vars["silenced"].get()
        self.assertIn("Silenced detectado", current)
        self.assertIn("intervalos detectados 2", current)
        self.assertIn("filtro local de habilidad: consultas 13", current)
        self.assertIn("detecciones 4", current)
        self.assertIn("omisiones 0", current)
        self.assertIn("experimental; aceptación del juego pendiente", current)
        self.assertNotIn("999", current)
        self.assertNotIn("888", current)
        self.assertNotIn("habilidades usadas", current)
        self.assertNotIn("habilidad aceptada", current)
        unknown = dict(observation, current={"silenced": None, "condition_word1": 65536})
        self.assertIn("Silenced sin confirmar", debuff_observation_text(unknown, "Silenced"))
        stale = dict(observation, fresh=False, current=None, last_episode={
            "state": "interrupted", "end_reason": "player_changed", "latest": observation["current"]})
        self.app._handle_runtime_record({"event": "telemetry", "debuffs": {"silenced_observation": stale}})
        historical = self.app.live_vars["silenced"].get()
        self.assertIn("Esperando datos recientes", historical)
        self.assertIn("histórica", historical)
        self.assertNotIn("Silenced detectado", historical)
        self.app.runtime_inbox.put({"event": "silenced_observation", "silenced_observation": observation})
        self.app._consume_runtime_inbox()
        self.assertEqual(self.app.live_vars["silenced"].get(), historical)
        self.assertIn("Registro de Silenced", self.app.live_log.get("1.0", "end"))
        self.app._handle_runtime_record({"event": "lifecycle", "status": "finished", "cleanup_complete": True, "code": 0})
        self.assertIn("Último estado de sesión (histórico)", self.app.live_vars["silenced"].get())

    def test_invalid_parameters_never_start_runtime(self):
        draft = self.app.draft
        for variable, invalid, valid in ((draft.threshold, "nan", "30"),
                (draft.extrapolation, "invalid", "302"), (draft.mouse_radius, "0", "4")):
            with self.subTest(invalid=invalid), patch("client_ui.messagebox.showerror"):
                variable.set(invalid)
                self.assertFalse(self.app.start_live())
                variable.set(valid)
        self.assertEqual(self.calls, [])

    def test_live_edits_replace_acknowledged_snapshot_without_restarting_worker(self):
        draft = self.app.draft
        draft.threshold.set("60")
        draft.aim_enabled.set(True)
        draft.prioritize_bosses.set(True)
        draft.ignore_walls.set(True)
        draft.extrapolation.set("150")
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
            self.assertEqual(str(widget.cget("state")), "normal")
        worker = self.app.runtime_thread
        self.allow_apply.clear()
        draft.threshold.set("10")
        self.assertTrue(self.app.flush_configuration())
        self.assertEqual(config.threshold, .6)
        self.assertEqual(self.app.applied_config, config)
        self.assertIsNotNone(self.app.pending_revision)
        self.allow_apply.set()
        self.pump_until(lambda: self.app.applied_config.threshold == .1)
        self.assertIs(self.app.runtime_thread, worker)
        self.assertEqual(len(self.calls), 1)
        self.assertTrue(all(identity != threading.get_ident() for identity in self.worker_ids))
        self.assertEqual(json.loads(self.config_path.read_text(encoding="utf-8"))["threshold"], .1)

    def test_applied_status_stays_visible_while_visiting_configuration_tabs(self):
        self.app.draft.detect_game_keys.set(False)
        self.app.refresh_game_bindings()
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
        self.pump_until(lambda: self.app.status_var.get() == "Activo")
        self.app.close()
        self.assertTrue(self.app.stop_event.is_set())
        self.assertIsNotNone(self.app.runtime_thread)
        self.assertFalse(self.app.cleanup_complete)
        self.assertTrue(self.root.winfo_exists())
        self.release_cleanup.set()
        self.pump_until(lambda: self.app.runtime_thread is None)
        self.assertTrue(self.app.cleanup_complete)

    def test_cleanup_failure_keeps_error_and_blocks_restart_and_install(self):
        def incomplete(config, *, active, stop_event, on_update, control):
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

    def test_fresh_runtime_bindings_update_applied_key_but_never_edit_manual_draft(self):
        self.assertTrue(self.app.start_live())
        self.pump_until(lambda: self.app.status_var.get() == "Activo")
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

    def test_autosave_debounces_joint_edits_and_keeps_last_valid_file_for_incomplete_input(self):
        from client_ui import save_config
        draft = self.app.draft
        with patch("client_ui.save_config", wraps=save_config) as save, patch("client_ui.messagebox.showerror") as popup:
            draft.threshold.set("42")
            draft.prediction_enabled.set(True)
            draft.extrapolation.set("")
            self.pump_until(lambda: "Sin guardar" in self.app.draft_status_var.get())
            save.assert_not_called()
            popup.assert_not_called()
            self.assertEqual(self.config_path.read_text(encoding="utf-8"), self.initial_config)
            draft.extrapolation.set("200")
            draft.aim_enabled.set(True)
            self.pump_until(lambda: self.app._autosave_after is None and not self.app._draft_dirty)
            self.assertEqual(save.call_count, 1)
        saved = json.loads(self.config_path.read_text(encoding="utf-8"))
        self.assertEqual(saved["threshold"], .42)
        self.assertEqual(saved["autoaim"]["extrapolation"], 200)
        self.assertTrue(saved["autoaim"]["enabled"])
        self.assertTrue(saved["prediction_enabled"])
        self.assertEqual(self.calls, [])

    def test_reading_automatic_key_preferences_does_not_change_or_save_manual_draft(self):
        self.bindings["nexus"] = GameBinding("nexus", "R", 114, "fixture")
        self.app.refresh_game_bindings()
        self.assertIn("Nexus: R", self.app.nexus_binding_var.get())
        self.assertEqual(self.app.draft.nexus_key.get(), "CTRL")
        self.assertIsNone(self.app._autosave_after)
        self.assertFalse(self.app._draft_dirty)
        self.assertEqual(self.config_path.read_text(encoding="utf-8"), self.initial_config)

    def test_pending_live_revisions_coalesce_and_old_acknowledgements_cannot_clear_newer_work(self):
        self.assertTrue(self.app.start_live())
        self.pump_until(lambda: self.app.status_var.get() == "Activo")
        self.allow_apply.clear()
        self.app.draft.threshold.set("40")
        first = self.app.flush_configuration()
        first_revision = self.app.pending_revision
        self.app.draft.threshold.set("50")
        second = self.app.flush_configuration()
        second_revision = self.app.pending_revision
        self.assertGreater(second_revision, first_revision)
        self.app._handle_runtime_record({"event": "config_applied", "revision": first_revision,
                                        "configuration": config_to_mapping(first)})
        self.assertEqual(self.app.applied_config.threshold, .4)
        self.assertEqual(self.app.pending_revision, second_revision)
        self.assertIn("pendiente", self.app.draft_status_var.get())
        self.app._handle_runtime_record({"event": "config_applied", "revision": 0,
                                        "configuration": config_to_mapping(self.calls[0][0])})
        self.assertEqual(self.app.applied_config.threshold, .4)
        self.allow_apply.set()
        self.pump_until(lambda: self.app.pending_revision is None)
        self.assertEqual(self.requests, [(second_revision, second)])
        self.assertEqual(self.app.applied_config.threshold, .5)
        self.assertEqual(len(self.calls), 1)

    def test_all_modules_off_is_applied_then_stops_and_reenabling_does_not_autostart(self):
        self.assertTrue(self.app.start_live())
        self.pump_until(lambda: self.app.status_var.get() == "Activo")
        self.release_cleanup.set()
        self.app.draft.nexus_enabled.set(False)
        self.assertTrue(self.app.flush_configuration())
        self.pump_until(lambda: self.app.runtime_thread is None)
        self.assertFalse(self.app.applied_config.autonexus_enabled)
        self.assertTrue(self.app.cleanup_complete)
        self.app.draft.aim_enabled.set(True)
        self.assertTrue(self.app.flush_configuration())
        self.assertIsNone(self.app.runtime_thread)
        self.assertEqual(len(self.calls), 1)
        self.assertTrue(self.app.start_live())
        self.pump_until(lambda: len(self.calls) == 2)

    def test_start_flushes_current_draft_and_cancels_its_delayed_save(self):
        self.app.draft.threshold.set("63")
        self.assertIsNotNone(self.app._autosave_after)
        self.assertTrue(self.app.start_live())
        self.assertIsNone(self.app._autosave_after)
        self.pump_until(lambda: self.app.status_var.get() == "Activo")
        self.assertEqual(self.calls[0][0].threshold, .63)
        self.assertEqual(json.loads(self.config_path.read_text(encoding="utf-8"))["threshold"], .63)

    def test_close_flushes_valid_pending_edits_without_starting_any_worker(self):
        self.app.draft.threshold.set("67")
        self.app.close()
        self.assertIsNone(self.app._autosave_after)
        self.assertEqual(json.loads(self.config_path.read_text(encoding="utf-8"))["threshold"], .67)
        self.assertEqual(self.calls, [])

    def test_close_with_incomplete_input_keeps_previous_valid_file(self):
        self.app.draft.threshold.set("65")
        self.app.flush_configuration()
        valid = self.config_path.read_bytes()
        self.app.draft.threshold.set("")
        with patch("client_ui.messagebox.showerror") as popup:
            self.app.close()
            popup.assert_not_called()
        self.assertEqual(self.config_path.read_bytes(), valid)

    def test_save_permission_error_never_submits_unpersisted_live_configuration(self):
        self.assertTrue(self.app.start_live())
        self.pump_until(lambda: self.app.status_var.get() == "Activo")
        old = self.app.applied_config
        self.app.draft.threshold.set("45")
        with patch("client_ui.save_config", side_effect=PermissionError("Archivo ocupado")), patch("client_ui.messagebox.showerror") as popup:
            self.assertIsNone(self.app.flush_configuration())
            popup.assert_not_called()
        self.assertEqual(self.app.applied_config, old)
        self.assertIsNone(self.app.pending_revision)
        self.assertEqual(self.requests, [])
        self.assertIn("Archivo ocupado", self.app.draft_status_var.get())
        self.assertIsNotNone(self.app.runtime_thread)

    def test_failed_live_config_ack_clears_claimed_state_and_requests_cleanup(self):
        self.assertTrue(self.app.start_live())
        self.pump_until(lambda: self.app.status_var.get() == "Activo")
        self.app._handle_runtime_record({"event": "config_error", "revision": 1,
                                        "reason": "configure_failed", "error": "No hubo confirmación"})
        self.assertIsNone(self.app.applied_config)
        self.assertTrue(self.app.stop_event.is_set())
        self.assertIn("sin confirmar", self.app.live_vars["applied"].get())
        self.assertIn("SIN CONFIRMAR", self.app.banner_var.get())

    def test_autoaim_telemetry_preserves_unknown_autofire_and_boss_identity(self):
        for boss, expected in ((True, "Boss"), (False, "Enemigo"), (None, "Tipo sin confirmar"), (1, "Tipo sin confirmar")):
            with self.subTest(boss=boss):
                self.app._handle_runtime_record({"event": "telemetry", "autoaim": {
                    "enabled": True, "active": False, "autofire": None, "target_id": 0, "target_is_boss": boss}})
                text = self.app.live_vars["autoaim"].get()
                self.assertIn(expected, text)
                self.assertIn("sin confirmar", text)
                self.assertIn("Sin intervención", text)
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
