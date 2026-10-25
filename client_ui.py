"""Tk configuration and live status. Opening the client never starts the bot.

One draft owns editable values; each worker receives an immutable validated
snapshot. Game access, input, logs and cleanup belong to the runtime worker.
"""
from __future__ import annotations

from collections import deque
from datetime import datetime
import math
from pathlib import Path
import queue
import threading
import tkinter as tk
from tkinter import messagebox, ttk

from app_paths import APP_ROOT
from app_version import VERSION
from autonexus_core import AutoNexus
from client_updates import UpdatePanel
from game_settings import read_game_bindings, unity_key_name
from live_autonexus import config_from_mapping, load_config, run_runtime, save_config
from update_core import launch_update
from windows_nexus import normalize_nexus_key


BG, PANEL, SURFACE, BORDER = "#0b1220", "#121e30", "#17253a", "#293a51"
TEXT, MUTED, TEAL, BLUE, AMBER, RED = "#edf3fc", "#9aacc4", "#4cdec2", "#75a9ff", "#ffc978", "#ff8f98"
REASONS = {
    "both health sources are at or above the limit": "Vida por encima del límite",
    "health is below the limit": "Vida bajo el límite",
    "health is below the limit; exit requested": "Vida baja · solicitar salida",
    "one health source is below the limit": "Vida bajo el límite",
    "escape already requested for this map": "Salida ya solicitada en este mapa",
    "exit was already requested for this map": "Salida ya solicitada en este mapa",
    "health data is unknown": "Esperando una lectura válida de vida",
    "map state is unknown": "Esperando la identidad del mapa",
    "player gates are unknown": "Filtros de estado sin datos",
    "map is excluded": "Mapa excluido", "excluded map": "Mapa excluido",
    "missing health observation": "Sin una lectura de vida válida",
    "autonexus is disabled": "AutoNexus desactivado", "AutoNexus is disabled": "AutoNexus desactivado",
    "a player gate blocks AutoNexus": "Un estado del jugador bloquea la salida",
}


def display_text(value, limit=600):
    text = str(value)
    return text if len(text) <= limit else text[:limit - 1] + "…"


def short_reason(value):
    return REASONS.get(str(value), display_text(value or "Sin decisión"))


def number(value):
    return "—" if value is None else str(value)


class ConfigDraft:
    """All editable values, validated together through the runtime contracts."""
    def __init__(self, master, config):
        self._fixed = {"excluded_maps": list(config.excluded_maps),
                       "gameassembly_sha256": config.gameassembly_sha256,
                       "use_state_filters": config.use_state_filters}
        self.threshold = tk.StringVar(master, f"{config.threshold * 100:g}")
        self.prediction_enabled = tk.BooleanVar(master, config.prediction_enabled)
        self.nexus_key = tk.StringVar(master, config.nexus_key)
        self.detect_game_keys = tk.BooleanVar(master, config.detect_game_keys)
        self.aim_enabled = tk.BooleanVar(master, config.autoaim.enabled)
        self.extrapolation = tk.StringVar(master, f"{config.autoaim.extrapolation:g}")
        self.mouse_radius = tk.StringVar(master, f"{config.autoaim.mouse_radius:g}")
        self.prioritize_bosses = tk.BooleanVar(master, config.autoaim.prioritize_bosses)
        self.ignore_walls = tk.BooleanVar(master, config.autoaim.ignore_walls)

    @property
    def variables(self):
        return (self.threshold, self.prediction_enabled, self.nexus_key, self.detect_game_keys,
                self.aim_enabled, self.extrapolation, self.mouse_radius, self.prioritize_bosses, self.ignore_walls)

    @staticmethod
    def _number(variable, label):
        try:
            return float(variable.get().replace(",", "."))
        except (ValueError, tk.TclError):
            raise ValueError(f"{label} debe ser un número válido") from None

    def threshold_percent(self):
        value = self._number(self.threshold, "El porcentaje")
        if not math.isfinite(value) or not 1 <= value <= 99:
            raise ValueError("El porcentaje debe estar entre 1 y 99")
        return value

    def validate(self):
        return config_from_mapping(dict(self._fixed, threshold=self.threshold_percent() / 100,
            prediction_enabled=self.prediction_enabled.get(), nexus_key=self.nexus_key.get(),
            detect_game_keys=self.detect_game_keys.get(), autoaim={
                "enabled": self.aim_enabled.get(), "mode": "close_to_mouse", "activation": "autofire",
                "extrapolation": self._number(self.extrapolation, "Extrapolation"),
                "mouse_radius": self._number(self.mouse_radius, "Radio junto al cursor"),
                "prioritize_bosses": self.prioritize_bosses.get(), "ignore_walls": self.ignore_walls.get()}))


class RuntimeInbox:
    """Bound replaceable telemetry; retain lifecycle/errors until Tk consumes them."""
    def __init__(self):
        self.critical = queue.Queue(maxsize=128)
        self.lock = threading.Lock()
        self.latest = None
        self.notes = deque(maxlen=160)
        self.last_damage = None

    def put(self, record):
        kind = record.get("event")
        if kind in ("lifecycle", "error", "cleanup", "worker_done", "worker_failure", "logging_warning"):
            self.critical.put(record)
        else:
            with self.lock:
                if kind == "telemetry":
                    self.latest = record
                if kind == "prediction_transport":
                    for event in record.get("events", []):
                        if event.get("kind") == "damage":
                            self.last_damage = event.get("amount")
                            self.notes.append("Golpe efectivo: " + display_text(event.get("amount"), 80) + " HP")
                if kind == "decision" and (record.get("delivery") or {}).get("status") not in (None, "none"):
                    self.notes.append("Salida: " + display_text(record["delivery"].get("status"), 160))

    def drain(self):
        critical = []
        while True:
            try:
                critical.append(self.critical.get_nowait())
            except queue.Empty:
                break
        with self.lock:
            latest, notes, damage = self.latest, list(self.notes), self.last_damage
            self.latest = None
            self.notes.clear()
        return critical, latest, notes, damage


class App:
    def __init__(self, root, *, config_path=None, runtime_runner=None, settings_reader=None,
                 auto_check_updates=False, update_service=None, portable_updates=None):
        self.root = root
        self.config_path = Path(config_path or APP_ROOT / "autonexus.json")
        self.runtime_runner = runtime_runner or run_runtime
        self.settings_reader = settings_reader or read_game_bindings
        self.draft = ConfigDraft(root, load_config(self.config_path))
        self.runtime_thread = None
        self.stop_event = None
        self.runtime_inbox = RuntimeInbox()
        self.cleanup_complete = True
        self.closing = False
        self._runtime_finished_seen = False
        self._tick_after = None
        self.applied_config = None
        self.applied_key = None
        self.nexus_binding_status = None
        self.last_error = None
        self.status_var = tk.StringVar(root, "Detenido")
        self.banner_var = tk.StringVar(root)
        self.footer_var = tk.StringVar(root, "Abrir MiRealmBot no inicia el bot · Las actualizaciones consultan GitHub")
        self.draft_status_var = tk.StringVar(root, "Los cambios se aplican al próximo inicio de sesión.")
        self.nexus_binding_var = tk.StringVar(root)
        self.autofire_binding_var = tk.StringVar(root)
        self.config_widgets = []
        self._style()
        self._build(auto_check_updates, update_service, portable_updates)
        for variable in self.draft.variables:
            variable.trace_add("write", self._draft_changed)
        self.root.protocol("WM_DELETE_WINDOW", self.close)
        self.refresh_game_bindings()
        self._draft_changed()
        self._schedule_tick()

    def _style(self):
        self.root.title(f"MiRealmBot {VERSION} · AutoNexus / AutoAim")
        self.root.geometry("1100x850")
        self.root.minsize(940, 740)
        self.root.configure(bg=BG)
        self.root.option_add("*Font", "{Segoe UI} 10")
        style = ttk.Style(self.root)
        style.theme_use("clam")
        style.configure(".", background=BG, foreground=TEXT, font=("Segoe UI", 10),
                        bordercolor=BORDER, lightcolor=BORDER, darkcolor=BORDER)
        for name, background in (("TFrame", BG), ("Panel.TFrame", PANEL), ("Card.TFrame", SURFACE)):
            style.configure(name, background=background)
        style.configure("TLabel", background=BG, foreground=TEXT)
        style.configure("Muted.TLabel", foreground=MUTED)
        style.configure("Panel.TLabel", background=PANEL)
        style.configure("PanelMuted.TLabel", background=PANEL, foreground=MUTED)
        style.configure("Title.TLabel", font=("Segoe UI Semibold", 22))
        style.configure("Subtitle.TLabel", font=("Segoe UI Semibold", 13))
        style.configure("Card.TLabel", background=SURFACE)
        style.configure("Metric.TLabel", background=SURFACE, font=("Segoe UI Semibold", 23))
        style.configure("TButton", background=SURFACE, foreground=TEXT, padding=(12, 8), relief="flat")
        style.map("TButton", background=[("active", "#253c57")], foreground=[("disabled", "#64758b")])
        style.configure("Accent.TButton", background=TEAL, foreground=BG, font=("Segoe UI Semibold", 10))
        style.map("Accent.TButton", background=[("active", "#85efdb"), ("disabled", "#244c4c")], foreground=[("disabled", "#8daca9")])
        for name in ("TEntry", "TSpinbox", "TCombobox"):
            style.configure(name, fieldbackground=BG, foreground=TEXT, padding=6, arrowcolor=MUTED)
        style.map("TCombobox", fieldbackground=[("readonly", BG)], foreground=[("readonly", TEXT), ("disabled", MUTED)])
        for name in ("TCheckbutton", "TRadiobutton"):
            style.configure(name, background=PANEL, foreground=TEXT, padding=(0, 5))
            style.map(name, background=[("active", PANEL)])
        style.configure("TNotebook", background=BG, borderwidth=0)
        style.configure("TNotebook.Tab", background=PANEL, foreground=MUTED, padding=(20, 11))
        style.map("TNotebook.Tab", background=[("selected", SURFACE)], foreground=[("selected", TEAL)])
        self.root.option_add("*TCombobox*Listbox.background", PANEL)
        self.root.option_add("*TCombobox*Listbox.foreground", TEXT)

    def _label(self, parent, text="", *, style="TLabel", **kwargs):
        return ttk.Label(parent, text=text, style=style, **kwargs)

    def _build(self, auto_check_updates, update_service, portable_updates):
        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(2, weight=1)
        header = ttk.Frame(self.root, padding=(24, 16, 24, 12))
        header.grid(row=0, column=0, sticky="ew")
        header.columnconfigure(0, weight=1)
        self._label(header, "MiRealmBot", style="Title.TLabel").grid(row=0, column=0, sticky="w")
        self._label(header, "AUTO NEXUS   /   AUTO AIM", style="Muted.TLabel").grid(row=1, column=0, sticky="w")
        tk.Label(header, textvariable=self.status_var, background=SURFACE, foreground=TEAL,
                 padx=16, pady=8, font=("Segoe UI Semibold", 10)).grid(row=0, column=1, rowspan=2, sticky="e")
        self.banner = tk.Label(self.root, textvariable=self.banner_var, anchor="w", padx=14, pady=10, font=("Segoe UI Semibold", 10))
        self.banner.grid(row=1, column=0, sticky="ew", padx=20, pady=(0, 10))
        self.notebook = ttk.Notebook(self.root)
        self.notebook.grid(row=2, column=0, sticky="nsew", padx=20, pady=(0, 10))
        self.live_tab = ttk.Frame(self.notebook, padding=20)
        self.nexus_tab = ttk.Frame(self.notebook, padding=24)
        self.aim_tab = ttk.Frame(self.notebook, padding=24)
        for frame, label in ((self.live_tab, "Juego en vivo"), (self.nexus_tab, "AutoNexus"), (self.aim_tab, "AutoAim")):
            self.notebook.add(frame, text=label)
        self._build_live()
        self._build_nexus()
        self._build_autoaim()
        self.updates_panel = UpdatePanel(self.notebook, can_install=self._can_install_update,
            request_install=self._install_update, service=update_service,
            portable=portable_updates, auto_check=auto_check_updates)
        self.notebook.add(self.updates_panel, text="Actualizaciones")
        self.notebook.select(self.live_tab)
        self._label(self.root, textvariable=self.footer_var, style="Muted.TLabel", padding=(22, 0, 22, 10), font=("Segoe UI", 9)).grid(row=3, column=0, sticky="ew")

    def _metric(self, parent, column, heading, color):
        card = ttk.Frame(parent, style="Card.TFrame", padding=(16, 11))
        card.grid(row=0, column=column, sticky="nsew", padx=(0, 10 if column < 2 else 0))
        self._label(card, heading, style="Card.TLabel", foreground=MUTED, font=("Segoe UI", 9)).pack(anchor="w")
        variable = tk.StringVar(self.root, "—")
        self._label(card, textvariable=variable, style="Metric.TLabel", foreground=color).pack(anchor="w")
        return variable

    def _build_live(self):
        p = self.live_tab
        p.columnconfigure(0, weight=1)
        p.rowconfigure(5, weight=1)
        controls = ttk.Frame(p, style="Panel.TFrame", padding=(16, 10))
        controls.grid(row=0, column=0, sticky="ew", pady=(0, 14))
        self.start_button = ttk.Button(controls, text="Iniciar bot", style="Accent.TButton", command=self.start_live)
        self.start_button.pack(side="left", padx=(0, 9))
        self.stop_button = ttk.Button(controls, text="Detener", command=self.stop_live, state="disabled")
        self.stop_button.pack(side="left")
        metrics = ttk.Frame(p)
        metrics.grid(row=1, column=0, sticky="ew", pady=(0, 14))
        for column in range(3):
            metrics.columnconfigure(column, weight=1, uniform="metrics")
        self.live_observed_var = self._metric(metrics, 0, "HP OBSERVADO", BLUE)
        self.live_predicted_var = self._metric(metrics, 1, "HP PREVISTO", TEAL)
        self.live_damage_var = self._metric(metrics, 2, "ÚLTIMO DAÑO EFECTIVO", AMBER)
        detail = ttk.Frame(p, style="Panel.TFrame", padding=(16, 9))
        detail.grid(row=2, column=0, sticky="ew")
        detail.columnconfigure(1, weight=1)
        self.live_vars = {}
        for row, (key, label, initial) in enumerate((
            ("map", "Mapa", "—"), ("decision", "AutoNexus", "Sin sesión"), ("autoaim", "AutoAim", "Sin sesión"),
            ("focus", "Ventana del juego", "Sin comprobar"), ("delivery", "Última salida", "Ninguna"),
            ("applied", "Configuración aplicada", "Todavía no se ha iniciado una sesión"),
            ("log", "Registro", "—"), ("logging", "Estado del registro", "Registro reciente: máx. 3 MiB"),
            ("error", "Último error", "Ninguno"))):
            self._label(detail, label, style="PanelMuted.TLabel", width=23).grid(row=row, column=0, sticky="nw", pady=4)
            variable = tk.StringVar(self.root, initial)
            self.live_vars[key] = variable
            self._label(detail, textvariable=variable, style="Panel.TLabel", wraplength=730,
                        foreground=RED if key == "error" else TEXT).grid(row=row, column=1, sticky="w", pady=4)
        self.live_hint_var = tk.StringVar(self.root, "Configura AutoNexus y AutoAim en sus pestañas. Abre RotMG Exalt y pulsa Iniciar aquí.")
        self._label(p, textvariable=self.live_hint_var, style="Muted.TLabel", wraplength=960).grid(row=3, column=0, sticky="ew", pady=(12, 12))
        self._label(p, "ACTIVIDAD DE LA SESIÓN · Registro reciente: máx. 3 MiB", style="Muted.TLabel", font=("Segoe UI Semibold", 9)).grid(row=4, column=0, sticky="w", pady=(0, 6))
        self.live_log = tk.Text(p, height=5, background=PANEL, foreground=MUTED, borderwidth=0,
                                highlightthickness=0, wrap="word", font=("Consolas", 9), padx=12, pady=10, state="disabled")
        self.live_log.grid(row=5, column=0, sticky="nsew")

    def _save_controls(self, parent, row):
        line = ttk.Frame(parent)
        line.grid(row=row, column=0, sticky="ew", pady=(18, 0))
        button = ttk.Button(line, text="Guardar configuración", command=self.save_parameters)
        button.pack(side="left", padx=(0, 14))
        self.config_widgets.append(button)
        self._label(line, textvariable=self.draft_status_var, style="Muted.TLabel", wraplength=690).pack(side="left")

    def _build_nexus(self):
        p = self.nexus_tab
        p.columnconfigure(0, weight=1)
        self._label(p, "AutoNexus", style="Subtitle.TLabel").grid(row=0, column=0, sticky="w")
        self._label(p, "Configura la salida al Nexus. La sesión se inicia y detiene desde Juego en vivo.", style="Muted.TLabel").grid(row=1, column=0, sticky="w", pady=(5, 16))
        settings = ttk.Frame(p, style="Panel.TFrame", padding=20)
        settings.grid(row=2, column=0, sticky="ew")
        self._label(settings, "Salir bajo", style="PanelMuted.TLabel").grid(row=0, column=0, sticky="w", padx=(0, 24))
        self.threshold_input = ttk.Spinbox(settings, from_=1, to=99, increment=1, width=8,
            textvariable=self.draft.threshold, font=("Segoe UI Semibold", 22))
        self.threshold_input.grid(row=0, column=1, sticky="w")
        self._label(settings, "% de vida", style="PanelMuted.TLabel").grid(row=0, column=2, sticky="w", padx=(12, 0))
        self.limit_var = tk.StringVar(self.root)
        self._label(settings, textvariable=self.limit_var, style="PanelMuted.TLabel").grid(row=1, column=0, columnspan=3, sticky="w", pady=(8, 14))
        self.prediction_check = ttk.Checkbutton(settings, text="Prever daño de proyectiles", variable=self.draft.prediction_enabled)
        self.prediction_check.grid(row=2, column=0, columnspan=3, sticky="w")
        self._label(settings, "Usa el daño efectivo de los golpes aceptados por el juego. La predicción no garantiza una salida a tiempo.", style="PanelMuted.TLabel", wraplength=880).grid(row=3, column=0, columnspan=3, sticky="w", pady=(2, 0))
        keys = ttk.Frame(p, style="Panel.TFrame", padding=20)
        keys.grid(row=3, column=0, sticky="ew", pady=(14, 0))
        keys.columnconfigure(0, weight=1)
        self.detect_keys_check = ttk.Checkbutton(keys, text="Leer teclas del juego", variable=self.draft.detect_game_keys, command=self.refresh_game_bindings)
        self.detect_keys_check.grid(row=0, column=0, sticky="w")
        self.refresh_keys_button = ttk.Button(keys, text="Volver a leer teclas", command=self.refresh_game_bindings)
        self.refresh_keys_button.grid(row=0, column=1, sticky="e")
        self._label(keys, textvariable=self.nexus_binding_var, style="PanelMuted.TLabel", wraplength=870).grid(row=1, column=0, columnspan=2, sticky="w", pady=(8, 12))
        manual = ttk.Frame(keys, style="Panel.TFrame")
        manual.grid(row=2, column=0, columnspan=2, sticky="w")
        self._label(manual, "Tecla Nexus manual", style="PanelMuted.TLabel").pack(side="left", padx=(0, 20))
        self.key_combo = ttk.Combobox(manual, textvariable=self.draft.nexus_key,
            values=("CTRL", "RCTRL", *tuple("ABCDEFGHIJKLMNOPQRSTUVWXYZ")), state="readonly", width=12)
        self.key_combo.pack(side="left")
        self._label(keys, "La tecla manual sólo se usa al desmarcar la lectura automática y debe coincidir con la del juego.", style="PanelMuted.TLabel", wraplength=870).grid(row=3, column=0, columnspan=2, sticky="w", pady=(8, 0))
        self.config_widgets.extend((self.threshold_input, self.prediction_check, self.detect_keys_check, self.refresh_keys_button))
        self._label(p, "Nexus excluido · Un intento por mapa · Filtros de inmunidad sin verificar", style="Muted.TLabel").grid(row=4, column=0, sticky="w", pady=(14, 0))
        self._save_controls(p, 5)

    def _build_autoaim(self):
        p = self.aim_tab
        p.columnconfigure(0, weight=1)
        self._label(p, "AutoAim · Close to Mouse", style="Subtitle.TLabel").grid(row=0, column=0, sticky="w")
        self._label(p, "Elige el enemigo cercano al cursor. La sesión se inicia y detiene desde Juego en vivo.", style="Muted.TLabel").grid(row=1, column=0, sticky="w", pady=(5, 16))
        settings = ttk.Frame(p, style="Panel.TFrame", padding=20)
        settings.grid(row=2, column=0, sticky="ew")
        settings.columnconfigure(1, weight=1)
        options = ttk.Frame(settings, style="Panel.TFrame")
        options.grid(row=0, column=0, columnspan=2, sticky="ew")
        self.aim_enabled_check = ttk.Checkbutton(options, text="Activar AutoAim", variable=self.draft.aim_enabled)
        self.prioritize_bosses_check = ttk.Checkbutton(options, text="Priorizar bosses", variable=self.draft.prioritize_bosses)
        self.ignore_walls_check = ttk.Checkbutton(options, text="Ignorar murallas", variable=self.draft.ignore_walls)
        for widget in (self.aim_enabled_check, self.prioritize_bosses_check, self.ignore_walls_check):
            widget.pack(side="left", padx=(0, 22))
        self._label(settings, "Modo", style="PanelMuted.TLabel").grid(row=1, column=0, sticky="w", pady=12)
        self._label(settings, "Close to Mouse", style="Panel.TLabel").grid(row=1, column=1, sticky="w")
        self._label(settings, "Extrapolation", style="PanelMuted.TLabel").grid(row=2, column=0, sticky="w", pady=8)
        self.extrapolation_input = ttk.Spinbox(settings, from_=0, to=1000000000, increment=1, textvariable=self.draft.extrapolation, width=12)
        self.extrapolation_input.grid(row=2, column=1, sticky="w")
        self._label(settings, "0 apunta a la posición actual. Un valor mayor adelanta más la puntería según el movimiento del enemigo.", style="PanelMuted.TLabel", wraplength=900).grid(row=3, column=0, columnspan=2, sticky="w", pady=(4, 12))
        self._label(settings, "Radio junto al cursor", style="PanelMuted.TLabel").grid(row=4, column=0, sticky="w", pady=8)
        self.aim_radius_input = ttk.Spinbox(settings, from_=0.1, to=1000000000, increment=.5, textvariable=self.draft.mouse_radius, width=12)
        self.aim_radius_input.grid(row=4, column=1, sticky="w")
        self._label(settings, "Prioriza bosses con marcador del juego dentro del radio; sin uno, elige al enemigo más cercano. Ignorar murallas las excluye como objetivos.", style="PanelMuted.TLabel", wraplength=900).grid(row=5, column=0, columnspan=2, sticky="w", pady=(12, 0))
        info = ttk.Frame(p, style="Panel.TFrame", padding=20)
        info.grid(row=3, column=0, sticky="ew", pady=(14, 0))
        self._label(info, "Con AutoAim activado, apunta mientras el autofire del juego está encendido; no necesitas mantener una tecla pulsada.", style="PanelMuted.TLabel", wraplength=900).pack(anchor="w")
        self._label(info, textvariable=self.autofire_binding_var, style="PanelMuted.TLabel", wraplength=900).pack(anchor="w", pady=(12, 0))
        self.config_widgets.extend((self.aim_enabled_check, self.prioritize_bosses_check, self.ignore_walls_check, self.extrapolation_input, self.aim_radius_input))
        self._save_controls(p, 4)

    def _draft_changed(self, *unused):
        self.draft_status_var.set("Cambios para el próximo inicio. Guardar no inicia el bot.")
        try:
            preview = AutoNexus(self.draft.threshold_percent() / 100, use_state_filters=False)
            preview.update_health(100, 100)
            limit = preview.evaluate().limit
            self.limit_var.set(f"Con 100 HP: límite {limit}; sale con {limit - 1} HP o menos.")
        except ValueError as error:
            self.limit_var.set(str(error))
        self._sync_controls()

    def _sync_controls(self):
        running = self.runtime_thread is not None
        locked = running or self.closing or self.updates_panel.installing
        for widget in self.config_widgets:
            widget.configure(state="disabled" if locked else "normal")
        self.key_combo.configure(state="readonly" if not locked and not self.draft.detect_game_keys.get() else "disabled")
        self.start_button.configure(state="normal" if not locked and self.cleanup_complete else "disabled")
        self.stop_button.configure(state="normal" if running and not self.stop_event.is_set() else "disabled")
        if running:
            self.draft_status_var.set("Configuración bloqueada hasta detener la sesión.")
        self._update_banner()

    def _update_banner(self):
        color, background = BLUE, "#192e49"
        if self.runtime_thread is None:
            text = "DETENIDO · INICIA LA SESIÓN DESDE JUEGO EN VIVO"
        else:
            color, background = AMBER, "#44321f"
            if self.nexus_binding_status == "waiting":
                text = "ACTIVO · NEXUS ESPERA LEER LA TECLA DEL JUEGO"
            elif self.nexus_binding_status == "unsupported":
                text = "ACTIVO · TECLA NEXUS NO ADMITIDA"
            else:
                text = "ACTIVO · NEXUS PUEDE ENVIAR " + str(self.applied_key)
            if self.applied_config and self.applied_config.autoaim.enabled:
                text += " · AUTOAIM SEGÚN AUTOFIRE"
        self.banner_var.set(text)
        self.banner.configure(background=background, foreground=color)

    def refresh_game_bindings(self):
        if self.runtime_thread is not None or self.closing:
            return
        self._sync_controls()
        if not self.draft.detect_game_keys.get():
            self.nexus_binding_var.set("Lectura automática desactivada. Nexus usará la tecla manual elegida abajo.")
            self.autofire_binding_var.set("El estado de autofire se observa dentro del juego al iniciar; no depende de mantener una tecla.")
            return
        try:
            bindings = self.settings_reader()
        except Exception:
            self.nexus_binding_var.set("No se pudieron leer las teclas guardadas. Nexus se comprobará dentro del juego al iniciar.")
            self.autofire_binding_var.set("Autofire se comprobará dentro del juego al iniciar.")
            return
        nexus, fire = bindings.get("nexus"), bindings.get("autofire")
        try:
            key = normalize_nexus_key(nexus.key if nexus else None)
        except ValueError:
            key = None
        if key:
            self.draft.nexus_key.set(key)
            self.nexus_binding_var.set(f"Nexus: {key} (guardada en el juego); se confirmará dentro del juego al iniciar.")
        else:
            self.nexus_binding_var.set("Nexus: sin valor guardado reconocido; se leerá dentro del juego al iniciar.")
        self.autofire_binding_var.set(f"Autofire: {fire.key} (guardada en el juego); se confirmará al iniciar."
            if fire and fire.key else "Autofire: sin valor guardado reconocido; se leerá dentro del juego al iniciar.")

    def save_parameters(self):
        if self.runtime_thread is not None or self.closing or self.updates_panel.installing:
            return False
        try:
            config = self.draft.validate()
            backup = save_config(self.config_path, config)
        except (OSError, ValueError, TypeError, tk.TclError) as error:
            self.last_error = display_text(error)
            messagebox.showerror("No se pudo guardar", self.last_error, parent=self.root)
            return False
        self.draft_status_var.set("Configuración guardada. Se aplicará al próximo inicio.")
        self.footer_var.set("Configuración guardada. " + (f"Copia anterior: {Path(backup).name}" if backup else "Sin cambios pendientes."))
        return True

    def start_live(self):
        if self.notebook.select() != str(self.live_tab) or self.runtime_thread is not None or not self.cleanup_complete or self.closing or self.updates_panel.installing:
            return False
        if self.draft.detect_game_keys.get():
            self.refresh_game_bindings()
        try:
            config = self.draft.validate()
        except (ValueError, TypeError, tk.TclError) as error:
            self.last_error = display_text(error)
            messagebox.showerror("Configuración inválida", self.last_error, parent=self.root)
            return False
        stop_event = self.stop_event = threading.Event()
        inbox = self.runtime_inbox = RuntimeInbox()
        self._runtime_finished_seen = False
        self.cleanup_complete = False
        self.applied_config = config
        self.applied_key = config.nexus_key
        self.nexus_binding_status = "waiting" if config.detect_game_keys else "manual"
        self.status_var.set("Conectando")
        self.live_vars["error"].set("Ninguno")
        self.live_vars["delivery"].set("Ninguna")
        self.live_vars["logging"].set("Registro reciente: máx. 3 MiB")
        self.live_vars["autoaim"].set("Esperando datos del juego…" if config.autoaim.enabled else "AutoAim desactivado")
        self._render_applied_config()
        self.live_hint_var.set("Conectando con el juego…")

        def worker():
            try:
                code = self.runtime_runner(config, active=True, stop_event=stop_event, on_update=inbox.put)
            except Exception as error:
                inbox.put({"event": "worker_failure", "error": display_text(error)})
                code = 1
            finally:
                inbox.put({"event": "worker_done", "code": locals().get("code", 1)})
        self.runtime_thread = threading.Thread(target=worker, name="MiRealmBot-runtime", daemon=False)
        self.runtime_thread.start()
        self._sync_controls()
        return True

    def stop_live(self):
        if self.runtime_thread is None:
            return
        self.stop_event.set()
        self.status_var.set("Deteniendo")
        self.live_hint_var.set("Deteniendo y retirando los hooks. Espera a que termine la limpieza.")
        self._sync_controls()

    def _render_applied_config(self):
        if self.applied_config is None:
            return
        config = self.applied_config
        key = ("Nexus pendiente de lectura" if self.nexus_binding_status == "waiting" else
               "Tecla Nexus no admitida" if self.nexus_binding_status == "unsupported" else self.applied_key)
        self.live_vars["applied"].set(f"{config.threshold * 100:g} % · {key} · predicción " +
            ("sí" if config.prediction_enabled else "no") + " · AutoAim " + ("sí" if config.autoaim.enabled else "no"))

    def _show_runtime_bindings(self, bindings):
        if not isinstance(bindings, dict):
            return
        resolved = {}
        for name in ("nexus", "autofire"):
            item = bindings.get(name)
            if not isinstance(item, dict):
                continue
            age = item.get("age_ms")
            if isinstance(age, bool) or not isinstance(age, (int, float)) or not math.isfinite(age) or not 0 <= age <= 250:
                continue
            key = unity_key_name(item.get("unity_code"))
            if key:
                resolved[name] = key
        if self.applied_config and self.applied_config.detect_game_keys:
            try:
                nexus = normalize_nexus_key(resolved.get("nexus"))
            except ValueError:
                nexus = None
            if nexus:
                self.applied_key = nexus
                self.nexus_binding_status = "detected"
            self.nexus_binding_var.set(f"Nexus: {nexus} (detectada dentro del juego)." if nexus else "Nexus: sin lectura reciente reconocida.")
        self.autofire_binding_var.set(f"Autofire: {resolved['autofire']} (detectada dentro del juego)."
            if "autofire" in resolved else "Autofire: sin lectura reciente reconocida.")

    def _append_log(self, text):
        self.live_log.configure(state="normal")
        self.live_log.insert("end", datetime.now().strftime("%H:%M:%S") + "  " + display_text(text, 800) + "\n")
        count = int(self.live_log.index("end-1c").split(".")[0])
        if count > 161:
            self.live_log.delete("1.0", f"{count - 160}.0")
        self.live_log.see("end")
        self.live_log.configure(state="disabled")

    def _handle_runtime_record(self, record):
        kind = record.get("event")
        if record.get("log_path"):
            self.live_vars["log"].set(display_text(record["log_path"]))
        if kind == "lifecycle":
            status = record.get("status")
            if status == "finished":
                self._runtime_finished_seen = True
                self.cleanup_complete = record.get("cleanup_complete") is True
                self.status_var.set("Detenido" if self.cleanup_complete and record.get("code") == 0 else "Error")
                if record.get("errors"):
                    errors = record["errors"]
                    text = "; ".join(display_text(item, 180) for item in errors[:8]) if isinstance(errors, (list, tuple)) else errors
                    self.live_vars["error"].set(display_text(text))
                self.live_hint_var.set("Sesión cerrada. El juego sigue abierto." if self.cleanup_complete else "La limpieza quedó incompleta. No se permite iniciar otra sesión.")
            elif status == "running":
                self.status_var.set("Activo")
                self.live_hint_var.set("Vuelve a la ventana del juego. AutoAim actúa cuando está habilitado y el autofire está encendido.")
            elif status == "stopping":
                self.status_var.set("Deteniendo")
            else:
                self.status_var.set("Conectando")
            self._append_log(self.status_var.get())
            self._update_banner()
        elif kind in ("error", "worker_failure"):
            error = display_text(record.get("error") or record.get("message") or record)
            self.last_error = error
            self.live_vars["error"].set(error)
            self._append_log(error)
        elif kind == "logging_warning":
            if record.get("logging_enabled") is True:
                text = "Registro activo; quedan registros antiguos pendientes de retirar. "
            else:
                text = "Registro en disco desactivado; el bot sigue funcionando. "
            text += str(record.get("error") or "Revisa el estado del registro.")[:240]
            self.live_vars["logging"].set(text)
            self._append_log(text)
        elif kind == "cleanup":
            complete = record.get("complete", record.get("cleanup_complete"))
            self._append_log("Limpieza: " + ("completa" if complete else display_text(record.get("errors", "en curso"))))
        elif kind == "telemetry":
            detect = self.applied_config is not None and self.applied_config.detect_game_keys
            binding_status = record.get("nexus_binding_status")
            if detect and binding_status in ("waiting", "unsupported", "detected"):
                self.nexus_binding_status = binding_status
            self._show_runtime_bindings(record.get("game_bindings"))
            self._render_applied_config()
            self._update_banner()
            frame, prediction = record.get("frame") or {}, record.get("prediction") or {}
            self.live_observed_var.set(f"{number(frame.get('player_hp'))} / {number(frame.get('max_hp'))}")
            self.live_predicted_var.set(number(prediction.get("tracked_hp")) if prediction.get("enabled") else "Desactivado")
            self.live_vars["map"].set(display_text(record.get("map_name") or frame.get("map_name") or "Sin datos recientes", 120))
            decision = record.get("decision") or {}
            self.live_vars["decision"].set("SOLICITAR NEXUS" if decision.get("action") == "escape" else short_reason(decision.get("reason")))
            if detect and self.nexus_binding_status == "waiting":
                self.live_vars["decision"].set("Esperando tecla Nexus del juego")
                self.live_hint_var.set("Esperando tecla Nexus del juego. La sesión aún no puede solicitar una salida.")
            elif detect and self.nexus_binding_status == "unsupported":
                self.live_vars["decision"].set("Tecla Nexus no admitida")
                self.live_hint_var.set("La tecla Nexus no está admitida. Detén la sesión y configura una letra o Control; o desmarca «Leer teclas del juego» en AutoNexus y elige una tecla manual que coincida con el juego.")
            elif binding_status in ("detected", "manual"):
                self.live_hint_var.set("Vuelve a la ventana del juego. AutoAim actúa cuando está habilitado y el autofire está encendido.")
            focus = record.get("game_foreground")
            self.live_vars["focus"].set("En primer plano" if focus is True else "Vuelve a la ventana del juego" if focus is False else "Sin comprobar")
            delivery = record.get("delivery") or {}
            if delivery.get("status") not in (None, "none"):
                labels = {"observe_only": "Sin intervención · no se envió la tecla", "key_sent": "Tecla enviada · Nexus sin confirmar",
                          "blocked": "Tecla bloqueada · no se reintentará en este mapa", "failed": "Falló el envío de la tecla"}
                self.live_vars["delivery"].set(labels.get(delivery.get("status"), display_text(delivery.get("status"))))
            aim = record.get("autoaim")
            if isinstance(aim, dict):
                if aim.get("enabled") is False:
                    text = "AutoAim desactivado"
                else:
                    fire = aim.get("autofire")
                    authority = ("Pausado" if aim.get("paused") is True else "Activo" if aim.get("active") is True else
                                 "Sin intervención · no altera los disparos" if aim.get("active") is False else "Estado sin confirmar")
                    text = authority + " · autofire " + ("encendido" if fire is True else "apagado" if fire is False else "sin confirmar")
                    if aim.get("target_id") is not None:
                        boss = aim.get("target_is_boss")
                        text += " · enemigo " + display_text(aim["target_id"], 64) + " · " + ("Boss" if boss is True else "Enemigo" if boss is False else "Tipo sin confirmar")
                    angle = aim.get("angle")
                    if isinstance(angle, (int, float)) and math.isfinite(angle):
                        text += f" · ángulo {angle:.2f}"
                    if aim.get("status"):
                        text += " · " + str(aim["status"])[:160]
                self.live_vars["autoaim"].set(display_text(text))

    def _can_install_update(self):
        if self.runtime_thread is not None:
            return False, "Detén AutoNexus y AutoAim y espera a que termine la sesión."
        if not self.cleanup_complete:
            return False, "No se confirmó la limpieza del juego. Cierra el juego y vuelve a abrir MiRealmBot antes de instalar."
        if self.closing:
            return False, "MiRealmBot ya se está cerrando."
        return True, ""

    def _install_update(self, prepared):
        allowed, reason = self._can_install_update()
        if not allowed:
            self.updates_panel.recover_install_error(reason)
            return False
        launch_update(prepared)
        self.close()
        return True

    def _schedule_tick(self):
        self._tick_after = self.root.after(60, self._tick)

    def _tick(self):
        self._tick_after = None
        self.updates_panel.tick()
        self._consume_runtime_inbox()
        if self.runtime_thread is not None and not self.runtime_thread.is_alive():
            self.runtime_thread.join()
            # A final handshake may arrive between the first drain and exit.
            # Consume it before deciding whether this owned session is clean.
            self._consume_runtime_inbox()
            self.runtime_thread = None
            if not self._runtime_finished_seen:
                self.cleanup_complete = False
                self.status_var.set("Error")
                self.live_hint_var.set("El runtime terminó sin confirmar la limpieza. Reinicio bloqueado.")
            if self.closing and not self.cleanup_complete:
                self.closing = False
                self.footer_var.set("La ventana permanece abierta porque falta confirmar la limpieza del runtime.")
            self._sync_controls()
        if self.closing and self.runtime_thread is None and not self.updates_panel.busy:
            self._destroy()
            return
        self._schedule_tick()

    def _consume_runtime_inbox(self):
        critical, latest, notes, damage = self.runtime_inbox.drain()
        # The latest sample precedes final lifecycle messages in the worker.
        # Render it first so a finished session keeps its final cleanup status.
        if latest:
            self._handle_runtime_record(latest)
        for record in critical:
            self._handle_runtime_record(record)
        if damage is not None:
            self.live_damage_var.set(str(damage))
        for note in notes:
            self._append_log(note)

    def close(self):
        self.closing = True
        updates_done = self.updates_panel.close()
        if self.runtime_thread is not None:
            self.stop_live()
            self.footer_var.set("Cerrando: se espera a que el runtime termine y confirme la limpieza…")
            return
        if not updates_done:
            self.footer_var.set("Cerrando: se cancela la consulta o descarga de GitHub; espera a que termine…")
            self._sync_controls()
            return
        self._destroy()

    def _destroy(self):
        if self._tick_after is not None:
            self.root.after_cancel(self._tick_after)
            self._tick_after = None
        self.root.destroy()


def main():
    root = tk.Tk()
    def show_callback_error(error_type, error, traceback):
        messagebox.showerror("Error de la interfaz", display_text(error), parent=root)
    root.report_callback_exception = show_callback_error
    try:
        App(root, auto_check_updates=True)
    except Exception as error:
        root.withdraw()
        messagebox.showerror("MiRealmBot no pudo abrirse", display_text(error), parent=root)
        root.destroy()
        return 1
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
