"""Replay retained observations with the production decision owners, offline.

The live JSONL is a partial recording: it contains every prediction batch but
only periodic/important decision frames. This module does not invent omitted
polls, reuse logged actions, access the game, or imply that an earlier request
would have saved a character. Times are original host receipt times, not input
latency measurements. Event ages are evaluated as recorded, without a live clock.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
import hashlib
import json
from pathlib import Path

from app_paths import resource_path
from autonexus_controller import Controller, HealthFrame
from autonexus_core import Action, AutoNexus
from damage_prediction import (AcceptedHitPrediction, PredictionEvent,
                               decoded_frame, finite_age, parse_events)


DEFAULT_RECORDING = resource_path("examples/demo-combat.jsonl")
# The local interface retains the parsed file and several parameter runs. Bound
# that synchronous in-memory workload; this is not a game or JSONL format limit.
MAX_RECORDING_BYTES = 16 * 1024 * 1024
SOURCE_MAX_AGE_MS = 250
KNOWN_EVENTS = frozenset(("start", "decision", "telemetry", "prediction_transport",
                          "prediction_state", "lifecycle", "stop_requested",
                          "cleanup", "error", "interrupted"))
BASE_LIMITATIONS = (
    "Registro parcial: conserva lotes de eventos y frames periódicos; faltan polls intermedios.",
    "Los tiempos son recepciones del registro original; no miden latencia de entrada ni de salida al Nexus.",
    "La simulación recalcula solicitudes, no envía teclas ni demuestra que un personaje hubiera sobrevivido.",
    "Se conserva la edad original de eventos/frames (máximo 250 ms), sin compararla con la fecha actual.",
)


@dataclass(frozen=True)
class ReplayStep:
    time_seconds: float
    captured_at_utc: str
    source_line: int
    sequence: int | None
    status: str
    age_ms: int | float | None
    frame: HealthFrame | None
    events: tuple[PredictionEvent, ...] = ()
    frame_basis: str = "recorded_poll_frame"


@dataclass(frozen=True)
class Recording:
    source_path: str
    source_sha256: str
    steps: tuple[ReplayStep, ...]
    limitations: tuple[str, ...]
    started_at_utc: str
    duration_seconds: float
    damage_count: int


@dataclass(frozen=True)
class ReplayResult:
    rows: tuple[dict, ...]
    summary: dict
    limitations: tuple[str, ...]
    threshold_percent: float
    prediction_enabled: bool


def _timestamp(value: object) -> datetime:
    if not isinstance(value, str):
        raise ValueError("captured_at_utc debe ser texto ISO 8601 con zona horaria")
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise ValueError("captured_at_utc no es una fecha ISO 8601 válida") from error
    if result.utcoffset() is None:
        raise ValueError("captured_at_utc debe incluir zona horaria")
    return result


def _sequence(value: object, *, optional: bool = False) -> int | None:
    if optional and value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError("sequence debe ser un entero no negativo")
    return value


def _decision_frame(item: dict) -> tuple[str, int | None, int | float | None, HealthFrame | None]:
    status = item.get("status")
    if status not in ("fresh", "missing", "stale", "duplicate"):
        raise ValueError("decision.status debe ser fresh, missing, stale o duplicate")
    sequence = _sequence(item.get("sequence"), optional=status == "missing")
    age = None if status == "missing" else finite_age(item.get("age_ms"))
    if status == "fresh" and age > SOURCE_MAX_AGE_MS:
        raise ValueError("Un frame fresh tiene edad superior a 250 ms")
    if status == "stale" and age <= SOURCE_MAX_AGE_MS:
        raise ValueError("Un frame stale requiere una edad superior a 250 ms")
    frame = None if item.get("frame") is None else decoded_frame(item["frame"])
    if status == "fresh" and frame is None:
        raise ValueError("Un frame fresh requiere decision.frame")
    return status, sequence, age, frame


def _event_transport(event: PredictionEvent) -> dict:
    """Preserve the tagged transport's required omissions after parsing."""
    item = asdict(event)
    if event.kind == "health_sync":
        del item["amount"]
        del item["cause"]
    return item


def load_recording(path: str | Path = DEFAULT_RECORDING) -> Recording:
    """Validate a bounded local JSONL and preserve its ordered source facts."""
    path = Path(path).resolve(strict=True)
    with path.open("rb") as handle:
        content = handle.read(MAX_RECORDING_BYTES + 1)
    if len(content) > MAX_RECORDING_BYTES:
        raise ValueError("El registro excede 16 MiB; divide el registro para la reproducción local")
    try:
        source_text = content.decode("utf-8-sig")
    except UnicodeDecodeError as error:
        raise ValueError("El registro debe estar codificado en UTF-8") from error

    records: list[tuple[int, dict, datetime]] = []
    previous_time = None
    start = None
    for line_number, line in enumerate(source_text.splitlines(), 1):
        if not line.strip():
            continue
        try:
            item = json.loads(line)
            if not isinstance(item, dict) or item.get("event") not in KNOWN_EVENTS:
                raise ValueError("Tipo de registro desconocido o no soportado")
            captured = _timestamp(item.get("captured_at_utc"))
            if previous_time is not None and captured < previous_time:
                raise ValueError("Las marcas de tiempo retroceden; no se puede ordenar la captura con certeza")
            previous_time = captured
            if item["event"] == "start":
                if start is not None or records:
                    raise ValueError("Se requiere un único start al principio del registro")
                start = captured
            records.append((line_number, item, captured))
        except (ValueError, TypeError, OverflowError) as error:
            raise ValueError(f"Registro, línea {line_number}: {error}") from error
    if start is None:
        raise ValueError("El registro no contiene el evento start inicial")

    # A transport record omits its complete poll frame. Use the exact frame
    # from a decision with the same sequence when retained, consuming it once.
    decisions: dict[int, tuple[int, dict]] = {}
    redundant_lines = set()
    for line_number, item, _ in records:
        if item["event"] in ("decision", "telemetry"):
            try:
                status, sequence, _, _ = _decision_frame(item)
                if status == "fresh":
                    if sequence in decisions:
                        previous_line, previous_item = decisions[sequence]
                        if (previous_item["event"] == item["event"] or
                                _decision_frame(previous_item) != _decision_frame(item)):
                            raise ValueError("Dos frames fresh incompatibles tienen la misma sequence")
                        redundant_lines.add(line_number)
                        continue
                    decisions[sequence] = (line_number, item)
            except (ValueError, TypeError) as error:
                raise ValueError(f"Registro, línea {line_number}: {error}") from error

    steps: list[ReplayStep] = []
    paired_lines = set()
    transports = []
    last_event_id = 0
    approximate = 0
    limitations = list(BASE_LIMITATIONS)
    for line_number, item, captured in records:
        if item["event"] != "prediction_transport":
            continue
        try:
            sequence = _sequence(item.get("sequence"))
            age = finite_age(item.get("age_ms"))
            events = parse_events(item.get("events"), item.get("events_lost", False), last_event_id)
            if not events:
                raise ValueError("prediction_transport no contiene eventos")
            last_event_id = events[-1].event_id
            paired = decisions.get(sequence)
            if paired is not None:
                paired_line, paired_item = paired
                status, _, _, frame = _decision_frame(paired_item)
                paired_lines.add(paired_line)
                basis = "recorded_poll_frame"
            else:
                # No claim of exact poll reconstruction: the last event's
                # frame is the only retained local-player/map observation in
                # this batch. Its uncertainty remains visible to consumers.
                status = "fresh" if age <= SOURCE_MAX_AGE_MS else "stale"
                frame = events[-1].frame
                basis = "last_event_frame_approximation"
                approximate += 1
            transports.append(ReplayStep((captured - start).total_seconds(),
                item["captured_at_utc"], line_number, sequence, status, age,
                frame, events, basis))
        except (ValueError, TypeError) as error:
            raise ValueError(f"Registro, línea {line_number}: {error}") from error
    steps.extend(transports)
    for line_number, item, captured in records:
        if (item["event"] not in ("decision", "telemetry") or
                line_number in paired_lines or line_number in redundant_lines):
            continue
        status, sequence, age, frame = _decision_frame(item)
        steps.append(ReplayStep((captured - start).total_seconds(),
            item["captured_at_utc"], line_number, sequence, status, age, frame))
    steps.sort(key=lambda step: step.source_line)
    if not steps:
        raise ValueError("El registro no contiene frames ni eventos reproducibles")
    last_sequence = -1
    for step in steps:
        if step.sequence is not None:
            if step.sequence < last_sequence:
                raise ValueError(f"Registro, línea {step.source_line}: la secuencia del agente retrocedió")
            last_sequence = step.sequence
    if approximate:
        limitations.append(f"{approximate} lote(s) omiten el frame exacto del poll: se usa el último frame del lote como aproximación; un cambio de mapa omitido podría cambiar el resultado.")
    if any(item["event"] == "error" for _, item, _ in records):
        limitations.append("La captura contiene un error del runtime; sólo se reproducen las observaciones conservadas.")
    if not any(item["event"] == "cleanup" and item.get("complete") is True for _, item, _ in records):
        limitations.append("El registro no confirma una limpieza completa del runtime.")
    return Recording(str(path), hashlib.sha256(content).hexdigest(), tuple(steps),
        tuple(limitations), records[0][1]["captured_at_utc"],
        (records[-1][2] - start).total_seconds(),
        sum(event.kind == "damage" for step in steps for event in step.events))


def replay_recording(recording: Recording, threshold_percent: float = 30,
                     prediction_enabled: bool = True,
                     use_state_filters: bool = False,
                     excluded_maps: tuple[str, ...] = ("Nexus",)) -> ReplayResult:
    """Recalculate once per retained batch/frame; never reuse logged decisions."""
    finite_age(threshold_percent, "threshold_percent")
    engine = AutoNexus(threshold=threshold_percent / 100,
                       use_state_filters=use_state_filters)
    controller = Controller(engine, excluded_maps)
    prediction = AcceptedHitPrediction(controller, enabled=prediction_enabled,
                                       max_age_ms=SOURCE_MAX_AGE_MS)
    rows = []
    hit_number = 0
    for step in recording.steps:
        raw_events = [_event_transport(event) for event in step.events]
        decision = prediction.consume(status=step.status, frame=step.frame,
                                       raw_events=raw_events, events_lost=False)
        damage_events = [event for event in step.events if event.kind == "damage"]
        hit_number += len(damage_events)
        if damage_events:
            label = f"Golpe {hit_number}" if len(damage_events) == 1 else f"{len(damage_events)} golpes (hasta {hit_number})"
        elif step.events:
            label = "Sincronización de vida"
        elif step.status != "fresh":
            label = f"Frame {step.status}"
        else:
            label = "Vida observada"
        state = prediction.state()
        row = {
            "time_seconds": step.time_seconds,
            "captured_at_utc": step.captured_at_utc,
            "event_label": label,
            "map_name": None if step.frame is None else step.frame.map_name,
            "observed_hp": None if decision is None else decision.player_hp,
            "max_hp": None if decision is None else decision.max_hp,
            "predicted_hp": None if decision is None else decision.tracked_hp,
            "damage": sum(event.amount for event in damage_events) if damage_events else None,
            "limit": None if decision is None else decision.limit,
            "action": "none" if decision is None else decision.action.value,
            "reason": "duplicate poll; no decision" if decision is None else decision.reason,
            "event_id": step.events[0].event_id if len(step.events) == 1 else None,
            "source_line": step.source_line,
            "status": step.status,
            "frame_basis": step.frame_basis,
            "reconciliation": state["reconciliation"],
        }
        rows.append(row)
    escapes = [row for row in rows if row["action"] == Action.ESCAPE.value]
    first_escape = escapes[0] if escapes else None
    summary = {
        "first_escape": first_escape,
        "damage_count": recording.damage_count,
        "escape_count": len(escapes),
        "result_label": ("Solicitud simulada; supervivencia no comprobada" if escapes
                         else "Sin solicitud en las observaciones conservadas"),
        "source_path": recording.source_path,
        "source_sha256": recording.source_sha256,
        "source_duration_seconds": recording.duration_seconds,
        "retained_step_count": len(rows),
        "threshold_float32": engine.threshold,
        "comparison": "strictly_less_than",
    }
    return ReplayResult(tuple(rows), summary, recording.limitations,
                        float(threshold_percent), prediction_enabled)


def compare_thresholds(recording: Recording,
                       thresholds: tuple[float, ...] = (20, 30, 35, 40, 50, 70, 90),
                       prediction_enabled: bool = True,
                       use_state_filters: bool = False,
                       excluded_maps: tuple[str, ...] = ("Nexus",)) -> list[dict]:
    """Compare first retained triggers; the subsequent timeline is unchanged."""
    rows = []
    for threshold in thresholds:
        result = replay_recording(recording, threshold, prediction_enabled,
                                  use_state_filters, excluded_maps)
        first = result.summary["first_escape"]
        rows.append({
            "threshold_percent": float(threshold),
            "first_escape_seconds": None if first is None else first["time_seconds"],
            **{key: None if first is None else first[key] for key in (
                "event_label", "event_id", "observed_hp", "predicted_hp", "damage", "limit")},
            "result_label": result.summary["result_label"],
        })
    return rows
