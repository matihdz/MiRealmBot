"""Adapt decoded internal health frames to the independent AutoNexus core.

This module does not read memory, communicate with a process, or send input.
The producer must identify the local player and emit a different map_id on
every actual map load (include its epoch when available), including repeated
loads with the same name. Changing a name on the same ID only updates the
exclusion decision; it does not represent a map transition or rearm a request.

Excluding Nexus by default is our configuration, not the recovered original
map list. Missing map/health invalidates observations without clearing the
already-requested latch. Unknown player gates retain valid health; the core's
explicit use_state_filters policy determines whether these gates block it.
tracked_hp=None uses the core's normal authoritative synchronization to
player_hp; this adapter does not predict missing damage.
"""

from collections.abc import Iterable
from dataclasses import dataclass

from autonexus_core import AutoNexus, Decision, validate_observation


@dataclass(frozen=True)
class HealthFrame:
    map_id: str | None
    map_name: str | None
    player_hp: int | None
    max_hp: int | None
    gate_a: bool | None
    gate_b: bool | None
    tracked_hp: int | None = None


def _map_text(value: object, field_name: str) -> None:
    if value is not None and (not isinstance(value, str) or not value):
        raise ValueError(f"{field_name} must be a nonempty string or None")


def validate_frame(frame: HealthFrame) -> None:
    """Validate a decoded frame without changing controller or core state."""
    if not isinstance(frame, HealthFrame):
        raise ValueError("frame must be a HealthFrame")
    _map_text(frame.map_id, "map_id")
    _map_text(frame.map_name, "map_name")
    validate_observation(frame.player_hp, frame.max_hp, frame.tracked_hp,
                         frame.gate_a, frame.gate_b)


class Controller:
    def __init__(self, engine: AutoNexus,
                 excluded_maps: Iterable[str] = ("Nexus",)):
        if isinstance(excluded_maps, str):
            raise ValueError("excluded_maps must be an iterable of map names")
        names = frozenset(excluded_maps)
        for name in names:
            if not isinstance(name, str) or not name:
                raise ValueError("excluded map names must be nonempty strings")
        self.engine = engine
        self._excluded_maps = names
        self._map_id = None

    def consume(self, frame: HealthFrame) -> Decision:
        """Validate a complete frame before changing any live engine state."""
        validate_frame(frame)

        if frame.map_id is None:
            # An unknown identity cannot establish a transition or clear a latch.
            self.engine.set_map_excluded(None)
            self._invalidate_observations()
            return self.engine.evaluate()

        excluded = (None if frame.map_name is None
                    else frame.map_name in self._excluded_maps)
        if frame.map_id != self._map_id:
            self.engine.new_map(excluded=excluded)
            self._map_id = frame.map_id
        else:
            self.engine.set_map_excluded(excluded)

        if frame.map_name is None or frame.player_hp is None or frame.max_hp is None:
            self._invalidate_observations()
            return self.engine.evaluate()

        self.engine.set_gates(frame.gate_a, frame.gate_b)
        return self.engine.on_tick_health(frame.player_hp, frame.max_hp,
                                          frame.tracked_hp)

    def _invalidate_observations(self) -> None:
        self.engine.update_health(None, None)
        self.engine.set_gates(None, None)
