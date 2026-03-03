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
_CONNECT_TIMEOUT = 45.0
# Seconds of buffering mid-stream before we consider the connection lost.
_BUFFER_TOLERANCE = 30.0
# Seconds of "Playing" with no time progress before declaring a stall.
_STALL_TIMEOUT = 20.0
# Poll interval in seconds.
_POLL_INTERVAL = 0.2


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
    if cfg.upscale_enabled:
        opts.append(":swscale-mode=9")           # Lanczos interpolation
        opts.append(":video-filter=sharpen")      # sharpening filter
        opts.append(":sharpen-sigma=0.04")        # subtle sharpening
    return opts


def _safe(fn, *args, default=None):
    """Call *fn* catching any exception (widget may have been deleted)."""
    try:
        return fn(*args)
    except Exception:
        return default


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

            widget._restart_requested = False
            attempt += 1

            if max_retries and attempt > max_retries:
                _safe(widget.show_status, "Connection failed — no more retries", "error")
                logger.error("[%s] max retries (%d) reached", name, max_retries)
                return

            if attempt == 1:
                _safe(widget.show_status, "Connecting...", "info")
            else:
                _safe(widget.show_status, f"Reconnecting (attempt {attempt})...", "warn")

            # Brief pause before (re)starting – gives VLC time to clean up
            # the previous player state and prevents rapid stop→play segfaults.
            # Stagger restarts across streams to avoid simultaneous VLC calls.
            _safe(widget.stop)
            jitter = widget.index * 0.4
            await asyncio.sleep(0.5 + jitter)

            options = _media_options(cfg)
            url = widget._quality_url or widget.channel.url
            if widget._quality_url:
                options = [o for o in options if not o.startswith(":adaptive-")]
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
            # Connection timeout: ticks spent in non-Playing state before ever playing.
            connect_ticks = 0
            # Mid-stream buffering: ticks spent buffering after stream was playing.
            buffer_ticks = 0
            # Stall detection: ticks of Playing with no time progress.
            stall_ticks = 0
            last_time = -1
            # Consecutive ticks in a terminal state (Ended/Error).
            # VLC can briefly flash Ended between HLS segments; only act
            # when the state persists.
            terminal_ticks = 0

            while True:
                if widget._released:
                    return

                if widget._restart_requested:
                    logger.debug("[%s] restart requested", name)
                    break

                # Toggle audio when active state changes
                if widget._active != was_active:
                    _safe(widget.set_audio_active, widget._active and cfg.audio_enabled)
                    was_active = widget._active

                # Skip state-based error detection during layout transitions.
                # Reparenting the widget gives VLC a new rendering surface which
                # can briefly flash non-Playing states.
                if time.monotonic() - widget._last_embed_time < 3.0:
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
                        # Reapply audio state now that VLC audio subsystem is live,
                        # and schedule a delayed reapply to catch late initialisation.
                        _safe(widget.set_audio_active, widget._active and cfg.audio_enabled)
                        QTimer.singleShot(500, lambda: _safe(widget.reapply_audio))
                        # Preload quality variants in background.
                        _safe(widget.prefetch_variants)
                        retry_delay = cfg.retry_delay
                        attempt = 0  # reset on successful playback
                        logger.info("[%s] playback started", name)

                    # Reset counters — stream is healthy.
                    buffer_ticks = 0
                    terminal_ticks = 0

                    # Stall detection: check if media time is advancing.
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
                    terminal_ticks = 0  # not terminal
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
                                  f"Buffering... ({secs:.0f}s)", "warn")
                    else:
                        _safe(widget.show_status, "Buffering...", "info")

                elif state in (vlc.State.Ended, vlc.State.Error):
                    if started or grace_ticks > 50:  # 10 s grace for startup
                        terminal_ticks += 1
                        # Require 5 s of consecutive terminal state before acting.
                        # VLC can briefly flash Ended between HLS segments.
                        if terminal_ticks * _POLL_INTERVAL >= 5.0:
                            kind = "ended" if state == vlc.State.Ended else "error"
                            logger.warning("[%s] stream %s (confirmed after %.1fs)",
                                           name, kind, terminal_ticks * _POLL_INTERVAL)
                            lvl = "info" if state == vlc.State.Ended else "error"
                            msg = "Stream ended" if state == vlc.State.Ended \
                                else "Stream error"
                            _safe(widget.show_status, msg, lvl)
                            break

                else:
                    # NothingSpecial, Opening, Stopped, etc. — not terminal.
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
                              f"Connecting... ({elapsed:.0f}s)", "info")

                grace_ticks += 1
                await asyncio.sleep(_POLL_INTERVAL)

            # ── Clean stop + back-off before retry ───────────────────────────
            _safe(widget.stop)

            if widget._restart_requested:
                attempt = 0  # user-triggered restart resets counter
                retry_delay = cfg.retry_delay
                continue

            await _countdown(widget, "Waiting to reconnect", retry_delay, "warn")
            retry_delay = min(retry_delay * 2, cfg.max_retry_delay)

    except asyncio.CancelledError:
        try:
            widget.stop()
        except Exception:
            pass
