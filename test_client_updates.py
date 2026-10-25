"""Offline update-panel checks: no GitHub calls, replacement or game access."""
from types import SimpleNamespace
import threading
import time
import tkinter as tk
import unittest
from unittest.mock import patch

from client_updates import UpdatePanel
import update_core


class FakeUpdates:
    def __init__(self):
        self.release = SimpleNamespace(version="9.0.0", notes="Una mejora de prueba.",
                                       html_url="https://github.com/matihdz/MiRealmBot/releases/tag/v9.0.0")
        self.calls = []
        self.worker_ids = []
        self.available = True
        self.check_effect = None
        self.prepare_effect = None
        self.prepared = SimpleNamespace(release=self.release)

    def check_for_update(self, current_version, *, cancel_event):
        self.calls.append(("check", current_version))
        self.worker_ids.append(threading.get_ident())
        if self.check_effect:
            return self.check_effect(cancel_event)
        return SimpleNamespace(release=self.release, available=self.available)

    def prepare_update(self, release, *, cancel_event, progress):
        self.calls.append(("prepare", release))
        self.worker_ids.append(threading.get_ident())
        if self.prepare_effect:
            return self.prepare_effect(cancel_event, progress)
        progress("Descargando", 30, 30)
        return self.prepared


class UpdatePanelTests(unittest.TestCase):
    def setUp(self):
        try:
            self.root = tk.Tk()
        except tk.TclError as error:
            self.skipTest(f"Tk no disponible: {error}")
        self.root.withdraw()
        self.service = FakeUpdates()
        self.allowed = (True, "")
        self.install_calls = []
        self.links = []
        self.release_events = []
        self.panel = UpdatePanel(self.root, service=self.service, portable=True,
            current_version="1.0.0", can_install=lambda: self.allowed,
            request_install=lambda value: self.install_calls.append(
                (value, threading.get_ident())), open_url=lambda value: self.links.append(value))
        self.panel.pack(fill="both", expand=True)
        self.root.update()

    def tearDown(self):
        if not hasattr(self, "panel"):
            return
        for event in self.release_events:
            event.set()
        self.panel.close()
        self.pump_until(lambda: not self.panel.busy)
        self.root.destroy()

    def pump_until(self, condition, timeout=2):
        deadline = time.monotonic() + timeout
        while not condition() and time.monotonic() < deadline:
            self.root.update()
            self.panel.tick()
            time.sleep(.005)
        self.assertTrue(condition())

    def checked(self):
        self.assertTrue(self.panel.check())
        self.pump_until(lambda: not self.panel.busy)

    def downloaded(self):
        self.checked()
        self.assertTrue(self.panel.download())
        self.pump_until(lambda: not self.panel.busy)

    def test_constructing_panel_is_offline_and_current_release_cannot_download(self):
        self.assertEqual(self.service.calls, [])
        self.assertFalse(self.panel.download())
        self.assertFalse(self.panel.install())
        self.assertEqual(self.install_calls, [])
        self.service.available = False
        self.checked()
        self.assertIn("más reciente", self.panel.status_var.get())
        self.assertEqual(str(self.panel.download_button.cget("state")), "disabled")
        self.assertFalse(self.panel.download())
        self.assertTrue(self.panel.open_release())
        self.assertEqual(self.links, [self.service.release.html_url])

    def test_startup_check_is_deferred_and_never_runs_on_the_tk_thread(self):
        self.panel.destroy()
        with patch("client_updates.time.monotonic", return_value=10):
            self.panel = UpdatePanel(self.root, service=self.service, portable=True,
                current_version="1.0.0", can_install=lambda: self.allowed,
                request_install=lambda value: True, auto_check=True)
            self.assertEqual(self.panel._auto_check_at, 11.5)
            self.panel.tick()
            self.assertEqual(self.service.calls, [])
        with patch("client_updates.time.monotonic", return_value=12):
            self.panel.tick()
        self.pump_until(lambda: not self.panel.busy)
        self.assertEqual(self.service.calls, [("check", "1.0.0")])
        self.assertNotEqual(self.service.worker_ids[0], threading.get_ident())
        self.panel.tick()
        self.assertEqual(len(self.service.calls), 1)

    def test_check_displays_release_notes_and_github_link_without_downloading(self):
        self.checked()
        self.assertTrue(self.panel.available)
        self.assertIn("9.0.0", self.panel.release_var.get())
        self.assertIn("mejora", self.panel.notes.get("1.0", "end"))
        self.assertEqual(str(self.panel.install_button.cget("state")), "disabled")
        self.assertEqual(len(self.service.calls), 1)
        self.assertEqual(self.install_calls, [])

    def test_source_mode_offers_manual_link_and_cannot_prepare_or_install(self):
        self.panel.portable = False
        self.checked()
        self.assertFalse(self.panel.download())
        self.assertEqual(str(self.panel.download_button.cget("state")), "disabled")
        self.assertTrue(self.panel.open_release())
        self.assertEqual(self.links, [self.service.release.html_url])
        self.panel.prepared = self.service.prepared
        self.assertFalse(self.panel.install())
        self.assertEqual(self.install_calls, [])

    def test_progress_is_bounded_and_download_never_installs_automatically(self):
        progress_ready, release = threading.Event(), threading.Event()
        self.release_events.append(release)
        def prepare(cancel, progress):
            for index in range(5000):
                progress("Descargando", index, 10000)
            progress_ready.set()
            release.wait(2)
            return self.service.prepared
        self.service.prepare_effect = prepare
        self.checked()
        self.assertTrue(self.panel.download())
        self.assertTrue(progress_ready.wait(1))
        self.panel.tick()
        self.assertIsNone(self.panel._latest_progress)
        self.assertAlmostEqual(float(self.panel.progress_bar.cget("value")), 49.99)
        self.assertFalse(self.panel.check())
        self.assertFalse(self.panel.install())
        release.set()
        self.pump_until(lambda: not self.panel.busy)
        self.assertIs(self.panel.prepared, self.service.prepared)
        self.assertEqual(self.install_calls, [])

    def test_install_checks_current_runtime_state_and_callback_runs_on_tk_thread(self):
        self.downloaded()
        self.allowed = (False, "Detén AutoNexus y espera la desconexión.")
        self.panel.tick()
        self.assertEqual(str(self.panel.install_button.cget("state")), "disabled")
        self.assertFalse(self.panel.install())
        self.assertEqual(self.install_calls, [])
        self.assertIn("desconexión", self.panel.status_var.get())
        self.allowed = (True, "")
        self.panel.tick()
        self.assertTrue(self.panel.install())
        self.assertTrue(self.panel.installing)
        self.assertEqual(self.install_calls, [(self.service.prepared, threading.get_ident())])
        self.assertFalse(self.panel.check())

    def test_failed_install_keeps_prepared_download_and_supports_retry(self):
        self.downloaded()
        def fail(value):
            raise OSError("No se pudo abrir el actualizador.")
        self.panel.request_install = fail
        self.assertFalse(self.panel.install())
        self.assertFalse(self.panel.installing)
        self.assertIs(self.panel.prepared, self.service.prepared)
        self.assertIn("actualizador", self.panel.status_var.get())
        self.panel.request_install = lambda value: True
        self.assertTrue(self.panel.install())

    def test_network_failure_is_visible_and_can_retry(self):
        def fail(cancel):
            raise update_core.UpdateError("La conexión se interrumpió.")
        self.service.check_effect = fail
        self.checked()
        self.assertIn("Sin conexión", self.panel.status_var.get())
        self.assertIn("conexión se interrumpió", self.panel.status_var.get())
        self.assertFalse(self.panel.available)
        self.service.check_effect = None
        self.checked()
        self.assertTrue(self.panel.available)

    def test_close_waits_for_actual_worker_cleanup_after_cancellation(self):
        cancel_seen, release = threading.Event(), threading.Event()
        self.release_events.append(release)
        def check(cancel):
            cancel.wait(2)
            cancel_seen.set()
            release.wait(2)
            raise update_core.UpdateCancelled("Cancelada")
        self.service.check_effect = check
        self.assertTrue(self.panel.check())
        self.assertFalse(self.panel.close())
        self.assertTrue(cancel_seen.wait(1))
        self.assertTrue(self.panel.busy)
        self.assertFalse(self.panel.check())
        self.assertTrue(self.root.winfo_exists())
        release.set()
        self.pump_until(lambda: not self.panel.busy)
        self.assertTrue(self.panel.close())

    def test_cancel_button_recovers_after_worker_has_stopped(self):
        def check(cancel):
            cancel.wait(2)
            raise update_core.UpdateCancelled("Cancelada")
        self.service.check_effect = check
        self.assertTrue(self.panel.check())
        self.panel.cancel()
        self.pump_until(lambda: not self.panel.busy)
        self.assertIn("cancelada", self.panel.status_var.get())
        self.assertEqual(str(self.panel.check_button.cget("state")), "normal")


if __name__ == "__main__":
    unittest.main()
