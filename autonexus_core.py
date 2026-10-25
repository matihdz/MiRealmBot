"""AutoNexus decisions based on the recovered native health rule.

This module only requests an action. It neither controls the game nor confirms
that the character reached the Nexus. The adapter supplies effective damage,
map exclusion and the two recovered player gates; their game names are unknown.
"""

from dataclasses import dataclass
from enum import Enum
from math import isfinite
from struct import pack, unpack


INT32_MIN = -(2**31)
INT32_MAX = 2**31 - 1


class Action(Enum):
    NONE = "none"
    ESCAPE = "escape"


@dataclass(frozen=True)
class Decision:
    action: Action
    reason: str
    limit: int | None = None
    player_hp: int | None = None
    tracked_hp: int | None = None
    max_hp: int | None = None
    cause: str | None = None


def _int32(value: object, name: str, *, optional: bool = False) -> int | None:
    if value is None and optional:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{name} must be an int32" + (" or None" if optional else ""))
    if not INT32_MIN <= value <= INT32_MAX:
        raise ValueError(f"{name} must fit in int32")
    return value


def _optional_bool(value: object, name: str) -> bool | None:
    if value is not None and not isinstance(value, bool):
        raise ValueError(f"{name} must be a bool or None")
    return value


def _health_values(player_hp: int | None, max_hp: int | None,
                   tracked_hp: int | None) -> tuple[int | None, int | None, int | None]:
    player_hp = _int32(player_hp, "player_hp", optional=True)
    max_hp = _int32(max_hp, "max_hp", optional=True)
    tracked_hp = _int32(tracked_hp, "tracked_hp", optional=True)
    if max_hp is not None and max_hp <= 0:
        raise ValueError("max_hp must be positive or None")
    # A normal authoritative update synchronizes the model to the player HP.
    return player_hp, max_hp, player_hp if tracked_hp is None else tracked_hp


def _threshold_float32(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("threshold must be a finite number strictly between 0 and 1")
    try:
        valid = isfinite(value) and 0 < value < 1
    except OverflowError:
        valid = False
    if not valid:
        raise ValueError("threshold must be a finite number strictly between 0 and 1")
    result = unpack("<f", pack("<f", value))[0]
    if not 0 < result < 1:
        raise ValueError("threshold must remain strictly between 0 and 1 as float32")
    return result


def validate_observation(player_hp: int | None, max_hp: int | None,
                         tracked_hp: int | None, gate_a: bool | None,
                         gate_b: bool | None) -> None:
    """Validate a complete adapter observation without changing engine state."""
    _health_values(player_hp, max_hp, tracked_hp)
    _optional_bool(gate_a, "gate_a")
    _optional_bool(gate_b, "gate_b")


def _truncate_int32(value: float) -> int:
    """Native conversion: NaN -> 0, saturation, otherwise truncate toward zero."""
    if value != value:
        return 0
    if value >= INT32_MAX:
        return INT32_MAX
    if value <= INT32_MIN:
        return INT32_MIN
    return int(value)


class AutoNexus:
    """Request ESCAPE once per map when either HP source falls below the limit.

    The limit is trunc_int32(double(float32(threshold)) * double(max_hp)).
    HP recovery does not rearm the request. Only new_map clears the latch.

    Unknown map/health inputs block decisions. State filters are enabled by
    default; disabling them is our explicit HP-only policy, not a recovered
    interpretation that unknown player states are false.

    A new map invalidates old HP and gates as our adapter contract, so call
    set_gates and update_health again.
    This freshness policy is ours, not a recovered native timing rule.
    """

    def __init__(self, threshold: float = 0.30, enabled: bool = True,
                 use_state_filters: bool = True):
        self._threshold = _threshold_float32(threshold)
        self.enabled = enabled
        self.use_state_filters = use_state_filters
        self.new_map(excluded=None)

    @property
    def threshold(self) -> float:
        """The configured fraction after native float32 storage."""
        return self._threshold

    @threshold.setter
    def threshold(self, value: float) -> None:
        """Apply a new threshold without changing this map's request latch."""
        self._threshold = _threshold_float32(value)

    @property
    def enabled(self) -> bool:
        return self._enabled

    @enabled.setter
    def enabled(self, value: bool) -> None:
        if not isinstance(value, bool):
            raise ValueError("enabled must be a bool")
        self._enabled = value

    @property
    def use_state_filters(self) -> bool:
        return self._use_state_filters

    @use_state_filters.setter
    def use_state_filters(self, value: bool) -> None:
        if not isinstance(value, bool):
            raise ValueError("use_state_filters must be a bool")
        self._use_state_filters = value

    def new_map(self, excluded: bool | None = False) -> None:
        """Rearm and invalidate the preceding map's HP and player gates."""
        self._excluded = _optional_bool(excluded, "excluded")
        self._already_requested = False
        self._player_hp = None
        self._tracked_hp = None
        self._max_hp = None
        self._gate_a = None
        self._gate_b = None

    def set_gates(self, gate_a: bool | None, gate_b: bool | None) -> None:
        """Set the two independently decoded player gates, or unknown (None)."""
        gate_a = _optional_bool(gate_a, "gate_a")
        gate_b = _optional_bool(gate_b, "gate_b")
        self._gate_a, self._gate_b = gate_a, gate_b

    def set_map_excluded(self, excluded: bool | None) -> None:
        """Update map knowledge while preserving the one-request latch."""
        self._excluded = _optional_bool(excluded, "excluded")

    def update_health(self, player_hp: int | None, max_hp: int | None,
                      tracked_hp: int | None = None) -> None:
        """Replace health state; omitted tracked_hp synchronizes it to player_hp.

        Negative HP and HP above the maximum are retained without clamping.
        The adapter must supply fresh, correctly identified player observations.
        """
        self._player_hp, self._max_hp, self._tracked_hp = _health_values(
            player_hp, max_hp, tracked_hp)

    def apply_damage(self, amount: int, cause: str = "damage") -> Decision:
        """Subtract effective damage, then check; no defense model is implied."""
        amount = _int32(amount, "amount")
        if amount < 0:
            raise ValueError("amount must be nonnegative effective damage")
        if not isinstance(cause, str):
            raise ValueError("cause must be a string")
        if self._tracked_hp is not None:
            # The recovered operation is an unchecked native int32 subtraction.
            self._tracked_hp = ((self._tracked_hp - amount - INT32_MIN)
                                % (2**32)) + INT32_MIN
        return self._evaluate(cause)

    def on_tick_health(self, player_hp: int | None, max_hp: int | None,
                       tracked_hp: int | None = None, cause: str = "NT") -> Decision:
        """Check before and after authoritative synchronization, like NT.

        All inputs are validated before either check. Return the first action
        when both checks run; the authoritative state is still synchronized.
        """
        values = _health_values(player_hp, max_hp, tracked_hp)
        if not isinstance(cause, str):
            raise ValueError("cause must be a string")
        before = self._evaluate(cause)
        self._player_hp, self._max_hp, self._tracked_hp = values
        after = self._evaluate(cause)
        return before if before.action is Action.ESCAPE else after

    def evaluate(self) -> Decision:
        return self._evaluate(None)

    def _evaluate(self, cause: str | None) -> Decision:
        limit = (None if self._max_hp is None
                 else _truncate_int32(float(self._threshold) * float(self._max_hp)))

        def decision(action: Action, reason: str) -> Decision:
            return Decision(action, reason, limit, self._player_hp,
                            self._tracked_hp, self._max_hp, cause)

        if not self.enabled:
            return decision(Action.NONE, "AutoNexus is disabled")
        if self._excluded is None:
            return decision(Action.NONE, "map state is unknown")
        if self._excluded:
            return decision(Action.NONE, "map is excluded")
        if self.use_state_filters:
            if self._gate_a is None or self._gate_b is None:
                return decision(Action.NONE, "player gates are unknown")
            if self._gate_a or self._gate_b:
                return decision(Action.NONE, "a player gate blocks AutoNexus")
        if limit is None or self._player_hp is None or self._tracked_hp is None:
            return decision(Action.NONE, "health data is unknown")
        if self._already_requested:
            return decision(Action.NONE, "exit was already requested for this map")
        if self._tracked_hp < limit or self._player_hp < limit:
            self._already_requested = True
            return decision(Action.ESCAPE, "health is below the limit; exit requested")
        return decision(Action.NONE, "both health sources are at or above the limit")
