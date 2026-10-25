"""Validated settings for the single supported aim mode."""
from dataclasses import asdict, dataclass
import math


@dataclass(frozen=True)
class AutoAimSettings:
    enabled: bool = False
    mode: str = "close_to_mouse"
    extrapolation: float = 302.0
    mouse_radius: float = 4.0
    activation: str = "autofire"


def parse_autoaim(data: object = None) -> AutoAimSettings:
    if data is None:
        return AutoAimSettings()
    if not isinstance(data, dict):
        raise ValueError("autoaim debe contener un objeto JSON")
    enabled = data.get("enabled", False)
    if not isinstance(enabled, bool):
        raise ValueError("AutoAim activado debe ser true o false")
    if data.get("mode", "close_to_mouse") != "close_to_mouse":
        raise ValueError("El único modo de AutoAim disponible es Close to Mouse")
    if data.get("activation", "autofire") != "autofire":
        raise ValueError("AutoAim se activa mediante el autofire del juego")
    numbers = {}
    for field, default, positive in (("extrapolation", 302.0, False),
                                      ("mouse_radius", 4.0, True)):
        value = data.get(field, default)
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError(f"{field} debe ser un número finito")
        try:
            value = float(value)
        except OverflowError:
            raise ValueError(f"{field} debe ser un número finito") from None
        if not math.isfinite(value) or value < 0 or (positive and value == 0):
            raise ValueError(f"{field} debe ser {'positivo' if positive else 'no negativo'} y finito")
        numbers[field] = value
    return AutoAimSettings(enabled=enabled, **numbers)


def autoaim_mapping(settings: AutoAimSettings) -> dict:
    return asdict(settings)
