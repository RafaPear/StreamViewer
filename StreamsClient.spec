# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for StreamsClient macOS .app bundle."""

import sys
from pathlib import Path

block_cipher = None
app_root = Path(SPECPATH)

a = Analysis(
    [str(app_root / "src" / "streams_client.py")],
    pathex=[str(app_root / "src")],
    binaries=[],
    datas=[],
    hiddenimports=[
        "qasync",
        "vlc",
        "PyQt6.QtCore",
        "PyQt6.QtGui",
        "PyQt6.QtWidgets",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["tkinter", "matplotlib", "numpy", "scipy", "PIL"],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="StreamsClient",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    name="StreamsClient",
)

if sys.platform == "darwin":
    app = BUNDLE(
        coll,
        name="StreamsClient.app",
        icon=None,  # Add icon path here if you have one
        bundle_identifier="com.streamsclient.app",
        info_plist={
            "CFBundleDisplayName": "StreamsClient",
            "CFBundleShortVersionString": "1.0.0",
            "CFBundleName": "StreamsClient",
            "NSHighResolutionCapable": True,
            "NSRequiresAquaSystemAppearance": False,  # support dark mode
            "LSMinimumSystemVersion": "11.0",
        },
    )
