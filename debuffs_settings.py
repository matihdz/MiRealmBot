"""Validated independent settings for the Debuffs module."""
from dataclasses import asdict, dataclass


QUIET_SUPPORTED = False
QUIET_UNSUPPORTED_REASON = (
    "AntiQuiet no disponible: no se ha verificado una ruta que evite su efecto "
    "sobre el maná y las habilidades."
)


@dataclass(frozen=True)
class DebuffSettings:
    enabled: bool = False
    confuse: bool = True
    quiet: bool = False

    @property
    def has_protection(self) -> bool:
        return self.enabled and self.confuse


def parse_debuffs(data: object = None) -> DebuffSettings:
    if data is None:
        return DebuffSettings()
    if not isinstance(data, dict):
        raise ValueError("debuffs debe contener un objeto JSON")
    values = {}
    for field, default, label in (("enabled", False, "Debuffs activado"),
                                   ("confuse", True, "AntiConfuse"),
                                   ("quiet", False, "AntiQuiet")):
        value = data.get(field, default)
        if not isinstance(value, bool):
            raise ValueError(f"{label} debe ser true o false")
        values[field] = value
    if values["quiet"]:
        raise ValueError(QUIET_UNSUPPORTED_REASON)
    return DebuffSettings(**values)


def debuffs_mapping(settings: DebuffSettings) -> dict:
    return asdict(settings)
