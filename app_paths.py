"""Separate packaged resources from the portable application's saved files.

PyInstaller places Python modules and bundled assets in ``_internal`` for a
directory build. Configuration and logs belong beside the executable instead.
The source checkout keeps the same layout it used before packaging.
"""
from pathlib import Path
import sys
from uuid import uuid4


RESOURCE_ROOT = Path(__file__).resolve().parent
APP_ROOT = (Path(sys.executable).resolve().parent
            if getattr(sys, "frozen", False) else RESOURCE_ROOT)
DEFAULT_CONFIG_PATH = APP_ROOT / "mirealmbot.json"
LEGACY_CONFIG_PATH = APP_ROOT / "autonexus.json"
CONFIG_TEMPLATE_PATH = APP_ROOT / "mirealmbot.example.json"
LOG_DIR = APP_ROOT / "analysis"


def resource_path(relative: str | Path) -> Path:
    """Resolve a bundled asset independently of the current working directory."""
    return RESOURCE_ROOT / relative


def ensure_default_config(*, root: Path | None = None) -> Path:
    """Create the app config once, preserving an older installation's exact bytes.

    Only callers choosing the default configuration use this migration. Explicit
    file selections continue to address exactly the selected path.
    """
    canonical = DEFAULT_CONFIG_PATH if root is None else Path(root) / DEFAULT_CONFIG_PATH.name
    legacy = LEGACY_CONFIG_PATH if root is None else Path(root) / LEGACY_CONFIG_PATH.name
    template = CONFIG_TEMPLATE_PATH if root is None else Path(root) / CONFIG_TEMPLATE_PATH.name
    if canonical.exists():
        return canonical
    if legacy.exists():
        contents = legacy.read_bytes()
        backup = legacy.with_name(f"{legacy.name}.migration.{uuid4().hex}.bak")
        with backup.open("xb") as handle:
            handle.write(contents)
    else:
        contents = template.read_bytes()
    try:
        handle = canonical.open("xb")
    except FileExistsError:
        return canonical
    try:
        with handle:
            handle.write(contents)
    except BaseException:
        canonical.unlink(missing_ok=True)
        raise
    return canonical
