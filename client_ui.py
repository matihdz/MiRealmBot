"""Local Tk client. Opening this window only replays a recording.

The runtime owns process access, input, logging and cleanup. Tk only submits a
validated configuration and renders structured messages on its main thread.
"""
from __future__ import annotations

from collections import deque
from datetime import datetime
import math
from pathlib import Path
import queue
import threading
import time
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from app_paths import APP_ROOT
from app_version import VERSION
from autonexus_core import AutoNexus
from client_updates import UpdatePanel
from game_settings import read_game_bindings, unity_key_name
from live_autonexus import (config_from_mapping, config_to_mapping, load_config,
                            run_runtime, save_config)
from replay_autonexus import (DEFAULT_RECORDING, compare_thresholds,
                              load_recording, replay_recording)
from update_core import launch_update
from windows_nexus import normalize_nexus_key

ROOT = APP_ROOT
BG = "#0b1220"
PANEL = "#121e30"
SURFACE = "#17253a"
BORDER = "#293a51"
TEXT = "#edf3fc"
MUTED = "#9aacc4"
TEAL = "#4cdec2"
BLUE = "#75a9ff"
AMBER = "#ffc978"
RED = "#ff8f98"

REASONS = {
    "both health sources are at or above the limit": "Vida por encima del límite",
    "health is below the limit": "Vida bajo el límite",
    "health is below the limit; exit requested": "Vida bajo el límite · salida solicitada",
    "one health source is below the limit": "Vida bajo el límite",
    "escape already requested for this map": "Salida ya solicitada en este mapa",
    "exit was already requested for this map": "Salida ya solicitada en este mapa",
    "exit was already requested for this map": "Salida ya solicitada",
    "health is below the limit; exit requested": "Vida baja · solicitar salida",
    "health data is unknown": "Vida sin datos recientes",
    "map state is unknown": "Mapa sin datos recientes",
    "player gates are unknown": "Filtros de estado sin datos",
    "map is excluded": "Mapa excluido",
    "excluded map": "Mapa excluido",
    "missing health observation": "Sin una lectura de vida válida",
    "autonexus is disabled": "AutoNexus desactivado",
    "AutoNexus is disabled": "AutoNexus desactivado",
    "map state is unknown": "Esperando la identidad del mapa",
    "health data is unknown": "Esperando una lectura válida de vida",
    "player gates are unknown": "Filtros de estado sin datos",
    "a player gate blocks AutoNexus": "Un estado del jugador bloquea la salida",
}


def short_reason(value):
    return REASONS.get(str(value), str(value or "Sin decisión"))


def number(value):
    if value is None:
        return "—"
    return str(value)


def clock_text(seconds):
    seconds = max(0.0, float(seconds))
    return f"{int(seconds // 60):02d}:{seconds % 60:04.1f}"


class RuntimeInbox:
    """Bound visual telemetry, retaining lifecycle/errors until Tk consumes them."""
    def __init__(self):
        self.critical = queue.Queue(maxsize=128)
        self.lock = threading.Lock()
        self.latest = None
        self.notes = deque(maxlen=160)
        self.last_damage = None

    def put(self, record):
        kind = record.get("event")
        if kind in ("lifecycle", "error", "cleanup", "worker_done", "worker_failure"):
            self.critical.put(record)
        else:
            with self.lock:
                if kind == "telemetry":
                    self.latest = record
                if kind == "prediction_transport":
                    for event in record.get("events", []):
                        if event.get("kind") == "damage":
                            self.last_damage = event.get("amount")
                            self.notes.append(f"Golpe efectivo: {event.get('amount')} HP")
                if kind == "decision" and (record.get("delivery") or {}).get("status") not in (None, "none"):
                    self.notes.append("Salida: " + str(record["delivery"].get("status")))

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
    def __init__(self, root, *, config_path=None, recording_path=None,
                 runtime_runner=None, settings_reader=None, auto_check_updates=False,
                 update_service=None, portable_updates=None):
        self.root = root
        self.config_path = Path(config_path or ROOT / "autonexus.json")
        self.runtime_runner = runtime_runner or run_runtime
        self.settings_reader = settings_reader or read_game_bindings
        self.auto_check_updates = auto_check_updates
        self.update_service = update_service
        self.portable_updates = portable_updates
        self.game_bindings = {}
        self.runtime_thread = None
        self.stop_event = None
        self.runtime_inbox = RuntimeInbox()
        self.cleanup_complete = True
        self.closing = False
        self.active_context = "replay"
        self.recording = None
        self.replay_result = None
        self.replay_rows = ()
        self.replay_position = 0.0
        self.playing = False
        self.chart_zoomed = True
        self.play_stamp = time.monotonic()
        self._update_after = None
        self._loading_controls = False
        self._runtime_finished_seen = False
        self._current_row_index = None
        self._after_ids = set()
        self.last_error = None
        self.live_status = "Detenido"
        self.live_active = False
        self.live_mapping = config_to_mapping(load_config(self.config_path))
        self.applied_key = self.live_mapping["nexus_key"]
        self.applied_detect_keys = self.live_mapping.get("detect_game_keys", True)
        self.applied_config = None
        self.nexus_binding_status = None
        self.replay_draft = {"threshold_percent": 30.0, "prediction_enabled": True}
        self.live_draft = {"threshold_percent": self.live_mapping["threshold"] * 100,
                           "prediction_enabled": self.live_mapping["prediction_enabled"]}
        self.threshold_var = tk.StringVar(root, "30")
        self.slider_var = tk.DoubleVar(root, 30)
        self.prediction_var = tk.BooleanVar(root, True)
        self.key_var = tk.StringVar(root, self.live_mapping["nexus_key"])
        self.detect_keys_var = tk.BooleanVar(root, self.live_mapping.get("detect_game_keys", True))
        aim = self.live_mapping["autoaim"]
        self.aim_enabled_var = tk.BooleanVar(root, aim["enabled"])
        self.extrapolation_var = tk.StringVar(root, f"{aim['extrapolation']:g}")
        self.aim_radius_var = tk.StringVar(root, f"{aim['mouse_radius']:g}")
        self.bindings_var = tk.StringVar(root, "Todavía no se han leído las teclas del juego.")
        self.mode_var = tk.StringVar(root, "observe")
        self.speed_var = tk.StringVar(root, "10×")
        self.scrub_var = tk.DoubleVar(root, 0)
        self.status_var = tk.StringVar(root, "Detenido")
        self.banner_var = tk.StringVar(root, "SIMULACIÓN · SIN CONEXIÓN AL JUEGO")
        self._style()
        self._build()
        self.root.protocol("WM_DELETE_WINDOW", self.close)
        self.threshold_var.trace_add("write", self._parameter_changed)
        self.prediction_var.trace_add("write", self._parameter_changed)
        self._load_recording(Path(recording_path or DEFAULT_RECORDING))
        self.refresh_game_bindings()
        self._sync_context()
        self._schedule(60, self._tick)

    def _schedule(self, milliseconds, callback):
        token = None
        def invoke():
            self._after_ids.discard(token)
            callback()
        token = self.root.after(milliseconds, invoke)
        self._after_ids.add(token)
        return token

    def _style(self):
        self.root.title(f"MiRealmBot {VERSION} · AutoNexus / AutoAim")
        self.root.geometry("1180x850")
        self.root.minsize(1040, 760)
        self.root.configure(bg=BG)
        self.root.option_add("*Font", "{Segoe UI} 10")
        style = ttk.Style(self.root)
        style.theme_use("clam")
        style.configure(".", background=BG, foreground=TEXT, font=("Segoe UI", 10),
                        bordercolor=BORDER, lightcolor=BORDER, darkcolor=BORDER)
        style.configure("TFrame", background=BG)
        style.configure("Panel.TFrame", background=PANEL)
        style.configure("Card.TFrame", background=SURFACE)
        style.configure("TLabel", background=BG, foreground=TEXT)
        style.configure("Muted.TLabel", foreground=MUTED)
        style.configure("Panel.TLabel", background=PANEL)
        style.configure("PanelMuted.TLabel", background=PANEL, foreground=MUTED)
        style.configure("Title.TLabel", font=("Segoe UI Semibold", 22))
        style.configure("Subtitle.TLabel", font=("Segoe UI Semibold", 13))
        style.configure("Card.TLabel", background=SURFACE)
        style.configure("Metric.TLabel", background=SURFACE, font=("Segoe UI Semibold", 23))
        style.configure("TButton", background=SURFACE, foreground=TEXT,
                        padding=(12, 8), borderwidth=1, relief="flat")
        style.map("TButton", background=[("active", "#253c57")],
                  foreground=[("disabled", "#64758b")])
        style.configure("Accent.TButton", background=TEAL, foreground=BG,
                        font=("Segoe UI Semibold", 10))
        style.map("Accent.TButton", background=[("active", "#85efdb"), ("disabled", "#244c4c")],
                  foreground=[("disabled", "#8daca9")])
        style.configure("TEntry", fieldbackground=BG, foreground=TEXT, padding=6)
        style.configure("TSpinbox", fieldbackground=BG, foreground=TEXT, padding=6,
                        arrowcolor=MUTED)
        style.configure("TCombobox", fieldbackground=BG, foreground=TEXT,
                        selectbackground=SURFACE, selectforeground=TEXT, padding=5,
                        arrowcolor=MUTED)
        style.map("TCombobox", fieldbackground=[("readonly", BG)],
                  foreground=[("readonly", TEXT), ("disabled", MUTED)])
        style.configure("TCheckbutton", background=PANEL, foreground=TEXT, padding=(0, 5))
        style.map("TCheckbutton", background=[("active", PANEL)])
        style.configure("TRadiobutton", background=PANEL, foreground=TEXT, padding=(0, 4))
        style.map("TRadiobutton", background=[("active", PANEL)])
        style.configure("Horizontal.TScale", background=PANEL, troughcolor=BG)
        style.configure("TNotebook", background=BG, borderwidth=0)
        style.configure("TNotebook.Tab", background=PANEL, foreground=MUTED, padding=(18, 10))
        style.map("TNotebook.Tab", background=[("selected", SURFACE)], foreground=[("selected", TEAL)])
        style.configure("Treeview", background=PANEL, fieldbackground=PANEL,
                        foreground=TEXT, rowheight=27, borderwidth=0)
        style.configure("Treeview.Heading", background=SURFACE, foreground=MUTED,
                        font=("Segoe UI Semibold", 9), relief="flat", padding=(5, 6))
        style.map("Treeview", background=[("selected", "#2c4b66")], foreground=[("selected", TEXT)])
        style.map("Treeview.Heading", background=[("active", SURFACE)])
        self.root.option_add("*TCombobox*Listbox.background", PANEL)
        self.root.option_add("*TCombobox*Listbox.foreground", TEXT)

    def _label(self, parent, text, *, style=None, **kwargs):
        return ttk.Label(parent, text=text, style=style or "TLabel", **kwargs)

    def _build(self):
        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(1, weight=1)
        header = ttk.Frame(self.root, padding=(24, 17, 24, 14))
        header.grid(row=0, column=0, sticky="ew")
        header.columnconfigure(0, weight=1)
        self._label(header, "MiRealmBot", style="Title.TLabel").grid(row=0, column=0, sticky="w")
        self._label(header, "AUTO NEXUS   /   AUTO AIM   /   CONFIGURACIÓN", style="Muted.TLabel",
                    font=("Segoe UI", 9)).grid(row=1, column=0, sticky="w", pady=(2, 0))
        self.status_pill = tk.Label(header, textvariable=self.status_var, background=SURFACE,
                                    foreground=TEAL, padx=15, pady=8, font=("Segoe UI Semibold", 10))
        self.status_pill.grid(row=0, column=1, rowspan=2, sticky="e")
        body = ttk.Frame(self.root, padding=(20, 0, 20, 12))
        body.grid(row=1, column=0, sticky="nsew")
        body.columnconfigure(1, weight=1)
        body.rowconfigure(0, weight=1)
        self.sidebar = ttk.Frame(body, style="Panel.TFrame", padding=20, width=252)
        self.sidebar.grid(row=0, column=0, sticky="nsew", padx=(0, 17))
        self.sidebar.grid_propagate(False)
        self.sidebar.columnconfigure(0, weight=1)
        self._build_parameters()
        main = ttk.Frame(body)
        main.grid(row=0, column=1, sticky="nsew")
        main.columnconfigure(0, weight=1)
        main.rowconfigure(1, weight=1)
        self.banner = tk.Label(main, textvariable=self.banner_var, anchor="w", background="#143732",
                               foreground=TEAL, padx=13, pady=10, font=("Segoe UI Semibold", 10))
        self.banner.grid(row=0, column=0, sticky="ew", pady=(0, 10))
        self.notebook = ttk.Notebook(main)
        self.notebook.grid(row=1, column=0, sticky="nsew")
        self.replay_tab = ttk.Frame(self.notebook, padding=(0, 14, 0, 0))
        self.live_tab = ttk.Frame(self.notebook, padding=(0, 14, 0, 0))
        self.aim_tab = ttk.Frame(self.notebook, padding=(0, 14, 0, 0))
        self.notebook.add(self.replay_tab, text="Reproducir registro")
        self.notebook.add(self.live_tab, text="Juego en vivo")
        self.notebook.add(self.aim_tab, text="AutoAim")
        self.updates_panel = UpdatePanel(self.notebook, can_install=self._can_install_update,
            request_install=self._install_update, service=self.update_service,
            portable=self.portable_updates, auto_check=self.auto_check_updates)
        self.notebook.add(self.updates_panel, text="Actualizaciones")
        self.notebook.bind("<<NotebookTabChanged>>", self._tab_changed)
        self._build_replay()
        self._build_live()
        self._build_autoaim()
        self.footer_var = tk.StringVar(self.root, "Abrir la interfaz no inicia el bot · Las actualizaciones consultan GitHub")
        ttk.Label(self.root, textvariable=self.footer_var, style="Muted.TLabel", padding=(22, 2, 22, 10),
                  font=("Segoe UI", 9)).grid(row=2, column=0, sticky="ew")

    def _build_parameters(self):
        p = self.sidebar
        self.parameter_title = self._label(p, "Parámetros del registro", style="Panel.TLabel",
                                          font=("Segoe UI Semibold", 12))
        self.parameter_title.grid(row=0, column=0, sticky="w", pady=(0, 18))
        self._label(p, "SALIR BAJO", style="PanelMuted.TLabel", font=("Segoe UI Semibold", 9)).grid(row=1, column=0, sticky="w")
        threshold_line = ttk.Frame(p, style="Panel.TFrame")
        threshold_line.grid(row=2, column=0, sticky="ew", pady=(7, 4))
        self.threshold_input = ttk.Spinbox(threshold_line, from_=1, to=99, increment=1,
                                           width=6, textvariable=self.threshold_var,
                                           font=("Segoe UI Semibold", 24))
        self.threshold_input.pack(side="left")
        self._label(threshold_line, "%", style="PanelMuted.TLabel", font=("Segoe UI", 19)).pack(side="left", padx=8)
        self.threshold_slider = ttk.Scale(p, from_=1, to=99, variable=self.slider_var,
                                          command=self._slider_changed)
        self.threshold_slider.grid(row=3, column=0, sticky="ew", pady=(4, 8))
        self.limit_var = tk.StringVar(self.root, "Con 100 HP: salir en 29 o menos")
        self._label(p, "", textvariable=self.limit_var, style="PanelMuted.TLabel",
                    wraplength=205, font=("Segoe UI", 9)).grid(row=4, column=0, sticky="w")
        self.prediction_check = ttk.Checkbutton(p, text="Prever daño de proyectiles", variable=self.prediction_var)
        self.prediction_check.grid(row=5, column=0, sticky="w", pady=(17, 0))
        self._label(p, "Usa el daño efectivo del golpe aceptado por el juego.", style="PanelMuted.TLabel",
                    wraplength=205, font=("Segoe UI", 9)).grid(row=6, column=0, sticky="w", pady=(0, 15))
        self._label(p, "TECLA NEXUS", style="PanelMuted.TLabel", font=("Segoe UI Semibold", 9)).grid(row=7, column=0, sticky="w")
        self.key_combo = ttk.Combobox(p, textvariable=self.key_var,
                                      values=("CTRL", "RCTRL", *tuple("ABCDEFGHIJKLMNOPQRSTUVWXYZ")),
                                      state="readonly", width=12)
        self.key_combo.grid(row=8, column=0, sticky="ew", pady=(6, 2))
        self.key_hint = self._label(p, "Sólo se utiliza en el juego en vivo.", style="PanelMuted.TLabel",
                                   wraplength=205, font=("Segoe UI", 9))
        self.key_hint.grid(row=9, column=0, sticky="w")
        self._label(p, "MODO EN VIVO", style="PanelMuted.TLabel", font=("Segoe UI Semibold", 9)).grid(row=10, column=0, sticky="w", pady=(17, 5))
        self.observe_radio = ttk.Radiobutton(p, text="Observar", variable=self.mode_var, value="observe", command=self._update_banner)
        self.observe_radio.grid(row=11, column=0, sticky="w")
        self.active_radio = ttk.Radiobutton(p, text="Activo · puede enviar la tecla", variable=self.mode_var, value="active", command=self._update_banner)
        self.active_radio.grid(row=12, column=0, sticky="w")
        self.save_button = ttk.Button(p, text="Guardar parámetros", command=self.save_parameters)
        self.save_button.grid(row=13, column=0, sticky="ew", pady=(18, 8))
        self.draft_var = tk.StringVar(self.root)
        self._label(p, "", textvariable=self.draft_var, style="PanelMuted.TLabel", wraplength=205,
                    font=("Segoe UI", 9)).grid(row=14, column=0, sticky="w")
        p.rowconfigure(15, weight=1)
        self._label(p, "PROTECCIONES", style="PanelMuted.TLabel", font=("Segoe UI Semibold", 9)).grid(row=16, column=0, sticky="w", pady=(12, 5))
        self._label(p, "Nexus excluido\nUn intento por mapa\nFiltros de inmunidad sin verificar", style="PanelMuted.TLabel",
                    wraplength=205, font=("Segoe UI", 9)).grid(row=17, column=0, sticky="w")

    def _metric(self, parent, column, heading, color=TEXT):
        card = ttk.Frame(parent, style="Card.TFrame", padding=(13, 9))
        card.grid(row=0, column=column, sticky="nsew", padx=(0, 9 if column < 2 else 0))
        self._label(card, heading, style="Card.TLabel", foreground=MUTED, font=("Segoe UI", 9)).pack(anchor="w")
        var = tk.StringVar(self.root, "—")
        self._label(card, "", textvariable=var, style="Metric.TLabel", foreground=color).pack(anchor="w")
        return var

    def _tree(self, parent, columns, height):
        container = ttk.Frame(parent)
        container.columnconfigure(0, weight=1)
        container.rowconfigure(0, weight=1)
        tree = ttk.Treeview(container, columns=[c[0] for c in columns], show="headings", height=height)
        tree.grid(row=0, column=0, sticky="nsew")
        for key, title, width, stretch in columns:
            tree.heading(key, text=title, anchor="w")
            tree.column(key, width=width, minwidth=min(width, 55), stretch=stretch, anchor="w")
        scroll = ttk.Scrollbar(container, orient="vertical", command=tree.yview)
        scroll.grid(row=0, column=1, sticky="ns")
        tree.configure(yscrollcommand=scroll.set)
        tree.tag_configure("escape", foreground=AMBER)
        tree.tag_configure("damage", foreground=TEAL)
        return container, tree

    def _build_replay(self):
        p = self.replay_tab
        p.columnconfigure(0, weight=1)
        p.rowconfigure(6, weight=1)
        top = ttk.Frame(p)
        top.grid(row=0, column=0, sticky="ew", pady=(0, 9))
        top.columnconfigure(0, weight=1)
        self._label(top, "El mismo registro, otros parámetros", style="Subtitle.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Button(top, text="Abrir registro…", command=self.choose_recording).grid(row=0, column=1, sticky="e")
        self.recording_var = tk.StringVar(self.root, "Cargando registro…")
        self._label(top, "", textvariable=self.recording_var, style="Muted.TLabel", font=("Segoe UI", 9)).grid(row=1, column=0, columnspan=2, sticky="w", pady=(3, 0))
        metrics = ttk.Frame(p)
        metrics.grid(row=1, column=0, sticky="ew", pady=(0, 9))
        for col in range(3):
            metrics.columnconfigure(col, weight=1, uniform="metrics")
        self.observed_var = self._metric(metrics, 0, "HP OBSERVADO", BLUE)
        self.predicted_var = self._metric(metrics, 1, "HP PREVISTO", TEAL)
        self.damage_var = self._metric(metrics, 2, "ÚLTIMO DAÑO EFECTIVO", AMBER)
        self.chart = tk.Canvas(p, background=PANEL, height=150, highlightthickness=0)
        self.chart.grid(row=2, column=0, sticky="ew")
        self.chart.bind("<Configure>", lambda event: self.draw_chart())
        ttk.Style(self.root).configure("Compact.TButton", padding=(6, 2))
        self.zoom_button = ttk.Button(self.chart, text="Ver todo", style="Compact.TButton", command=self.toggle_chart_zoom)
        playback = ttk.Frame(p, padding=(0, 7, 0, 0))
        playback.grid(row=3, column=0, sticky="ew")
        playback.columnconfigure(4, weight=1)
        self.play_button = ttk.Button(playback, text="▶ Reproducir", command=self.toggle_playback)
        self.play_button.grid(row=0, column=0, padx=(0, 5))
        ttk.Button(playback, text="↺", width=3, command=self.reset_playback).grid(row=0, column=1, padx=(0, 6))
        self.first_hit_button = ttk.Button(playback, text="Primer golpe", command=self.jump_to_first_hit)
        self.first_hit_button.grid(row=0, column=2, padx=(0, 7))
        self.speed_combo = ttk.Combobox(playback, values=("1×", "5×", "10×", "30×"), textvariable=self.speed_var, state="readonly", width=4)
        self.speed_combo.grid(row=0, column=3, padx=(0, 10))
        self.scrub = ttk.Scale(playback, from_=0, to=1, variable=self.scrub_var, command=self._scrubbed)
        self.scrub.grid(row=0, column=4, sticky="ew")
        self.time_var = tk.StringVar(self.root, "00:00.0 / 00:00.0")
        self._label(playback, "", textvariable=self.time_var, style="Muted.TLabel", font=("Consolas", 9)).grid(row=0, column=5, padx=(10, 0))
        self.replay_summary_var = tk.StringVar(self.root)
        self._label(p, "", textvariable=self.replay_summary_var, foreground=AMBER, wraplength=820,
                    font=("Segoe UI", 10)).grid(row=4, column=0, sticky="ew", pady=(8, 6))
        self.replay_tables = ttk.Notebook(p)
        self.replay_tables.grid(row=6, column=0, sticky="nsew", pady=(3, 0))
        event_panel = ttk.Frame(self.replay_tables)
        compare_panel = ttk.Frame(self.replay_tables)
        for panel in (event_panel, compare_panel):
            panel.columnconfigure(0, weight=1)
            panel.rowconfigure(0, weight=1)
        self.replay_tables.add(event_panel, text="Eventos del registro")
        self.replay_tables.add(compare_panel, text="Comparar 7 umbrales")
        container, self.event_tree = self._tree(event_panel, (
            ("time", "Tiempo", 68, False), ("event", "Evento", 125, True),
            ("hp", "HP", 55, False), ("predicted", "Previsto", 65, False),
            ("damage", "Daño", 55, False), ("limit", "Límite", 55, False),
            ("action", "Decisión", 175, True)), 5)
        container.grid(row=0, column=0, sticky="nsew")
        self.event_tree.bind("<<TreeviewSelect>>", self._event_selected)
        comparison_title = ttk.Frame(p)
        comparison_title.grid(row=7, column=0, sticky="ew", pady=(11, 5))
        comparison_title.columnconfigure(0, weight=1)
        self._label(comparison_title, "Comparación: doble clic en un umbral para probarlo.", style="Muted.TLabel", font=("Segoe UI", 9)).grid(row=0, column=0, sticky="w")
        self.copy_button = ttk.Button(comparison_title, text="Usar en vivo →", command=self.apply_replay_to_live)
        self.copy_button.grid(row=0, column=1, sticky="e")
        container, self.comparison_tree = self._tree(compare_panel, (
            ("threshold", "Umbral", 65, False), ("time", "Solicita a los", 104, False),
            ("event", "Primer evento que activa", 210, True),
            ("observed", "HP", 55, False), ("predicted", "Previsto", 68, False),
            ("limit", "Límite", 65, False)), 7)
        container.grid(row=0, column=0, sticky="nsew")
        self.comparison_tree.bind("<Double-1>", self._comparison_selected)
        self.limitations_var = tk.StringVar(self.root, "")
        self._label(p, "", textvariable=self.limitations_var, style="Muted.TLabel", wraplength=840,
                    font=("Segoe UI", 9)).grid(row=8, column=0, sticky="ew", pady=(8, 0))

    def _build_live(self):
        p = self.live_tab
        p.columnconfigure(0, weight=1)
        p.rowconfigure(7, weight=1)
        self._label(p, "Tu AutoNexus en el juego", style="Subtitle.TLabel").grid(row=0, column=0, sticky="w")
        self._label(p, "La configuración se aplica al iniciar. Detén la sesión para cambiarla.", style="Muted.TLabel").grid(row=1, column=0, sticky="w", pady=(4, 16))
        metrics = ttk.Frame(p)
        metrics.grid(row=2, column=0, sticky="ew", pady=(0, 16))
        for col in range(3):
            metrics.columnconfigure(col, weight=1, uniform="live_metrics")
        self.live_observed_var = self._metric(metrics, 0, "HP OBSERVADO", BLUE)
        self.live_predicted_var = self._metric(metrics, 1, "HP PREVISTO", TEAL)
        self.live_damage_var = self._metric(metrics, 2, "ÚLTIMO DAÑO EFECTIVO", AMBER)
        detail = ttk.Frame(p, style="Panel.TFrame", padding=17)
        detail.grid(row=3, column=0, sticky="ew")
        detail.columnconfigure(1, weight=1)
        self.live_vars = {}
        for row, (key, title, initial) in enumerate((
            ("map", "Mapa", "—"), ("decision", "Decisión", "Sin sesión"),
            ("focus", "Ventana del juego", "Sin comprobar"), ("delivery", "Última salida", "Ninguna"),
            ("applied", "Parámetros aplicados", "Todavía no se ha iniciado una sesión"),
            ("log", "Registro", "—"), ("error", "Último error", "Ninguno"))):
            self._label(detail, title, style="PanelMuted.TLabel", width=20).grid(row=row, column=0, sticky="nw", pady=5)
            value = tk.StringVar(self.root, initial)
            self.live_vars[key] = value
            self._label(detail, "", textvariable=value, style="Panel.TLabel", wraplength=570,
                        foreground=RED if key == "error" else TEXT).grid(row=row, column=1, sticky="nw", pady=5)
        buttons = ttk.Frame(p)
        buttons.grid(row=4, column=0, sticky="ew", pady=(15, 10))
        self.start_button = ttk.Button(buttons, text="Iniciar observación", style="Accent.TButton", command=self.start_live)
        self.start_button.pack(side="left", padx=(0, 9))
        self.stop_button = ttk.Button(buttons, text="Detener", command=self.stop_live, state="disabled")
        self.stop_button.pack(side="left")
        self.live_hint_var = tk.StringVar(self.root, "Abre RotMG Exalt y pulsa Iniciar. Después vuelve a la ventana del juego.")
        self._label(p, "", textvariable=self.live_hint_var, style="Muted.TLabel", wraplength=800).grid(row=5, column=0, sticky="ew", pady=(0, 16))
        self._label(p, "ACTIVIDAD DE LA SESIÓN", style="Muted.TLabel", font=("Segoe UI Semibold", 9)).grid(row=6, column=0, sticky="w", pady=(0, 5))
        self.live_log = tk.Text(p, height=6, background=PANEL, foreground=MUTED, borderwidth=0,
                                highlightthickness=0, wrap="word", font=("Consolas", 9), padx=12, pady=10,
                                state="disabled")
        self.live_log.grid(row=7, column=0, sticky="nsew")
        self._label(p, "La predicción cubre golpes aceptados de proyectiles; no anticipa trayectorias ni garantiza una salida a tiempo.",
                    style="Muted.TLabel", wraplength=810, font=("Segoe UI", 9)).grid(row=8, column=0, sticky="ew", pady=(12, 0))

    def _build_autoaim(self):
        p = self.aim_tab
        p.columnconfigure(0, weight=1)
        self._label(p, "AutoAim · Close to Mouse", style="Subtitle.TLabel").grid(row=0, column=0, sticky="w")
        self._label(p, "Elige el enemigo cercano al cursor. Se aplica al iniciar la sesión en vivo.",
                    style="Muted.TLabel", wraplength=740).grid(row=1, column=0, sticky="w", pady=(5, 14))
        controls = ttk.Frame(p, style="Panel.TFrame", padding=18)
        controls.grid(row=2, column=0, sticky="ew")
        controls.columnconfigure(1, weight=1)
        self.aim_enabled_check = ttk.Checkbutton(controls, text="Activar AutoAim",
                                                variable=self.aim_enabled_var)
        self.aim_enabled_check.grid(row=0, column=0, columnspan=2, sticky="w")
        self._label(controls, "Modo", style="PanelMuted.TLabel").grid(row=1, column=0, sticky="w", pady=8)
        self._label(controls, "Close to Mouse", style="Panel.TLabel").grid(row=1, column=1, sticky="w")
        self._label(controls, "Extrapolation", style="PanelMuted.TLabel").grid(row=2, column=0, sticky="w", pady=8)
        self.extrapolation_input = ttk.Spinbox(controls, from_=0, to=1000000000, increment=1,
                                              textvariable=self.extrapolation_var, width=12)
        self.extrapolation_input.grid(row=2, column=1, sticky="w")
        self._label(controls, "0 apunta a la posición actual. Un valor mayor adelanta más la puntería "
                    "según el movimiento del enemigo.",
                    style="PanelMuted.TLabel", wraplength=650, font=("Segoe UI", 9)).grid(
                        row=3, column=0, columnspan=2, sticky="w", pady=(0, 8))
        self._label(controls, "Radio junto al cursor", style="PanelMuted.TLabel").grid(row=4, column=0, sticky="w", pady=8)
        self.aim_radius_input = ttk.Spinbox(controls, from_=0.1, to=1000000000, increment=.5,
                                          textvariable=self.aim_radius_var, width=12)
        self.aim_radius_input.grid(row=4, column=1, sticky="w")
        self._label(controls, "Con AutoAim habilitado y modo Activo, apunta mientras el autofire del juego está encendido. "
                    "No necesitas mantener una tecla pulsada. Observar calcula sin alterar los disparos.",
                    style="PanelMuted.TLabel", wraplength=680).grid(row=5, column=0, columnspan=2, sticky="w", pady=(10, 0))
        bindings = ttk.Frame(p, style="Panel.TFrame", padding=18)
        bindings.grid(row=3, column=0, sticky="ew", pady=(12, 12))
        bindings.columnconfigure(0, weight=1)
        self.detect_keys_check = ttk.Checkbutton(bindings, text="Leer teclas del juego",
            variable=self.detect_keys_var, command=self.refresh_game_bindings)
        self.detect_keys_check.grid(row=0, column=0, sticky="w")
        self.refresh_keys_button = ttk.Button(bindings, text="Volver a leer teclas", command=self.refresh_game_bindings)
        self.refresh_keys_button.grid(row=0, column=1, sticky="e")
        self._label(bindings, "", textvariable=self.bindings_var, style="PanelMuted.TLabel",
                    wraplength=680).grid(row=1, column=0, columnspan=2, sticky="w", pady=(8, 0))
        self.aim_status_var = tk.StringVar(self.root, "Sin sesión · AutoAim no está actuando")
        self._label(p, "", textvariable=self.aim_status_var, wraplength=730).grid(row=4, column=0, sticky="ew", pady=(5, 14))
        buttons = ttk.Frame(p)
        buttons.grid(row=5, column=0, sticky="ew")
        self.aim_start_button = ttk.Button(buttons, text="Iniciar observación", style="Accent.TButton", command=self.start_live)
        self.aim_start_button.pack(side="left", padx=(0, 9))
        self.aim_stop_button = ttk.Button(buttons, text="Detener", command=self.stop_live, state="disabled")
        self.aim_stop_button.pack(side="left")

    def refresh_game_bindings(self):
        if self.runtime_thread is not None:
            return
        self._sync_context()
        if not self.detect_keys_var.get():
            self.bindings_var.set("Lectura desactivada. Nexus usará la tecla manual de la izquierda; "
                                  "AutoAim observa el estado de autofire dentro del juego.")
            self.key_hint.configure(text="Tecla manual para la próxima sesión en vivo.")
            return
        try:
            self.game_bindings = self.settings_reader()
        except Exception:
            self.game_bindings = {}
            self.bindings_var.set("No se pudieron leer las teclas guardadas. Se comprobarán dentro del juego al iniciar.")
            self.key_hint.configure(text="La tecla Nexus se comprobará dentro del juego al iniciar.")
            return
        nexus, autofire = (self.game_bindings.get(name) for name in ("nexus", "autofire"))
        try:
            detected = normalize_nexus_key(nexus.key if nexus else None)
        except ValueError:
            detected = None
        if detected:
            self.key_var.set(detected)
            nexus_text = f"Nexus: {detected} (guardada en el juego); se confirmará dentro del juego al iniciar."
            self.key_hint.configure(text=f"Nexus guardada: {detected}. Se confirmará dentro del juego al iniciar.")
        else:
            nexus_text = "Nexus: sin valor guardado reconocido; se leerá dentro del juego al iniciar."
            self.key_hint.configure(text="La tecla Nexus se leerá dentro del juego al iniciar.")
        fire_text = (f" Autofire: {autofire.key} (guardada en el juego); se confirmará al iniciar." if autofire and autofire.key else
                     " Autofire: sin valor guardado reconocido; se leerá dentro del juego al iniciar.")
        self.bindings_var.set(nexus_text + fire_text)

    def _updates_selected(self):
        return self.notebook.select() == str(self.updates_panel)

    def _tab_changed(self, event=None):
        tab = self.notebook.select()
        selected = ("live" if tab in (str(self.live_tab), str(self.aim_tab)) else
                    "replay" if tab == str(self.replay_tab) else self.active_context)
        if selected != self.active_context:
            self._store_draft()
            self.active_context = selected
            draft = self.live_draft if selected == "live" else self.replay_draft
            self._loading_controls = True
            self.threshold_var.set(f"{draft['threshold_percent']:g}")
            self.prediction_var.set(draft["prediction_enabled"])
            self._loading_controls = False
            self._parameter_changed()
        self._sync_context()

    def _store_draft(self):
        try:
            value = self._threshold()
        except ValueError:
            return
        draft = self.live_draft if self.active_context == "live" else self.replay_draft
        draft.update(threshold_percent=value, prediction_enabled=self.prediction_var.get())

    def _threshold(self):
        try:
            value = float(self.threshold_var.get().replace(",", "."))
        except (ValueError, tk.TclError):
            raise ValueError("Escribe un porcentaje entre 1 y 99") from None
        if not math.isfinite(value) or not 1 <= value <= 99:
            raise ValueError("El porcentaje debe estar entre 1 y 99")
        return value

    def _slider_changed(self, value):
        if self._loading_controls:
            return
        self.threshold_var.set(str(round(float(value))))

    def _parameter_changed(self, *unused):
        if self._loading_controls:
            return
        try:
            threshold = self._threshold()
            self.slider_var.set(threshold)
            preview = AutoNexus(threshold / 100, use_state_filters=False)
            preview.update_health(100, 100)
            limit = preview.evaluate().limit
            self.limit_var.set(f"Con 100 HP: límite {limit}\nSale con {limit - 1} HP o menos.")
            self._store_draft()
            self.draft_var.set("Sólo cambia esta reproducción." if self.active_context == "replay"
                               else "Borrador para la próxima sesión. Guardar conserva una copia del archivo anterior.")
        except ValueError as error:
            self.limit_var.set(str(error))
            return
        if self.active_context == "replay":
            self._cancel_recompute()
            def run_pending():
                self._update_after = None
                self.recompute_replay()
            self._update_after = self.root.after(100, run_pending)

    def _cancel_recompute(self):
        if self._update_after is not None:
            self.root.after_cancel(self._update_after)
            self._update_after = None

    def _sync_context(self):
        live = self.active_context == "live"
        running = self.runtime_thread is not None
        updates = self._updates_selected()
        installing = self.updates_panel.installing
        self.parameter_title.configure(text="Actualizaciones" if updates else "Parámetros del juego" if live else "Parámetros del registro")
        state = "disabled" if updates or installing or (live and running) else "normal"
        self.threshold_input.configure(state=state)
        self.threshold_slider.configure(state=state)
        self.prediction_check.configure(state=state)
        self.key_combo.configure(state="readonly" if live and not running and not updates and not installing
                                 and not self.detect_keys_var.get() else "disabled")
        for widget in (self.observe_radio, self.active_radio):
            widget.configure(state="normal" if live and not running and not updates and not installing else "disabled")
        self.save_button.configure(state="normal" if live and not running and not updates and not installing else "disabled")
        for widget in (self.aim_enabled_check, self.extrapolation_input, self.aim_radius_input,
                       self.detect_keys_check, self.refresh_keys_button):
            widget.configure(state="disabled" if running or installing else "normal")
        for widget in (self.start_button, self.aim_start_button):
            widget.configure(state="normal" if not running and self.cleanup_complete and not installing and not self.closing else "disabled")
        for widget in (self.stop_button, self.aim_stop_button):
            widget.configure(state="normal" if running and not self.stop_event.is_set() else "disabled")
        self.copy_button.configure(state="disabled" if running else "normal")
        self.draft_var.set("Detén el bot antes de instalar una actualización." if updates else
                           "Sólo cambia esta reproducción. «Usar en vivo» copia el borrador." if not live else
                           "Parámetros bloqueados durante la sesión." if running else "Borrador para la próxima sesión; guardar no inicia el bot.")
        self._update_banner()

    def _update_banner(self):
        if self._updates_selected() and self.runtime_thread is None:
            text, color, bg = "ACTUALIZACIONES · CONSULTA Y DESCARGA DESDE GITHUB", BLUE, "#192e49"
        elif self.active_context == "replay":
            text, color, bg = "SIMULACIÓN · SIN CONEXIÓN AL JUEGO", TEAL, "#143732"
            if self.runtime_thread is not None:
                if self.live_active:
                    text = "REPRODUCCIÓN   |   ACTIVO: PUEDE ENVIAR " + self.applied_key
                    color, bg = AMBER, "#44321f"
                else:
                    text = "REPRODUCCIÓN   |   OBSERVANDO EL JUEGO: NO ENVÍA TECLAS"
                    color, bg = BLUE, "#192e49"
        elif self.runtime_thread is not None:
            text, color, bg = (("ACTIVO · PUEDE ENVIAR " + self.applied_key, AMBER, "#44321f") if self.live_active else
                               ("SOLO OBSERVACIÓN · NO ENVÍA TECLAS", BLUE, "#192e49"))
        elif self.mode_var.get() == "active":
            text, color, bg = "DETENIDO · MODO ACTIVO SE APLICA AL PULSAR INICIAR", AMBER, "#44321f"
        else:
            text, color, bg = "DETENIDO · OBSERVACIÓN SE APLICA AL PULSAR INICIAR", BLUE, "#192e49"
        if self.runtime_thread is not None and self.live_active and self.applied_detect_keys:
            if self.nexus_binding_status == "waiting":
                text = "ACTIVO · NEXUS ESPERA LEER LA TECLA DEL JUEGO"
            elif self.nexus_binding_status == "unsupported":
                text = "ACTIVO · TECLA NEXUS NO ADMITIDA"
        self.banner_var.set(text)
        self.banner.configure(background=bg, foreground=color)
        self.start_button.configure(text="Iniciar AutoNexus activo" if self.mode_var.get() == "active" else "Iniciar observación")
        self.aim_start_button.configure(text="Iniciar sesión activa" if self.mode_var.get() == "active" else "Iniciar observación")

    def choose_recording(self):
        selected = filedialog.askopenfilename(parent=self.root, title="Abrir registro de MiRealmBot",
                                             initialdir=ROOT / "analysis", filetypes=(("Registro JSONL", "*.jsonl"), ("Todos", "*.*")))
        if selected:
            self._load_recording(Path(selected))

    def _load_recording(self, path):
        try:
            recording = load_recording(path)
        except (OSError, ValueError) as error:
            self.last_error = str(error)
            self.recording_var.set("No se pudo abrir el registro")
            self.limitations_var.set(str(error))
            return False
        self.recording = recording
        self.playing = False
        self.replay_position = 0
        self.recording_var.set(f"{Path(recording.source_path).name}  ·  {recording.damage_count} golpes  ·  {recording.duration_seconds:.1f} s")
        self.scrub.configure(to=max(recording.duration_seconds, 0.001))
        self.limitations_var.set("Captura parcial: algunos eventos usan un fotograma asociado. Esta reproducción no demuestra que el personaje se hubiera salvado.")
        self.recompute_replay()
        self.jump_to_first_hit()
        return True

    def recompute_replay(self):
        # Direct callers supersede the debounce too. Keep the pending Tcl
        # callback owned until canceled or until its own wrapper begins.
        self._cancel_recompute()
        if self.recording is None:
            return
        try:
            draft = self.replay_draft
            self.replay_result = replay_recording(self.recording, threshold_percent=draft["threshold_percent"],
                prediction_enabled=draft["prediction_enabled"],
                use_state_filters=self.live_mapping.get("use_state_filters", False),
                excluded_maps=tuple(self.live_mapping.get("excluded_maps", ["Nexus"])))
            comparisons = compare_thresholds(self.recording, prediction_enabled=draft["prediction_enabled"],
                use_state_filters=self.live_mapping.get("use_state_filters", False),
                excluded_maps=tuple(self.live_mapping.get("excluded_maps", ["Nexus"])))
        except (ValueError, OSError) as error:
            self.last_error = str(error)
            self.replay_summary_var.set(str(error))
            return
        self.replay_rows = self.replay_result.rows
        self.event_tree.delete(*self.event_tree.get_children())
        previous = None
        for index, row in enumerate(self.replay_rows):
            signature = (row.get("observed_hp"), row.get("predicted_hp"), row.get("map_name"), row.get("action"))
            if row.get("damage") is None and signature == previous and row.get("event_label") not in ("Sin datos",):
                continue
            previous = signature
            tags = ("escape",) if row.get("action") == "escape" else ("damage",) if row.get("damage") is not None else ()
            self.event_tree.insert("", "end", iid=str(index), values=(f"{row['time_seconds']:.2f} s", row["event_label"],
                number(row.get("observed_hp")), number(row.get("predicted_hp")), number(row.get("damage")),
                number(row.get("limit")), "SOLICITAR NEXUS" if row.get("action") == "escape" else short_reason(row.get("reason"))), tags=tags)
        self.comparison_tree.delete(*self.comparison_tree.get_children())
        for row in comparisons:
            when = row.get("first_escape_seconds")
            self.comparison_tree.insert("", "end", values=(f"{row['threshold_percent']:g} %", "—" if when is None else f"{when:.2f} s",
                row.get("event_label") or "Sin solicitud", number(row.get("observed_hp")),
                number(row.get("predicted_hp")), number(row.get("limit"))), tags=("escape",) if when is not None else ())
        first = self.replay_result.summary.get("first_escape")
        if first:
            self.replay_summary_var.set(f"Con {self.replay_draft['threshold_percent']:g} %: primera solicitud a {first['time_seconds']:.2f} s · "
                f"{first['event_label']} · HP previsto {number(first.get('predicted_hp'))} / límite {number(first.get('limit'))}")
        else:
            self.replay_summary_var.set("Con estos parámetros no se solicita salida en el registro disponible.")
        self._current_row_index = None
        self._show_position()

    def _current_row(self):
        current = None
        for row in self.replay_rows:
            if row["time_seconds"] > self.replay_position:
                break
            current = row
        return current

    def _show_position(self):
        if self.recording is None:
            return
        self.scrub_var.set(self.replay_position)
        self.time_var.set(f"{clock_text(self.replay_position)} / {clock_text(self.recording.duration_seconds)}")
        row = self._current_row()
        if row:
            self.observed_var.set(f"{number(row.get('observed_hp'))} / {number(row.get('max_hp'))}")
            self.predicted_var.set(number(row.get("predicted_hp")) if self.replay_draft["prediction_enabled"] else "Desactivado")
            damage = next((r.get("damage") for r in reversed(self.replay_rows)
                           if r["time_seconds"] <= self.replay_position and r.get("damage") is not None), None)
            self.damage_var.set(number(damage))
        self.draw_chart()

    def _scrubbed(self, value):
        self.replay_position = float(value)
        self._show_position()

    def toggle_playback(self):
        if self.recording is None:
            return
        if self.replay_position >= self.recording.duration_seconds:
            self.replay_position = 0
        self.playing = not self.playing
        self.play_stamp = time.monotonic()
        self.play_button.configure(text="Ⅱ Pausar" if self.playing else "▶ Reproducir")

    def reset_playback(self):
        self.playing = False
        self.play_button.configure(text="▶ Reproducir")
        self.replay_position = 0
        self._show_position()

    def jump_to_first_hit(self):
        first = next((row for row in self.replay_rows if row.get("damage") is not None), None)
        if first is not None:
            self.replay_position = first["time_seconds"]
            self._show_position()
            item = str(self.replay_rows.index(first))
            if self.event_tree.exists(item):
                self.event_tree.selection_set(item)
                self.event_tree.see(item)

    def _event_selected(self, event=None):
        selected = self.event_tree.selection()
        if selected:
            self.replay_position = self.replay_rows[int(selected[0])]["time_seconds"]
            self._show_position()

    def _comparison_selected(self, event=None):
        selected = self.comparison_tree.selection()
        if selected:
            self.threshold_var.set(self.comparison_tree.item(selected[0], "values")[0].replace(" %", ""))

    def toggle_chart_zoom(self):
        self.chart_zoomed = not self.chart_zoomed
        self.zoom_button.configure(text="Ver todo" if self.chart_zoomed else "Zona de impactos")
        self.draw_chart()

    def draw_chart(self):
        c = self.chart
        c.delete("all")
        w, h = c.winfo_width(), c.winfo_height()
        if w < 100 or not self.replay_rows or self.recording is None:
            return
        left, right, top, bottom = 42, w - 16, 30, h - 24
        duration = max(self.recording.duration_seconds, 0.001)
        impacts = [r["time_seconds"] for r in self.replay_rows if r.get("damage") is not None]
        view_start, view_end = 0.0, duration
        if self.chart_zoomed and impacts:
            view_start = max(0.0, min(impacts) - 1.0)
            view_end = min(duration, max(impacts) + 1.0)
        view_span = max(.001, view_end - view_start)
        maxima = [r.get("max_hp") or 0 for r in self.replay_rows]
        values = [r.get(name) for r in self.replay_rows for name in ("observed_hp", "predicted_hp") if r.get(name) is not None]
        high = max(1, *maxima, *values)
        low = min(0, *values)
        margin = max(5, (high - low) * .05)
        low, high = low - margin, high + margin
        def x(value):
            return left + (right - left) * (value - view_start) / view_span
        def y(value):
            return bottom - (bottom - top) * (value - low) / (high - low)
        for fraction in (0, .5, 1):
            value = round(low + (high - low) * fraction)
            c.create_line(left, y(value), right, y(value), fill=BORDER)
            c.create_text(left - 8, y(value), text=str(value), fill=MUTED, anchor="e", font=("Segoe UI", 8))
        c.create_text(left, 13, text="● HP observado", fill=BLUE, anchor="w", font=("Segoe UI", 9))
        c.create_text(left + 126, 13, text="● HP previsto", fill=TEAL, anchor="w", font=("Segoe UI", 9))
        c.create_text(left + 243, 13, text="– Límite", fill=AMBER, anchor="w", font=("Segoe UI", 9))
        c.create_window(right - 56, 13, window=self.zoom_button, width=112, height=25)
        for name, color, dash in (("limit", AMBER, (5, 4)), ("observed_hp", BLUE, None),
                                   ("predicted_hp", TEAL, None)):
            if name == "predicted_hp" and not self.replay_draft["prediction_enabled"]:
                continue
            segment, previous, before_view = [], None, None
            for row in self.replay_rows:
                value = row.get(name)
                moment = row["time_seconds"]
                if moment < view_start:
                    before_view = value
                    continue
                if moment > view_end:
                    break
                if not segment and previous is None and before_view is not None:
                    segment = [left, y(before_view)]
                    previous = (left, y(before_view))
                    before_view = None
                if value is None:
                    if len(segment) >= 4:
                        c.create_line(*segment, fill=color, width=2, dash=dash)
                    segment, previous = [], None
                    continue
                point = (x(moment), y(value))
                if previous is not None:
                    segment.extend((point[0], previous[1]))
                segment.extend(point)
                previous = point
            if previous is not None:
                segment.extend((right, previous[1]))
            if len(segment) >= 4:
                c.create_line(*segment, fill=color, width=2, dash=dash)
        for row in self.replay_rows:
            if row.get("damage") is not None and view_start <= row["time_seconds"] <= view_end:
                px = x(row["time_seconds"])
                c.create_line(px, top, px, bottom, fill="#3d5369", dash=(2, 3))
                c.create_text(px - 4, bottom - 3, text=f"−{row['damage']}", fill=AMBER, anchor="se", font=("Segoe UI Semibold", 9))
        first = self.replay_result.summary.get("first_escape") if self.replay_result else None
        if first and view_start <= first["time_seconds"] <= view_end:
            px = x(first["time_seconds"])
            c.create_polygon(px - 5, top - 2, px + 5, top - 2, px, top + 6, fill=AMBER)
        if view_start <= self.replay_position <= view_end:
            cursor = x(self.replay_position)
            c.create_line(cursor, top, cursor, bottom, fill=TEXT, width=1)
        else:
            c.create_text((left + right) / 2, bottom - 5, text="El cursor está fuera de esta zona", fill=MUTED,
                          anchor="s", font=("Segoe UI", 8))
        c.create_text(left, h - 8, text=f"{view_start:.1f} s", fill=MUTED, anchor="w", font=("Segoe UI", 8))
        c.create_text(right, h - 8, text=f"{view_end:.1f} s", fill=MUTED, anchor="e", font=("Segoe UI", 8))

    def apply_replay_to_live(self):
        if self.runtime_thread is not None:
            return
        self._store_draft()
        self.live_draft = dict(self.replay_draft)
        self.notebook.select(self.live_tab)
        self._tab_changed()
        self.footer_var.set("Parámetros copiados al borrador en vivo. Aún no se guardaron ni se inició el bot.")

    def _live_config(self):
        self._store_draft()
        data = dict(self.live_mapping)
        data.update(threshold=self.live_draft["threshold_percent"] / 100,
                    prediction_enabled=self.live_draft["prediction_enabled"], nexus_key=self.key_var.get(),
                    detect_game_keys=self.detect_keys_var.get(), autoaim={
                        "enabled": self.aim_enabled_var.get(), "mode": "close_to_mouse",
                        "activation": "autofire", "extrapolation": self._aim_number(self.extrapolation_var, "Extrapolation"),
                        "mouse_radius": self._aim_number(self.aim_radius_var, "Radio junto al cursor")})
        # Validate even when text did not become a valid draft.
        if self.active_context == "live":
            data["threshold"] = self._threshold() / 100
        return config_from_mapping(data)

    @staticmethod
    def _aim_number(variable, label):
        try:
            return float(variable.get().replace(",", "."))
        except (ValueError, tk.TclError):
            raise ValueError(f"{label} debe ser un número válido") from None

    def save_parameters(self):
        if self.runtime_thread is not None or self.active_context != "live" or self._updates_selected() or self.updates_panel.installing:
            return
        try:
            config = self._live_config()
            backup = save_config(self.config_path, config)
        except (OSError, ValueError, TypeError) as error:
            self.last_error = str(error)
            messagebox.showerror("No se pudo guardar", str(error), parent=self.root)
            return
        self.live_mapping = config_to_mapping(config)
        self.footer_var.set("Parámetros guardados. " + (f"Copia anterior: {Path(backup).name}" if backup else "Sin cambios pendientes."))
        self.draft_var.set("Guardado. Se aplicará al próximo inicio.")

    def start_live(self):
        if self.active_context != "live" or self._updates_selected() or self.runtime_thread is not None or not self.cleanup_complete or self.closing or self.updates_panel.installing:
            return False
        if self.detect_keys_var.get():
            self.refresh_game_bindings()
        try:
            config = self._live_config()
        except (ValueError, TypeError) as error:
            self.last_error = str(error)
            messagebox.showerror("Parámetros inválidos", str(error), parent=self.root)
            return False
        self.stop_event = threading.Event()
        self.runtime_inbox = RuntimeInbox()
        self._runtime_finished_seen = False
        self.cleanup_complete = False
        self.live_active = self.mode_var.get() == "active"
        self.applied_key = config.nexus_key
        self.applied_config = config
        self.applied_detect_keys = config.detect_game_keys
        self.nexus_binding_status = "waiting" if config.detect_game_keys else "manual"
        self.live_status = "Conectando"
        self.status_var.set(self.live_status)
        self.live_vars["error"].set("Ninguno")
        self.live_vars["delivery"].set("Ninguna")
        self.aim_status_var.set("Esperando datos de AutoAim del juego…" if config.autoaim.enabled else "AutoAim desactivado")
        self._render_applied_config()
        self.live_hint_var.set("Conectando con el juego…")
        def worker():
            try:
                code = self.runtime_runner(config, active=self.live_active, stop_event=self.stop_event,
                                           on_update=self.runtime_inbox.put)
            except Exception as error:
                self.runtime_inbox.put({"event": "worker_failure", "error": str(error)})
                code = 1
            finally:
                self.runtime_inbox.put({"event": "worker_done", "code": locals().get("code", 1)})
        self.runtime_thread = threading.Thread(target=worker, name="MiRealmBot-runtime", daemon=False)
        self.runtime_thread.start()
        self._sync_context()
        return True

    def _can_install_update(self):
        if self.runtime_thread is not None:
            return False, "Detén AutoNexus y AutoAim y espera a que termine la sesión."
        if not self.cleanup_complete:
            return False, "No se confirmó la limpieza del juego. Cierra el juego y vuelve a abrir MiRealmBot antes de instalar."
        if self.closing:
            return False, "MiRealmBot ya se está cerrando."
        return True, ""

    def _render_applied_config(self):
        if self.applied_config is not None:
            config = self.applied_config
            key = ("Nexus pendiente de lectura" if self.nexus_binding_status == "waiting" else
                   "Tecla Nexus no admitida" if self.nexus_binding_status == "unsupported" else self.applied_key)
            self.live_vars["applied"].set(f"{config.threshold * 100:g} % · {key} · predicción " +
                ("sí" if config.prediction_enabled else "no") + " · AutoAim " + ("sí" if config.autoaim.enabled else "no"))

    def _show_runtime_bindings(self, bindings):
        if not isinstance(bindings, dict) or not self.applied_detect_keys:
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
        nexus = resolved.get("nexus")
        if nexus:
            self.nexus_binding_status = "detected"
            try:
                nexus = normalize_nexus_key(nexus)
            except ValueError:
                nexus = None
        if nexus:
            self.applied_key = nexus
            self.key_var.set(nexus)
            self._render_applied_config()
            self.key_hint.configure(text=f"Nexus detectada dentro del juego: {nexus}.")
        parts = [f"Nexus: {nexus} (detectada dentro del juego)." if nexus else "Nexus: sin lectura reciente reconocida."]
        parts.append(f"Autofire: {resolved['autofire']} (detectada dentro del juego)." if "autofire" in resolved else
                     "Autofire: sin lectura reciente reconocida.")
        self.bindings_var.set(" ".join(parts))
        self._update_banner()

    def _install_update(self, prepared):
        allowed, reason = self._can_install_update()
        if not allowed:
            self.updates_panel.recover_install_error(reason)
            return False
        launch_update(prepared)
        self.close()
        return True

    def stop_live(self):
        if self.runtime_thread is None:
            return
        self.stop_event.set()
        self.live_status = "Deteniendo"
        self.status_var.set(self.live_status)
        self.live_hint_var.set("Deteniendo y retirando los hooks. Espera a que termine la limpieza.")
        self._sync_context()

    def _append_log(self, text):
        self.live_log.configure(state="normal")
        self.live_log.insert("end", datetime.now().strftime("%H:%M:%S") + "  " + str(text) + "\n")
        count = int(self.live_log.index("end-1c").split(".")[0])
        if count > 180:
            self.live_log.delete("1.0", f"{count - 160}.0")
        self.live_log.see("end")
        self.live_log.configure(state="disabled")

    def _handle_runtime_record(self, record):
        kind = record.get("event")
        if record.get("log_path"):
            self.live_vars["log"].set(record["log_path"])
        if kind == "lifecycle":
            status = record.get("status")
            if status == "finished":
                self._runtime_finished_seen = True
                self.cleanup_complete = record.get("cleanup_complete") is True
                self.live_status = "Detenido" if self.cleanup_complete and record.get("code") == 0 else "Error"
                errors = record.get("errors") or []
                if errors:
                    self.live_vars["error"].set("; ".join(map(str, errors)))
                self.live_hint_var.set("Sesión cerrada. El juego sigue abierto." if self.cleanup_complete else
                                       "La limpieza quedó incompleta. No se permite iniciar otra sesión.")
            elif status == "running":
                self.live_status = "Activo" if self.live_active else "Observando"
                self.live_hint_var.set("Vuelve a la ventana del juego. AutoAim actúa en modo Activo cuando el autofire está encendido.")
            elif status == "stopping":
                self.live_status = "Deteniendo"
            else:
                self.live_status = "Conectando"
            self.status_var.set(self.live_status)
            self._append_log(self.live_status)
            self._update_banner()
        elif kind in ("error", "worker_failure"):
            error = str(record.get("error") or record.get("message") or record)
            self.last_error = error
            self.live_vars["error"].set(error)
            self._append_log(error)
        elif kind == "cleanup":
            self._append_log("Limpieza: " + ("completa" if record.get("complete", record.get("cleanup_complete")) else str(record.get("errors", "en curso"))))
        elif kind == "telemetry":
            if self.applied_detect_keys and record.get("nexus_binding_status") in ("waiting", "unsupported", "detected"):
                self.nexus_binding_status = record["nexus_binding_status"]
            self._show_runtime_bindings(record.get("game_bindings"))
            self._render_applied_config()
            self._update_banner()
            frame, prediction = record.get("frame") or {}, record.get("prediction") or {}
            self.live_observed_var.set(f"{number(frame.get('player_hp'))} / {number(frame.get('max_hp'))}")
            self.live_predicted_var.set(number(prediction.get("tracked_hp")) if prediction.get("enabled") else "Desactivado")
            self.live_vars["map"].set(record.get("map_name") or frame.get("map_name") or "Sin datos recientes")
            decision = record.get("decision") or {}
            self.live_vars["decision"].set("SOLICITAR NEXUS" if decision.get("action") == "escape" else short_reason(decision.get("reason")))
            if self.applied_detect_keys and self.nexus_binding_status == "waiting":
                self.live_vars["decision"].set("Esperando tecla Nexus del juego")
                self.key_hint.configure(text="Esperando una lectura reciente de la tecla Nexus dentro del juego.")
                self.live_hint_var.set("Esperando tecla Nexus del juego. La sesión aún no puede solicitar una salida.")
            elif self.applied_detect_keys and self.nexus_binding_status == "unsupported":
                self.live_vars["decision"].set("Tecla Nexus no admitida")
                self.key_hint.configure(text="Tecla no admitida. Detén la sesión y configura una letra o Control.")
                self.live_hint_var.set("La tecla Nexus del juego no está admitida. Detén la sesión y configura una letra o Control; "
                                       "o desmarca «Leer teclas del juego» y elige una tecla manual que coincida con el juego.")
            elif record.get("nexus_binding_status") in ("detected", "manual"):
                self.live_hint_var.set("Vuelve a la ventana del juego. AutoAim actúa en modo Activo cuando el autofire está encendido.")
            focus = record.get("game_foreground")
            self.live_vars["focus"].set("En primer plano" if focus is True else "Vuelve a la ventana del juego" if focus is False else "Sin comprobar")
            delivery = record.get("delivery") or {}
            if delivery.get("status") not in (None, "none"):
                labels = {"observe_only": "Sólo observación · no se envió la tecla", "key_sent": "Tecla enviada · Nexus sin confirmar",
                          "blocked": "Tecla bloqueada · no se reintentará en este mapa", "failed": "Falló el envío de la tecla"}
                self.live_vars["delivery"].set(labels.get(delivery.get("status"), str(delivery.get("status"))))
            aim = record.get("autoaim")
            if isinstance(aim, dict):
                if aim.get("enabled") is False:
                    text = "AutoAim desactivado"
                else:
                    fire = aim.get("autofire")
                    fire_text = "encendido" if fire is True else "apagado" if fire is False else "sin confirmar"
                    authority = ("Pausado" if aim.get("paused") is True else "Activo" if aim.get("active") is True
                                 else "Observación · no altera los disparos" if aim.get("active") is False
                                 else "Estado sin confirmar")
                    text = f"{authority} · autofire {fire_text}"
                    if aim.get("target_id") is not None:
                        text += f" · enemigo {aim['target_id']}"
                    if isinstance(aim.get("angle"), (int, float)) and math.isfinite(aim["angle"]):
                        text += f" · ángulo {aim['angle']:.2f}"
                    if aim.get("status"):
                        text += " · " + str(aim["status"])[:160]
                self.aim_status_var.set(text)

    def _tick(self):
        self.updates_panel.tick()
        if self.playing and self.recording is not None:
            now = time.monotonic()
            speed = float(self.speed_var.get().rstrip("×"))
            self.replay_position = min(self.recording.duration_seconds, self.replay_position + (now - self.play_stamp) * speed)
            self.play_stamp = now
            self._show_position()
            if self.replay_position >= self.recording.duration_seconds:
                self.playing = False
                self.play_button.configure(text="▶ Reproducir")
        critical, latest, notes, damage = self.runtime_inbox.drain()
        for record in critical:
            self._handle_runtime_record(record)
        if latest:
            self._handle_runtime_record(latest)
        if damage is not None:
            self.live_damage_var.set(str(damage))
        for note in notes:
            self._append_log(note)
        if self.runtime_thread is not None and not self.runtime_thread.is_alive():
            self.runtime_thread.join(timeout=0)
            self.runtime_thread = None
            if not self._runtime_finished_seen:
                self.cleanup_complete = False
                self.live_status = "Error"
                self.status_var.set("Error")
                self.live_hint_var.set("El runtime terminó sin confirmar la limpieza. Reinicio bloqueado.")
            self._sync_context()
            if self.closing and not self.cleanup_complete:
                self.closing = False
                self.footer_var.set("La ventana permanece abierta porque falta confirmar la limpieza del runtime.")
        if self.closing and self.runtime_thread is None and not self.updates_panel.busy:
            self._destroy()
            return
        self._schedule(60, self._tick)

    def close(self):
        self.playing = False
        self.closing = True
        updates_done = self.updates_panel.close()
        if self.runtime_thread is not None:
            self.stop_live()
            self.footer_var.set("Cerrando: se espera a que el runtime termine y confirme la limpieza…")
            return
        if not updates_done:
            self.footer_var.set("Cerrando: se cancela la consulta o descarga de GitHub; espera a que termine…")
            return
        self._destroy()

    def _destroy(self):
        for token in self._after_ids:
            self.root.after_cancel(token)
        self._after_ids.clear()
        self._cancel_recompute()
        self.root.destroy()


def main():
    root = tk.Tk()
    def show_callback_error(error_type, error, traceback):
        messagebox.showerror("Error de la interfaz", str(error), parent=root)
    root.report_callback_exception = show_callback_error
    try:
        App(root, auto_check_updates=True)
    except Exception as error:
        root.withdraw()
        messagebox.showerror("MiRealmBot no pudo abrirse", str(error), parent=root)
        root.destroy()
        return 1
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
