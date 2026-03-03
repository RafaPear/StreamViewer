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
* Seamless reconnect: when a stream ends cleanly (e.g. IPTV server
  closes the HTTP connection), we immediately create a fresh media
  object and reconnect without showing any status to the user.
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
_CONNECT_TIMEOUT = 45.0
# Seconds of buffering mid-stream before we consider the connection lost.
_BUFFER_TOLERANCE = 60.0
# Seconds of "Playing" with no time progress before declaring a stall.
_STALL_TIMEOUT = 30.0
# Poll interval in seconds.
_POLL_INTERVAL = 0.2
# How long to wait after an embed (reparent) before monitoring state.
_EMBED_GRACE = 3.0
# Buffering shorter than this (seconds) is absorbed silently.
_BUFFER_SILENT = 1.5
# Buffering longer than this shows elapsed seconds.
_BUFFER_WARN = 5.0
# Seconds to confirm Ended state before acting (filters transient flickers).
_ENDED_CONFIRM = 0.6

# Break-reason flags (why the inner monitoring loop exited).
_REASON_RESTART = "restart"      # user / settings triggered restart
_REASON_ENDED = "ended"          # stream ended cleanly (seamless reconnect)
_REASON_ERROR = "error"          # stream error / timeout / stall


def _media_options(cfg: Config) -> list[str]:
    """Build per-media VLC options from config."""
    opts = [
        f":network-caching={cfg.vlc_network_cache}",
        f":live-caching={cfg.vlc_live_cache}",
        f":file-caching={cfg.vlc_network_cache}",
        f":http-user-agent={_UA}",
        ":adaptive-logic=highest",
        ":clock-jitter=5000",       # tolerate 5s clock drift before resync
    ]
    if cfg.cenc_decryption_key:
        opts.append(f":ts-csa-ck={cfg.cenc_decryption_key}")
    return opts


def _upscale_options(preset: str) -> list[str]:
    """Per-media VLC options for the given upscale preset (fullscreen only)."""
    if preset == "lanczos":
        return [
            ":avcodec-hw=none",
            ":swscale-mode=9",              # Lanczos interpolation
        ]
    if preset == "sharpen_light":
        return [
            ":avcodec-hw=none",
            ":swscale-mode=9",
            ":video-filter=sharpen",
            ":sharpen-sigma=0.03",
        ]
    if preset == "sharpen_medium":
        return [
            ":avcodec-hw=none",
            ":swscale-mode=9",
            ":video-filter=sharpen",
            ":sharpen-sigma=0.06",
        ]
    if preset == "sharpen_strong":
        return [
            ":avcodec-hw=none",
            ":swscale-mode=9",
            ":video-filter=sharpen",
            ":sharpen-sigma=0.12",
        ]
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


async def capture_loop(widget, loop, cfg: Config) -> None:
    """
    Per-stream async loop: start VLC playback, monitor state, retry on failure.

    Parameters match the old ffmpeg-based signature so MainWindow.set_loop()
    works unchanged.
    """
    attempt = 0
    max_retries = cfg.max_retries  # 0 = unlimited
    name = widget.channel.display_name()
    seamless = False  # True when reconnecting after a clean Ended

    try:
        while True:
            if widget._released:
                return

            widget._restart_requested = False
            attempt += 1

            if max_retries and attempt > max_retries:
                _safe(widget.show_status, "Connection failed — no more retries", "error")
                logger.error("[%s] max retries (%d) reached", name, max_retries)
                return

            # ── Status message ────────────────────────────────────────────
            if seamless:
                pass  # silent reconnect — no visible indicator
            elif attempt == 1:
                _safe(widget.show_status, "Connecting…", "info")
            else:
                _safe(widget.show_status, "Reconnecting…", "warn")

            # ── Pre-start delay ───────────────────────────────────────────
            _safe(widget.stop)
            if seamless:
                # Minimal delay for seamless reconnect (Ended → fresh media).
                await asyncio.sleep(0.15)
            else:
                jitter = widget.index * 0.3
                await asyncio.sleep(0.5 + jitter)
            seamless = False

            # ── Start playback ────────────────────────────────────────────
            url, options = _build_options(widget, cfg)
            try:
                widget.play_url(url, options)
            except Exception as exc:
                logger.error("[%s] failed to start playback: %s", name, exc)
                _safe(widget.show_status, "Playback error", "error")
                await asyncio.sleep(2)
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
            break_reason = _REASON_ERROR

            # ── Inner monitoring loop ─────────────────────────────────────
            while True:
                if widget._released:
                    return

                if widget._restart_requested:
                    logger.debug("[%s] restart requested", name)
                    break_reason = _REASON_RESTART
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

                # ── Playing ───────────────────────────────────────────────
                if state == vlc.State.Playing:
                    if not started:
                        started = True
                        _safe(widget.hide_status)
                        _safe(widget.set_audio_active, widget._active and cfg.audio_enabled)
                        QTimer.singleShot(500, lambda: _safe(widget.reapply_audio))
                        _safe(widget.prefetch_variants)
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
                        break_reason = _REASON_ERROR
                        break

                # ── Buffering ─────────────────────────────────────────────
                elif state == vlc.State.Buffering:
                    terminal_ticks = 0
                    if started:
                        buffer_ticks += 1
                        secs = buffer_ticks * _POLL_INTERVAL
                        if secs >= _BUFFER_TOLERANCE:
                            logger.warning("[%s] buffering too long (%.0fs), reconnecting",
                                           name, _BUFFER_TOLERANCE)
                            _safe(widget.show_status, "Connection lost", "error")
                            break_reason = _REASON_ERROR
                            break
                        elif secs >= _BUFFER_WARN:
                            _safe(widget.show_status,
                                  f"Buffering… {secs:.0f}s", "warn")
                        elif secs >= _BUFFER_SILENT:
                            _safe(widget.show_status, "Buffering…", "info")
                        # else: brief hiccup — absorbed silently
                    else:
                        _safe(widget.show_status, "Buffering…", "info")

                # ── Ended (after playback had started) ────────────────────
                elif state == vlc.State.Ended and started:
                    terminal_ticks += 1
                    if terminal_ticks * _POLL_INTERVAL >= _ENDED_CONFIRM:
                        logger.info("[%s] stream ended — seamless reconnect", name)
                        break_reason = _REASON_ENDED
                        break

                # ── Ended (never started) or Error ────────────────────────
                elif state in (vlc.State.Ended, vlc.State.Error):
                    if started or grace_ticks > 50:
                        terminal_ticks += 1
                        if terminal_ticks * _POLL_INTERVAL >= 3.0:
                            kind = "ended" if state == vlc.State.Ended else "error"
                            logger.warning("[%s] stream %s (confirmed after %.1fs)",
                                           name, kind, terminal_ticks * _POLL_INTERVAL)
                            lvl = "info" if state == vlc.State.Ended else "error"
                            msg = "Stream ended" if state == vlc.State.Ended \
                                else "Stream error"
                            _safe(widget.show_status, msg, lvl)
                            break_reason = _REASON_ERROR
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
                        break_reason = _REASON_ERROR
                        break
                    elif elapsed >= 5:
                        _safe(widget.show_status,
                              f"Connecting… {elapsed:.0f}s", "info")

                grace_ticks += 1
                await asyncio.sleep(_POLL_INTERVAL)

            # ── Post-loop: decide how to reconnect ────────────────────────
            _safe(widget.stop)

            if break_reason == _REASON_RESTART:
                attempt = 0
                continue

            if break_reason == _REASON_ENDED:
                # Seamless reconnect: stream ended cleanly (server closed
                # the HTTP connection / TS stream finished).  Create a
                # fresh media object immediately — no status shown.
                seamless = True
                attempt = 0
                continue

            # Error / stall / timeout — normal reconnect with status.
            if started:
                attempt = 0

    except asyncio.CancelledError:
        try:
            widget.stop()
        except Exception:
            pass
