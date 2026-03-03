"""
streams_client.py – Entry point for StreamsClient.

Usage:
  python streams_client.py                              # restore session or show picker
  python streams_client.py -p playlist.m3u              # load from local M3U/M3U8
  python streams_client.py -p http://host/pl.m3u8       # load from remote M3U8
  python streams_client.py -e                           # start with no streams
  python streams_client.py -s rtsp://cam1 rtsp://cam2   # open specific stream URLs
  python streams_client.py --grid 3x3                   # set grid size
  python streams_client.py --preset "News 2x2"          # load a saved grid preset
  python streams_client.py --fullscreen                  # start in fullscreen
  python streams_client.py --no-audio                    # start with audio disabled
  python streams_client.py --reset                       # reset config to defaults
"""

import argparse
import asyncio
import logging
import os
import sys
from logging.handlers import RotatingFileHandler

# Set VLC plugin path on macOS before importing vlc.
if sys.platform == "darwin":
    _vlc_lib = "/Applications/VLC.app/Contents/MacOS/lib"
    _vlc_plugins = "/Applications/VLC.app/Contents/MacOS/plugins"
    if os.path.isdir(_vlc_lib):
        os.environ.setdefault("DYLD_LIBRARY_PATH", _vlc_lib)
    if os.path.isdir(_vlc_plugins):
        os.environ.setdefault("VLC_PLUGIN_PATH", _vlc_plugins)

# Suppress harmless Qt font warnings on macOS.
os.environ.setdefault("QT_LOGGING_RULES", "qt.text.font.db=false")

import vlc
import qasync
from PyQt6.QtWidgets import QApplication, QMessageBox

from config import APP_DIR, load_config
from dialogs import ChannelPickerDialog
from main_window import MainWindow
from models import Channel, parse_m3u


def _setup_logging() -> None:
    """Write DEBUG+ to logs/streams.log, WARNING+ to the terminal."""
    log_dir = APP_DIR / "logs"
    log_dir.mkdir(exist_ok=True)

    root = logging.getLogger()
    root.setLevel(logging.DEBUG)

    # Silence noisy third-party loggers.
    for noisy in ("qasync", "asyncio"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    fh = RotatingFileHandler(
        log_dir / "streams.log",
        maxBytes=5 * 1024 * 1024,   # 5 MB per file
        backupCount=3,
        encoding="utf-8",
    )
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(logging.Formatter(
        "%(asctime)s %(levelname)-8s %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    ))
    root.addHandler(fh)

    ch = logging.StreamHandler()
    ch.setLevel(logging.WARNING)
    ch.setFormatter(logging.Formatter("%(levelname)s %(name)s: %(message)s"))
    root.addHandler(ch)


def main() -> None:
    _setup_logging()
    parser = argparse.ArgumentParser(
        description="StreamsClient – multi-stream video viewer",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "examples:\n"
            "  %(prog)s -p https://example.com/tv.m3u8\n"
            "  %(prog)s -s rtsp://cam1 rtsp://cam2 --grid 1x2\n"
            "  %(prog)s --preset 'News 2x2'\n"
            "  %(prog)s -e            # empty start, add streams from menus\n"
        ),
    )
    parser.add_argument("-p", "--playlist", metavar="FILE_OR_URL",
                        help="M3U/M3U8 playlist to load (overrides saved session)")
    parser.add_argument("-s", "--streams", nargs="+", metavar="URL",
                        help="One or more stream URLs to open directly")
    parser.add_argument("-e", "--empty", action="store_true",
                        help="Start with no streams loaded")
    parser.add_argument("--grid", metavar="RxC",
                        help="Set grid size, e.g. 2x2, 3x3, or 'dynamic'")
    parser.add_argument("--preset", metavar="NAME",
                        help="Load a saved grid preset by name")
    parser.add_argument("--fullscreen", action="store_true",
                        help="Start in fullscreen mode")
    parser.add_argument("--no-audio", action="store_true",
                        help="Start with audio disabled")
    parser.add_argument("--reset", action="store_true",
                        help="Reset configuration to defaults and exit")
    parser.add_argument("--list-presets", action="store_true",
                        help="List saved grid presets and exit")
    parser.add_argument("--list-favourites", action="store_true",
                        help="List saved favourite channels and exit")
    parser.add_argument("-v", "--verbose", action="store_true",
                        help="Show all log output in the terminal")
    args = parser.parse_args()

    cfg = load_config()

    # ── Info-only commands (no GUI) ───────────────────────────────────────────

    if args.reset:
        from config import CONFIG_FILE, save_config as _save
        _save(Config())
        print("Configuration reset to defaults.")
        sys.exit(0)

    if args.list_presets:
        if not cfg.grid_presets:
            print("No saved grid presets.")
        else:
            for i, p in enumerate(cfg.grid_presets, 1):
                grid = "dynamic" if p.get("dynamic") else f"{p.get('rows',2)}x{p.get('cols',2)}"
                n = len(p.get("channels", []))
                print(f"  {i}. {p['name']}  ({grid}, {n} streams)")
        sys.exit(0)

    if args.list_favourites:
        if not cfg.favourites:
            print("No saved favourites.")
        else:
            for i, f in enumerate(cfg.favourites, 1):
                name = f.get("name") or f.get("url", "?")
                print(f"  {i}. {name}  —  {f.get('url', '')}")
        sys.exit(0)

    if args.verbose:
        for h in logging.getLogger().handlers:
            if isinstance(h, logging.StreamHandler) and not isinstance(h, RotatingFileHandler):
                h.setLevel(logging.DEBUG)

    # ── Apply CLI overrides to config ─────────────────────────────────────────

    if args.grid:
        g = args.grid.strip().lower()
        if g == "dynamic":
            cfg.dynamic_grid = True
        else:
            try:
                r, c = g.split("x")
                cfg.grid_rows = max(1, min(6, int(r)))
                cfg.grid_cols = max(1, min(6, int(c)))
                cfg.dynamic_grid = False
            except ValueError:
                parser.error(f"Invalid grid format '{args.grid}'. Use RxC (e.g. 2x2) or 'dynamic'.")

    if args.no_audio:
        cfg.audio_enabled = False

    app = QApplication(sys.argv)

    # ── Resolve channel list ──────────────────────────────────────────────────

    if args.preset:
        preset = next((p for p in cfg.grid_presets if p["name"] == args.preset), None)
        if preset is None:
            print(f"Preset '{args.preset}' not found. Use --list-presets to see available presets.",
                  file=sys.stderr)
            sys.exit(1)
        cfg.grid_rows = preset.get("rows", 2)
        cfg.grid_cols = preset.get("cols", 2)
        cfg.dynamic_grid = preset.get("dynamic", False)
        channels: list[Channel] = [Channel.from_dict(d) for d in preset.get("channels", [])]

    elif args.streams:
        channels = [Channel(url=u) for u in args.streams]

    elif args.empty:
        channels = []

    elif args.playlist:
        channels = _load_and_pick(args.playlist, cfg)
        if channels is None:
            sys.exit(0)

    elif cfg.remember_session and cfg.last_channels:
        channels = [Channel.from_dict(d) for d in cfg.last_channels]

    else:
        if cfg.default_playlist:
            channels = _load_and_pick(cfg.default_playlist, cfg)
            if channels is None:
                channels = []
        else:
            channels = []

    # ── Initialize VLC ────────────────────────────────────────────────────────

    vlc_args = [
        "--quiet",
        "--no-video-title-show",
    ]
    if sys.platform == "darwin":
        vlc_args.append("--avcodec-hw=videotoolbox")
    else:
        vlc_args.append("--avcodec-hw=any")

    try:
        vlc_instance = vlc.Instance(vlc_args)
        if vlc_instance is None:
            raise RuntimeError("vlc.Instance() returned None")
    except Exception as exc:
        QMessageBox.critical(
            None,
            "VLC Required",
            f"Failed to initialize VLC: {exc}\n\n"
            "Install VLC from https://www.videolan.org/vlc/",
        )
        sys.exit(1)

    # ── Launch window ─────────────────────────────────────────────────────────

    window = MainWindow(channels, cfg, vlc_instance)
    if args.fullscreen:
        window.showFullScreen()
    else:
        window.show()

    async def _run() -> None:
        loop = asyncio.get_event_loop()
        window.set_loop(loop)
        try:
            while window.isVisible():
                await asyncio.sleep(0.05)
        except asyncio.CancelledError:
            pass
        finally:
            # Cancel all capture tasks and wait at most 3 s for them to stop.
            tasks = list(window._tasks.values())
            for t in tasks:
                t.cancel()
            if tasks:
                try:
                    await asyncio.wait_for(
                        asyncio.gather(*tasks, return_exceptions=True),
                        timeout=3.0,
                    )
                except (asyncio.TimeoutError, RuntimeError):
                    pass
            # Ordered release: players first, then VLC instance.
            for w in window._widgets:
                w.release()
            try:
                vlc_instance.release()
            except Exception:
                pass

    with qasync.QEventLoop(app) as loop:
        try:
            loop.run_until_complete(_run())
        except RuntimeError:
            pass  # event loop stopped on window close — expected


def _load_and_pick(source: str, cfg) -> list[Channel] | None:
    """Fetch + parse an M3U, show the channel picker, return selected channels."""
    try:
        all_channels = parse_m3u(source)
    except Exception as exc:
        QMessageBox.critical(None, "Playlist Error", str(exc))
        return None

    if not all_channels:
        QMessageBox.information(None, "Empty Playlist", "No streams found in the playlist.")
        return None

    picker = ChannelPickerDialog(all_channels)
    if picker.exec() == 0:  # Rejected
        return None

    return picker.selected_channels() or None


if __name__ == "__main__":
    main()
