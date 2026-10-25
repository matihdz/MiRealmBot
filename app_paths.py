"""Separate packaged resources from the portable application's saved files.

PyInstaller places Python modules and bundled assets in ``_internal`` for a
directory build. Configuration and logs belong beside the executable instead.
The source checkout keeps the same layout it used before packaging.
"""
from pathlib import Path
import sys


RESOURCE_ROOT = Path(__file__).resolve().parent
APP_ROOT = (Path(sys.executable).resolve().parent
            if getattr(sys, "frozen", False) else RESOURCE_ROOT)
DEFAULT_CONFIG_PATH = APP_ROOT / "autonexus.json"
LOG_DIR = APP_ROOT / "analysis"


def resource_path(relative: str | Path) -> Path:
    """Resolve a bundled asset independently of the current working directory."""
    return RESOURCE_ROOT / relative
