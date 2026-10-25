"""Own ordered accepted-hit events and their pending HP prediction.

This module never detects collisions, calculates defense, reads game memory,
or sends input. Its producer supplies final effective damage for an accepted
local projectile hit, and explicit authoritative HP synchronization events.
Ordinary unchanged HP samples retain pending damage; a synchronization (even
with unchanged HP), observed HP change, or lost freshness discards it.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from math import isfinite

from autonexus_controller import Controller, HealthFrame, validate_frame
from autonexus_core import Action, Decision, validate_observation

UNKNOWN_FRAME = HealthFrame(None, None, None, None, None, None)
FRAME_FIELDS = tuple(HealthFrame.__dataclass_fields__)


def finite_age(value: object, field: str = "age_ms") -> int | float:
    """Parse the shared poll/event source-age boundary, without coercion."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{field} debe ser un número finito")
    try:
        valid = isfinite(value)
    except OverflowError:
        valid = False
    if not valid or value < 0:
        raise ValueError(f"{field} debe ser finito y no negativo")
    return value


def decoded_frame(value: object) -> HealthFrame:
    if not isinstance(value, dict):
        raise ValueError("El frame de un evento debe ser un objeto")
    frame = HealthFrame(*(value.get(name) for name in FRAME_FIELDS))
    validate_frame(frame)
    return frame


@dataclass(frozen=True)
class PredictionEvent:
    event_id: int
    kind: str
    age_ms: int | float
    frame: HealthFrame
    amount: int | None = None
    cause: str | None = None


def parse_events(raw: object, events_lost: object,
                 last_event_id: int) -> tuple[PredictionEvent, ...]:
    """Validate the entire batch before any event can affect live state."""
    if not isinstance(events_lost, bool):
        raise ValueError("events_lost debe ser true o false")
    if events_lost:
        raise ValueError("El agente perdió eventos de daño; se detuvo la predicción")
    if not isinstance(raw, list):
        raise ValueError("events debe ser una lista")
    events = []
    expected = last_event_id + 1
    for item in raw:
        if not isinstance(item, dict):
            raise ValueError("Cada evento debe ser un objeto")
        event_id = item.get("event_id")
        if isinstance(event_id, bool) or not isinstance(event_id, int) or event_id <= 0:
            raise ValueError("event_id debe ser un entero positivo")
        if event_id != expected:
            raise ValueError(f"Secuencia de eventos incompleta o repetida: esperaba {expected}, recibió {event_id}")
        expected += 1
        kind = item.get("kind")
        if kind not in ("damage", "health_sync"):
            raise ValueError("kind debe ser damage o health_sync")
        age = finite_age(item.get("age_ms"), "event.age_ms")
        frame = decoded_frame(item.get("frame"))
        if frame.tracked_hp is not None:
            raise ValueError("El productor de eventos debe dejar tracked_hp en null")
        amount = cause = None
        if kind == "damage":
            amount = item.get("amount")
            # Reuse the core's int32 boundary rather than interpreting health
            # integers again. A damage amount must additionally be nonnegative.
            validate_observation(amount, None, None, None, None)
            if amount is None or amount < 0:
                raise ValueError("amount debe ser daño efectivo int32 no negativo")
            cause = item.get("cause")
            if cause != "projectile":
                raise ValueError("El único origen de daño soportado es projectile")
        elif "amount" in item:
            raise ValueError("health_sync no debe contener amount")
        events.append(PredictionEvent(event_id, kind, age, frame, amount, cause))
    return tuple(events)


class AcceptedHitPrediction:
    """One owner for event ordering, accumulated prediction and reconciliation."""
    def __init__(self, controller: Controller, *, enabled: bool = False,
                 max_age_ms: int = 250):
        if not isinstance(enabled, bool):
            raise ValueError("prediction_enabled debe ser true o false")
        self.controller = controller
        self.enabled = enabled
        self.max_age_ms = max_age_ms
        self.last_event_id = 0
        self.last_events: tuple[PredictionEvent, ...] = ()
        self._map_id: str | None = None
        self._observed_hp: int | None = None
        self._tracked_hp: int | None = None
        self._reason = "disabled" if not enabled else "no_observation"

    def state(self) -> dict:
        return {"enabled": self.enabled, "scope": "accepted_local_projectile_hits",
                "last_event_id": self.last_event_id, "map_id": self._map_id,
                "observed_hp": self._observed_hp, "tracked_hp": self._tracked_hp,
                "reconciliation": self._reason}

    def event_records(self) -> list[dict]:
        return [asdict(event) for event in self.last_events]

    def invalidate(self, reason: str) -> Decision:
        self._map_id = self._observed_hp = self._tracked_hp = None
        self._reason = reason
        return self.controller.consume(UNKNOWN_FRAME)

    @staticmethod
    def _complete(frame: HealthFrame) -> bool:
        return (frame.map_id is not None and frame.map_name is not None
                and frame.player_hp is not None and frame.max_hp is not None)

    def _observe(self, frame: HealthFrame, *, synchronize: bool = False) -> Decision:
        if not self._complete(frame):
            self._map_id = self._observed_hp = self._tracked_hp = None
            self._reason = "incomplete_observation"
            return self.controller.consume(frame)
        new_map = frame.map_id != self._map_id
        changed_hp = frame.player_hp != self._observed_hp
        if synchronize or new_map or changed_hp or self._tracked_hp is None:
            # Clear obsolete prediction before the controller's before/after
            # tick checks. This changes health knowledge, never the map latch.
            self.controller.engine.update_health(None, None)
            self._tracked_hp = frame.player_hp
            self._reason = ("health_sync" if synchronize else "map_changed" if new_map
                            else "observed_hp_changed" if changed_hp else "baseline")
        self._map_id = frame.map_id
        self._observed_hp = frame.player_hp
        return self.controller.consume(replace(frame, tracked_hp=self._tracked_hp))

    def consume(self, *, status: str, frame: HealthFrame | None,
                raw_events: object, events_lost: object = False) -> Decision | None:
        """Consume one already parsed poll; return its first current-map action."""
        try:
            if frame is not None:
                validate_frame(frame)
                if self.enabled and frame.tracked_hp is not None:
                    raise ValueError("La predicción es dueña de tracked_hp; el agente debe enviar null")
            events = parse_events(raw_events, events_lost, self.last_event_id)
            if status == "duplicate" and events:
                raise ValueError("Los eventos requieren una secuencia de frame nueva")
        except ValueError:
            self.last_events = ()
            self.invalidate("invalid_transport")
            raise

        self.last_events = events
        if events:
            self.last_event_id = events[-1].event_id
        if status == "duplicate":
            return None
        if status != "fresh" or frame is None:
            return self.invalidate(status)
        if not self.enabled:
            return self.controller.consume(frame)
        if not self._complete(frame):
            return self._observe(frame)

        first_escape = None
        for event in events:
            # An old map must not update the Controller's identity or rearm it.
            if event.frame.map_id != frame.map_id:
                continue
            if event.age_ms > self.max_age_ms:
                self.invalidate("stale_event")
                continue
            if not self._complete(event.frame):
                self.invalidate("incomplete_event")
                continue
            before = self._observe(event.frame, synchronize=event.kind == "health_sync")
            if first_escape is None and before.action is Action.ESCAPE:
                first_escape = before
            if event.kind == "damage":
                decision = self.controller.engine.apply_damage(event.amount, event.cause)
                self._tracked_hp = decision.tracked_hp
                self._reason = "pending_accepted_damage"
                if first_escape is None and decision.action is Action.ESCAPE:
                    first_escape = decision
        latest = self._observe(frame)
        return first_escape if first_escape is not None else latest
