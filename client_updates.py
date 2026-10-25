"""Tk update panel; network work owns no Tk objects and never runs on its thread.

The application calls ``tick`` from its existing Tk timer and ``close`` while
closing.  ``close`` requests cancellation and returns False until the network
worker has actually stopped.  Installing is delegated to the application so it
can enforce runtime cleanup before starting the replacement process.
"""
from __future__ import annotations

import queue
import sys
import threading
import time
import tkinter as tk
from tkinter import ttk
import webbrowser

from app_version import VERSION
import update_core


PANEL = "#121e30"
TEXT = "#edf3fc"


class UpdatePanel(ttk.Frame):
    """One worker at a time, with explicit download and install buttons.

    ``can_install()`` returns (allowed, explanation). ``request_install`` must
    start the prepared update and arrange application shutdown only on success;
    returning False or raising keeps this panel available for another attempt.
    Both callbacks run on the Tk thread.  Tests can inject an update service,
    browser opener and portable mode without making real network calls.
    """

    def __init__(self, parent, *, can_install, request_install, service=None,
                 current_version=VERSION, portable=None, auto_check=False,
                 open_url=None):
        super().__init__(parent, padding=24)
        self.service = service if service is not None else update_core
        self.current_version = current_version
        self.portable = bool(getattr(sys, "frozen", False)) if portable is None else portable
        self.can_install = can_install
        self.request_install = request_install
        self.open_url = open_url if open_url is not None else webbrowser.open
        self.release = None
        self.available = False
        self.prepared = None
        self.installing = False
        self._closing = False
        self._thread = None
        self._cancel = None
        self._messages = queue.Queue(maxsize=2)
        self._progress_lock = threading.Lock()
        self._latest_progress = None
        self._auto_check_at = time.monotonic() + 1.5 if auto_check else None
        self.status_var = tk.StringVar(self, "Todavía no se han buscado actualizaciones.")
        self.release_var = tk.StringVar(self, "Versión disponible: —")
        self.progress_var = tk.StringVar(self, "")
        self.install_hint_var = tk.StringVar(self, "")
        self._build()
        self._refresh_buttons()

    @property
    def busy(self):
        # Ownership lasts until tick/close has joined the stopped thread.
        return self._thread is not None

    @property
    def worker_thread(self):
        return self._thread

    def _build(self):
        self.columnconfigure(0, weight=1)
        self.rowconfigure(6, weight=1)
        ttk.Label(self, text="Actualizaciones", style="Subtitle.TLabel").grid(
            row=0, column=0, sticky="w")
        ttk.Label(self, text=f"Tu versión: {self.current_version}",
                  style="Muted.TLabel").grid(row=1, column=0, sticky="w", pady=(8, 3))
        ttk.Label(self, textvariable=self.release_var).grid(row=2, column=0, sticky="w")
        ttk.Label(self, textvariable=self.status_var, wraplength=850).grid(
            row=3, column=0, sticky="ew", pady=(12, 10))
        buttons = ttk.Frame(self)
        buttons.grid(row=4, column=0, sticky="ew")
        self.check_button = ttk.Button(buttons, text="Buscar actualizaciones", command=self.check)
        self.check_button.pack(side="left", padx=(0, 8))
        self.download_button = ttk.Button(buttons, text="Descargar actualización",
                                         command=self.download)
        self.download_button.pack(side="left", padx=(0, 8))
        self.install_button = ttk.Button(buttons, text="Instalar y reiniciar",
                                        command=self.install, style="Accent.TButton")
        self.install_button.pack(side="left", padx=(0, 8))
        self.cancel_button = ttk.Button(buttons, text="Cancelar", command=self.cancel)
        self.cancel_button.pack(side="left")
        progress = ttk.Frame(self)
        progress.grid(row=5, column=0, sticky="ew", pady=(12, 14))
        progress.columnconfigure(0, weight=1)
        self.progress_bar = ttk.Progressbar(progress, mode="determinate", maximum=100)
        self.progress_bar.grid(row=0, column=0, sticky="ew")
        ttk.Label(progress, textvariable=self.progress_var, style="Muted.TLabel").grid(
            row=1, column=0, sticky="w", pady=(4, 0))
        notes_frame = ttk.Frame(self)
        notes_frame.grid(row=6, column=0, sticky="nsew")
        notes_frame.columnconfigure(0, weight=1)
        notes_frame.rowconfigure(1, weight=1)
        ttk.Label(notes_frame, text="Novedades de la versión", style="Muted.TLabel").grid(
            row=0, column=0, sticky="w", pady=(0, 6))
        self.notes = tk.Text(notes_frame, wrap="word", height=10, background=PANEL,
                             foreground=TEXT, insertbackground=TEXT, relief="flat",
                             padx=12, pady=12, font=("Segoe UI", 10), state="disabled")
        self.notes.grid(row=1, column=0, sticky="nsew")
        scrollbar = ttk.Scrollbar(notes_frame, orient="vertical", command=self.notes.yview)
        scrollbar.grid(row=1, column=1, sticky="ns")
        self.notes.configure(yscrollcommand=scrollbar.set)
        footer = ttk.Frame(self)
        footer.grid(row=7, column=0, sticky="ew", pady=(14, 0))
        footer.columnconfigure(0, weight=1)
        self.release_button = ttk.Button(footer, text="Ver descarga en GitHub",
                                        command=self.open_release)
        self.release_button.grid(row=0, column=0, sticky="w")
        ttk.Label(footer, textvariable=self.install_hint_var, style="Muted.TLabel",
                  wraplength=850).grid(row=1, column=0, sticky="ew", pady=(8, 0))
        if not self.portable:
            ttk.Label(footer, text="Estás usando el código fuente. Puedes consultar las novedades "
                      "y descargar el programa portátil desde GitHub.", style="Muted.TLabel",
                      wraplength=850).grid(row=2, column=0, sticky="ew", pady=(8, 0))

    def _set_notes(self, text):
        self.notes.configure(state="normal")
        self.notes.delete("1.0", "end")
        self.notes.insert("1.0", str(text or "Esta versión no incluye notas.")[:12000])
        self.notes.configure(state="disabled")

    def _install_allowed(self):
        if not self.portable:
            return False, "La instalación desde la interfaz requiere la versión portátil."
        try:
            allowed, reason = self.can_install()
            return bool(allowed), str(reason or "")
        except Exception:
            return False, "No se pudo confirmar que el juego esté desconectado del bot."

    def _refresh_buttons(self):
        idle = not self.busy and not self.installing and not self._closing
        available = self.release is not None
        self.check_button.configure(state="normal" if idle else "disabled")
        self.download_button.configure(state="normal" if idle and self.available and self.portable
                                       and self.prepared is None else "disabled")
        allowed, reason = self._install_allowed()
        self.install_button.configure(state="normal" if idle and self.prepared is not None
                                      and allowed else "disabled")
        cancelling = self._cancel is not None and self._cancel.is_set()
        self.cancel_button.configure(state="normal" if self.busy and not self._closing and not cancelling
                                     else "disabled")
        self.release_button.configure(state="normal" if available and not self._closing
                                      else "disabled")
        if self.prepared is not None and not allowed:
            self.install_hint_var.set(reason or "Detén el bot antes de instalar.")
        elif self.prepared is not None:
            self.install_hint_var.set("Lista para instalar. MiRealmBot se cerrará y volverá a abrirse; "
                                      "conservará tu configuración y tus registros.")
        else:
            self.install_hint_var.set("")

    def _start(self, operation, function):
        if self.busy or self.installing or self._closing:
            return False
        self._auto_check_at = None
        self._cancel = threading.Event()
        self.progress_bar.configure(value=0)
        self.progress_var.set("")
        with self._progress_lock:
            self._latest_progress = None
        cancel_event = self._cancel

        def work():
            try:
                value = function(cancel_event)
                self._messages.put(("result", operation, value))
            except update_core.UpdateCancelled:
                self._messages.put(("cancelled", operation, None))
            except Exception as error:
                self._messages.put(("error", operation, str(error)))

        self._thread = threading.Thread(target=work, name=f"MiRealmBot-update-{operation}",
                                        daemon=False)
        self._thread.start()
        self._refresh_buttons()
        return True

    def check(self):
        if self.busy or self.installing or self._closing:
            return False
        self.release = None
        self.available = False
        self.prepared = None
        self.release_var.set("Versión disponible: —")
        self._set_notes("Buscando novedades…")
        self.status_var.set("Buscando actualizaciones en GitHub…")
        return self._start("check", lambda cancel: self.service.check_for_update(
            self.current_version, cancel_event=cancel))

    def _receive_progress(self, phase, received, total):
        # Called only by the worker. A single replaceable slot bounds progress.
        with self._progress_lock:
            self._latest_progress = (str(phase), int(received), int(total))

    def download(self):
        if not self.portable or not self.available or self.release is None or self.prepared is not None:
            return False
        if self.busy or self.installing or self._closing:
            return False
        release = self.release
        self.status_var.set("Descargando la actualización. Puedes cancelarla.")
        return self._start("download", lambda cancel: self.service.prepare_update(
            release, cancel_event=cancel, progress=self._receive_progress))

    def cancel(self):
        if self._cancel is not None and self.busy:
            self._cancel.set()
            self.status_var.set("Cancelando… espera a que termine la descarga o consulta.")
            self.cancel_button.configure(state="disabled")

    def open_release(self):
        if self.release is None or self._closing:
            return False
        try:
            if self.open_url(self.release.html_url) is False:
                raise RuntimeError("No se pudo abrir el navegador.")
            return True
        except Exception:
            self.status_var.set("No se pudo abrir el navegador. Busca MiRealmBot en GitHub "
                                "para descargarlo manualmente.")
            return False

    def install(self):
        if self.prepared is None or self.busy or self.installing or self._closing:
            return False
        allowed, reason = self._install_allowed()
        if not allowed:
            self.status_var.set(reason or "Detén el bot y espera a que termine antes de instalar.")
            self._refresh_buttons()
            return False
        self.installing = True
        self.status_var.set("Preparando el reinicio de MiRealmBot…")
        self._refresh_buttons()
        try:
            if self.request_install(self.prepared) is False:
                self.recover_install_error("No se pudo iniciar la instalación. Puedes intentarlo otra vez.")
                return False
        except Exception as error:
            self.recover_install_error(str(error))
            return False
        return True

    def recover_install_error(self, message):
        self.installing = False
        self.status_var.set("No se pudo iniciar la actualización. " + str(message)[:600])
        self._refresh_buttons()

    def tick(self):
        with self._progress_lock:
            progress, self._latest_progress = self._latest_progress, None
        if progress is not None and not self._closing:
            phase, received, total = progress
            value = max(0, min(100, received * 100 / total)) if total > 0 else 0
            self.progress_bar.configure(value=value)
            amount = f"{received / 1_000_000:.1f} / {total / 1_000_000:.1f} MB" if total > 0 else ""
            self.progress_var.set(f"{phase[:120]} · {amount}".strip(" ·"))
        while True:
            try:
                kind, operation, value = self._messages.get_nowait()
            except queue.Empty:
                break
            if self._closing:
                continue
            if kind == "cancelled":
                self.status_var.set("Operación cancelada. Puedes volver a intentarlo.")
                self.progress_var.set("")
                self.progress_bar.configure(value=0)
            elif kind == "error":
                prefix = "Sin conexión o no se pudo consultar GitHub. " if operation == "check" else "No se pudo descargar la actualización. "
                self.status_var.set(prefix + str(value)[:600])
                self.progress_var.set("")
                self.progress_bar.configure(value=0)
            elif operation == "check":
                self.release_var.set(f"Última versión publicada: {value.release.version}")
                self._set_notes(value.release.notes)
                if value.available:
                    self.release = value.release
                    self.status_var.set("Hay una actualización disponible." if self.portable else
                                        "Hay una actualización disponible. Descarga el programa portátil desde GitHub.")
                else:
                    # Keep the release link useful even when already current.
                    self.release = value.release
                    self.status_var.set("Ya tienes la versión más reciente.")
                self.available = bool(value.available)
            else:
                self.prepared = value
                self.status_var.set("Descarga completada y comprobada. Pulsa «Instalar y reiniciar» cuando estés listo.")
                self.progress_bar.configure(value=100)
        if self._thread is not None and not self._thread.is_alive():
            self._thread.join()
            self._thread = None
            self._cancel = None
        self._refresh_buttons()
        if self._auto_check_at is not None and time.monotonic() >= self._auto_check_at:
            self._auto_check_at = None
            if not self._closing:
                self.check()

    def close(self):
        self._closing = True
        self._auto_check_at = None
        if self._cancel is not None:
            self._cancel.set()
        self.tick()
        return not self.busy
