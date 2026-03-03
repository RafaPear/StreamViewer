"""capture.py – VLC-based stream monitoring and retry logic.

Design
------
* Each stream gets an async coroutine that starts VLC playback, monitors
  the player state, and retries on failure with exponential back-off.
* VLC handles decoding (HW-accelerated), buffering, audio sync, and
  adaptive bitrate internally – no manual frame piping needed.
* Audio is routed only to the active stream; mute state is toggled from
  the main window when the active selection changes.
* Connection timeout prevents indefinite hangs in Opening/Buffering.
* Mid-stream buffering tolerance avoids killing streams during brief
  network hiccups.
* Stall detection catches frozen streams (Playing state but no progress).
"""

import asyncio
import logging
import time

import vlc

from PyQt6.QtCore import QTimer
from config import Config

logger = logging.getLogger(__name__)

_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
)

# Seconds to wait in Opening/Buffering before declaring a connection timeout.
_CONNECT_TIMEOUT = 30.0
# Seconds of buffering mid-stream before we consider the connection lost.
_BUFFER_TOLERANCE = 15.0
# Seconds of "Playing" with no time progress before declaring a stall.
_STALL_TIMEOUT = 10.0
# Poll interval in seconds.
_POLL_INTERVAL = 0.2
# How long to wait after an embed (reparent) before monitoring state.
_EMBED_GRACE = 3.0


def _media_options(cfg: Config) -> list[str]:
    """Build per-media VLC options from config."""
    opts = [
        f":network-caching={cfg.vlc_network_cache}",
        f":live-caching={cfg.vlc_live_cache}",
        f":http-user-agent={_UA}",
        ":adaptive-logic=highest",
    ]
    if cfg.cenc_decryption_key:
        opts.append(f":ts-csa-ck={cfg.cenc_decryption_key}")
    return opts


def _upscale_options(preset: str) -> list[str]:
    """Per-media VLC options for the given upscale preset (fullscreen only)."""
    return []


def _safe(fn, *args, default=None):
    """Call *fn* catching any exception (widget may have been deleted)."""
    try:
        return fn(*args)
    except Exception:
        return default


def _build_options(widget, cfg: Config) -> tuple[str, list[str]]:
    """Return (url, options) for the stream, including upscale if active."""
    options = _media_options(cfg)
    upscale_preset = getattr(widget, '_upscale_preset', 'off')
    if upscale_preset != 'off':
        options.extend(_upscale_options(upscale_preset))
    url = widget._quality_url or widget.channel.url
    if widget._quality_url:
        options = [o for o in options if not o.startswith(":adaptive-")]
    return url, options


async def _countdown(widget, msg: str, seconds: float, level: str = "warn") -> None:
    """Show a countdown on the widget overlay before retrying."""
    remaining = int(seconds)
    while remaining > 0:
        if widget._released or widget._restart_requested:
            return
        _safe(widget.show_status, f"{msg} (retrying in {remaining}s)", level)
        await asyncio.sleep(1)
        remaining -= 1


async def capture_loop(widget, loop, cfg: Config) -> None:
    """
    Per-stream async loop: start VLC playback, monitor state, retry on failure.

    Parameters match the old ffmpeg-based signature so MainWindow.set_loop()
    works unchanged.
    """
    retry_delay = cfg.retry_delay
    attempt = 0
    max_retries = cfg.max_retries  # 0 = unlimited
    name = widget.channel.display_name()

    try:
        while True:
            if widget._released:
                return

            # If paused for single-view mode, just sleep until resumed.
            if getattr(widget, '_paused_for_single_view', False):
                await asyncio.sleep(1)
                continue

            widget._restart_requested = False
            attempt += 1

            if max_retries and attempt > max_retries:
                _safe(widget.show_status, "Connection failed — no more retries", "error")
                logger.error("[%s] max retries (%d) reached", name, max_retries)
                return

            if attempt == 1:
                _safe(widget.show_status, "Connecting…", "info")
            else:
                _safe(widget.show_status, f"Reconnecting (attempt {attempt})…", "warn")

            # Brief pause before (re)starting – gives VLC time to clean up.
            await asyncio.sleep(0.3)

            url, options = _build_options(widget, cfg)
            try:
                widget.play_url(url, options)
            except Exception as exc:
                logger.error("[%s] failed to start playback: %s", name, exc)
                await _countdown(widget, "Playback error", retry_delay, "error")
                retry_delay = min(retry_delay * 2, cfg.max_retry_delay)
                continue

            logger.info("[%s] starting VLC playback (attempt %d)", name, attempt)

            started = False
            was_active = widget._active
            grace_ticks = 0
            connect_ticks = 0
            buffer_ticks = 0
            stall_ticks = 0
            last_time = -1
            terminal_ticks = 0

            while True:
                if widget._released:
                    return

                # Paused for single-view — stop and go back to outer loop.
                if getattr(widget, '_paused_for_single_view', False):
                    break

                if widget._restart_requested:
                    logger.debug("[%s] restart requested", name)
                    break

                # Toggle audio when active state changes.
                if widget._active != was_active:
                    _safe(widget.set_audio_active, widget._active and cfg.audio_enabled)
                    was_active = widget._active

                # Skip state monitoring during layout transitions (reparent).
                if time.monotonic() - widget._last_embed_time < _EMBED_GRACE:
                    grace_ticks += 1
                    await asyncio.sleep(_POLL_INTERVAL)
                    continue

                state = _safe(widget.get_state, default=vlc.State.Error)
                if state is None:
                    state = vlc.State.Error

                if state == vlc.State.Playing:
                    if not started:
                        started = True
                        _safe(widget.hide_status)
                        _safe(widget.set_audio_active, widget._active and cfg.audio_enabled)
                        QTimer.singleShot(500, lambda: _safe(widget.reapply_audio))
                        _safe(widget.prefetch_variants)
                        retry_delay = cfg.retry_delay
                        attempt = 0
                        logger.info("[%s] playback started", name)

                    buffer_ticks = 0
                    terminal_ticks = 0

                    cur_time = _safe(lambda: widget._player.get_time(), default=-1)
                    if cur_time is not None and cur_time == last_time:
                        stall_ticks += 1
                    else:
                        stall_ticks = 0
                    last_time = cur_time if cur_time is not None else last_time

                    if stall_ticks * _POLL_INTERVAL >= _STALL_TIMEOUT:
                        logger.warning("[%s] stall detected (no progress for %.0fs)",
                                       name, _STALL_TIMEOUT)
                        _safe(widget.show_status, "Stream stalled", "warn")
                        break

                elif state == vlc.State.Buffering:
                    terminal_ticks = 0
                    if started:
                        buffer_ticks += 1
                        secs = buffer_ticks * _POLL_INTERVAL
                        if secs >= _BUFFER_TOLERANCE:
                            logger.warning("[%s] buffering too long (%.0fs), reconnecting",
                                           name, _BUFFER_TOLERANCE)
                            _safe(widget.show_status, "Connection lost", "error")
                            break
                        elif buffer_ticks > 5:
                            _safe(widget.show_status,
                                  f"Buffering… ({secs:.0f}s)", "warn")
                    else:
                        _safe(widget.show_status, "Buffering…", "info")

                elif state in (vlc.State.Ended, vlc.State.Error):
                    if started or grace_ticks > 25:
                        terminal_ticks += 1
                        if terminal_ticks * _POLL_INTERVAL >= 3.0:
                            kind = "ended" if state == vlc.State.Ended else "error"
                            logger.warning("[%s] stream %s (confirmed after %.1fs)",
                                           name, kind, terminal_ticks * _POLL_INTERVAL)
                            lvl = "info" if state == vlc.State.Ended else "error"
                            msg = "Stream ended" if state == vlc.State.Ended \
                                else "Stream error"
                            _safe(widget.show_status, msg, lvl)
                            break

                else:
                    terminal_ticks = 0

                # Connection timeout (never reached Playing state).
                if not started:
                    connect_ticks += 1
                    elapsed = connect_ticks * _POLL_INTERVAL
                    if elapsed >= _CONNECT_TIMEOUT:
                        logger.warning("[%s] connection timeout (%.0fs), retrying",
                                       name, _CONNECT_TIMEOUT)
                        _safe(widget.show_status, "Connection timed out", "error")
                        break
                    elif elapsed >= 10:
                        _safe(widget.show_status,
                              f"Connecting… ({elapsed:.0f}s)", "info")

                grace_ticks += 1
                await asyncio.sleep(_POLL_INTERVAL)

            # ── Clean stop + back-off before retry ────────────────────────
            _safe(widget.stop)

            if getattr(widget, '_paused_for_single_view', False):
                continue

            if widget._restart_requested:
                attempt = 0
                retry_delay = cfg.retry_delay
                continue

            await _countdown(widget, "Waiting to reconnect", retry_delay, "warn")
            retry_delay = min(retry_delay * 2, cfg.max_retry_delay)

    except asyncio.CancelledError:
        try:
            widget.stop()
        except Exception:
            pass
