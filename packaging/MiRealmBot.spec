# -*- mode: python ; coding: utf-8 -*-
"""Onedir, windowed build. build_portable.py supplies an allowlisted source tree."""
import os
import json
from pathlib import Path

from PyInstaller.utils.hooks import copy_metadata

source = Path(os.environ["MIREALMBOT_BUILD_SOURCE_ROOT"])
resource_files = json.loads((source / "bundle-resources.json").read_text(encoding="utf-8"))
datas = [(str(source / relative), str(Path(relative).parent))
         for relative in resource_files]
datas += copy_metadata("frida")

a = Analysis(
    [str(source / "launcher.py")],
    pathex=[str(source)],
    binaries=[],
    datas=datas,
    hiddenimports=["frida"],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["pytest", "unittest", "pip", "setuptools"],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="MiRealmBot",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    contents_directory="_internal",
)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name="MiRealmBot")
