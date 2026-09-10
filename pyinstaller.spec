# -*- mode: python ; coding: utf-8 -*-
"""Build the standalone Despatch executable for Windows, macOS, and Linux."""

import os
import sys
import tomllib
from pathlib import Path


PROJECT_ROOT = Path(SPECPATH).resolve()
SOURCE_ROOT = PROJECT_ROOT / "py"
RESOURCE_ROOT = PROJECT_ROOT / "resources"
DOCUMENTATION_ROOT = PROJECT_ROOT / "site"
CONSOLE_BUILD = os.environ.get("DESPATCH_CONSOLE_BUILD") == "1"

if not (DOCUMENTATION_ROOT / "index.html").is_file():
    raise SystemExit("Build the ProperDocs site before running PyInstaller")

with (PROJECT_ROOT / "pyproject.toml").open("rb") as pyproject_file:
    PROJECT_VERSION = tomllib.load(pyproject_file)["project"]["version"]

analysis = Analysis(
    [str(PROJECT_ROOT / "scripts" / "pyinstaller_entry.py")],
    pathex=[str(SOURCE_ROOT)],
    binaries=[],
    datas=[
        (str(RESOURCE_ROOT), "despatch/resources"),
        (str(DOCUMENTATION_ROOT), "despatch/docs"),
    ],
    hiddenimports=[
        "envoy",
        "envoy._envoy",
        "PySide6.QtCore",
        "PySide6.QtGui",
        "PySide6.QtNetwork",
        "PySide6.QtWidgets",
        "Qt",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        'matplotlib',
        'numpy',
        'pandas',
        'scipy',
        'tkinter',
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    noarchive=False,
)

python_archive = PYZ(analysis.pure)

# PyInstaller only honors EXE()'s `icon` argument on Windows -- the macOS
# app icon instead comes from the BUNDLE() step below, and Linux ignores
# icon metadata on the executable entirely, so `None` there is correct
# (not just harmless).
executable_icon = str(RESOURCE_ROOT / "icons" / "despatch.ico") if sys.platform == "win32" else None

executable = EXE(
    python_archive,
    analysis.scripts,
    analysis.binaries,
    analysis.datas,
    [],
    name="despatch",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=CONSOLE_BUILD,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=executable_icon,
)

# macOS only: wrap the frozen executable in a proper .app bundle so it gets
# a Dock/Finder identity and an Info.plist. LSUIElement=True keeps it out of
# the Dock and app-switcher entirely -- Despatch is a tray-only application
# on every platform, and on Windows/Linux it never claims a taskbar slot of
# its own either, so this preserves that same behavior on macOS.
if sys.platform == "darwin":
    app_bundle = BUNDLE(
        executable,
        name="Despatch.app",
        icon=str(RESOURCE_ROOT / "icons" / "despatch.icns"),
        bundle_identifier="io.github.gtvfx-envoy.despatch",
        info_plist={
            "CFBundleShortVersionString": PROJECT_VERSION,
            "CFBundleVersion": PROJECT_VERSION,
            "LSUIElement": True,
            "NSHighResolutionCapable": True,
        },
    )
