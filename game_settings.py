"""Read only RotMG's saved key bindings from Unity PlayerPrefs on Windows.

Missing PlayerPrefs entries do not establish a game's default binding. No
credentials or unrelated preferences are retained or saved.
"""
from __future__ import annotations

from dataclasses import dataclass
import re


REGISTRY_PATH = r"Software\DECA Live Operations GmbH\RotMGExalt"
CONTROL_NAMES = {"nexus": "Key.EscapeToNexus", "autofire": "Key.AutoFire"}


@dataclass(frozen=True)
class GameBinding:
    control: str
    key: str | None
    unity_code: int | None
    source: str | None
    reason: str | None = None


def unity_key_name(value: object) -> str | None:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    if 97 <= value <= 122:
        return chr(value).upper()
    return {306: "CTRL", 305: "RCTRL", 304: "SHIFT", 303: "RSHIFT",
            308: "ALT", 307: "RALT", 32: "SPACE", 9: "TAB",
            27: "ESC", 13: "ENTER"}.get(value)


def parse_game_bindings(values: dict[str, object]) -> dict[str, GameBinding]:
    result = {}
    for control, pref in CONTROL_NAMES.items():
        matches = [(name, value) for name, value in values.items()
                   if re.fullmatch(re.escape(pref) + r"(?:_h\d+)?", name)]
        if not matches:
            result[control] = GameBinding(control, None, None, None,
                                          "El juego no guardó un valor para esta tecla.")
            continue
        if len(matches) != 1:
            result[control] = GameBinding(control, None, None, None,
                                          "Hay más de una preferencia para esta tecla.")
            continue
        name, code = matches[0]
        valid_code = code if isinstance(code, int) and not isinstance(code, bool) else None
        key = unity_key_name(code)
        result[control] = GameBinding(control, key, valid_code,
                                      f"HKCU\\{REGISTRY_PATH}\\{name}",
                                      None if key else "La tecla guardada no está reconocida.")
    return result


def read_game_bindings() -> dict[str, GameBinding]:
    try:
        import winreg
        values = {}
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, REGISTRY_PATH,
                            0, winreg.KEY_READ) as key:
            count = winreg.QueryInfoKey(key)[1]
            for index in range(count):
                # Enumerate names to accommodate Unity's hashed suffix. Retain
                # only the two preferences the user asked to read.
                name, value, kind = winreg.EnumValue(key, index)
                if any(re.fullmatch(re.escape(pref) + r"(?:_h\d+)?", name)
                       for pref in CONTROL_NAMES.values()):
                    values[name] = value if kind == winreg.REG_DWORD else None
        return parse_game_bindings(values)
    except (ImportError, OSError) as error:
        return {control: GameBinding(control, None, None, None,
                                    f"No se pudieron leer las teclas guardadas: {error}")
                for control in CONTROL_NAMES}
