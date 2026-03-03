"""PyInstaller runtime hook – set up Qt environment before anything imports PyQt6.

This runs before the main script. It ensures Qt can find its plugins and
frameworks inside the PyInstaller bundle, preventing the CFBundleCopyBundleURL
crash on macOS.
"""
import os
import sys

if getattr(sys, 'frozen', False):
    bundle_dir = sys._MEIPASS  # noqa: SLF001

    # Qt plugins (platforms, styles, etc.)
    qt_plugins = os.path.join(bundle_dir, 'PyQt6', 'Qt6', 'plugins')
    if not os.path.isdir(qt_plugins):
        qt_plugins = os.path.join(bundle_dir, 'PyQt6', 'Qt', 'plugins')
    if os.path.isdir(qt_plugins):
        os.environ['QT_PLUGIN_PATH'] = qt_plugins

    # Qt platform plugin path
    qt_platforms = os.path.join(qt_plugins, 'platforms')
    if os.path.isdir(qt_platforms):
        os.environ['QT_QPA_PLATFORM_PLUGIN_PATH'] = qt_platforms

    # Ensure macOS layer-backed views
    os.environ.setdefault('QT_MAC_WANTS_LAYER', '1')
