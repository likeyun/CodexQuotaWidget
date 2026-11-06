# -*- mode: python ; coding: utf-8 -*-
"""Portable Windows build; do not collect another program's ICU from PATH."""

from pathlib import Path


project_root = Path(SPECPATH).resolve().parent

a = Analysis(
    [str(project_root / "main.py")],
    pathex=[str(project_root)],
    binaries=[],
    datas=[(str(project_root / "assets"), "assets")],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)

# Qt 6.11 on Windows imports the OS's unversioned ICU API (ucnv_open, etc.).
# If Poppler/Conda is on PATH, dependency analysis can accidentally collect its
# icuuc.dll instead, whose exports are versioned (ucnv_open_78). The executable
# then fails while importing QtCore. Leave Windows ICU to System32 and omit the
# accidentally collected ICU 78 data dependency as well.
foreign_icu = {"icuuc.dll", "icudt78.dll"}
a.binaries = [
    entry for entry in a.binaries
    if Path(entry[0]).name.lower() not in foreign_icu
]

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="CodexQuota",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=[str(project_root / "assets" / "app.ico")],
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="CodexQuota",
)
