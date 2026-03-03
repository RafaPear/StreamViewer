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
  closes the HTTP connection), we create a fresh media via set_media()
  WITHOUT calling stop() – VLC keeps the last frame visible during
  the transition, eliminating black-screen flicker.
"""

import asyncio
import logging
import random
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
_STALL_TIMEOUT = 15.0
# Poll interval in seconds.
_POLL_INTERVAL = 0.2
# How long to wait after an embed (reparent) before monitoring state.
_EMBED_GRACE = 3.0
# Buffering shorter than this (seconds) is absorbed silently.
_BUFFER_SILENT = 1.5
# Buffering longer than this shows elapsed seconds.
_BUFFER_WARN = 5.0
# Seconds to confirm Ended state before recovery/reconnect.
# Short — with http-reconnect VLC rarely reaches Ended; this is a fast fallback.
_ENDED_CONFIRM = 0.4
# Smart buffer: minimum stall threshold (seconds).
_SMART_MIN_SECS = 8.0
# Smart buffer: how many seconds of playback to observe for auto-tuning.
_SMART_LEARN_SECS = 30.0
# Smart buffer: multiplier applied to observed max data gap.
_SMART_GAP_MULT = 2.5
# Seconds of stable playback before clearing reconnect backoff.
_STABLE_PLAY_RESET = 20.0
# Retry delay jitter (+/- percentage).
_RETRY_JITTER = 0.2
# Smart buffer: minimum playback stall to pair with data stall.
_SMART_PLAY_STALL_MIN = 2.0
# Cooldown between smart-buffer-triggered reconnects.
_SMART_RECONNECT_COOLDOWN = 20.0
# Ignore restart requests during first playback warmup seconds.
_RESTART_WARMUP = 8.0

# Break-reason flags (why the inner monitoring loop exited).
_REASON_RESTART = "restart"      # user / settings triggered restart
_REASON_ENDED = "ended"          # stream ended cleanly (seamless reconnect)
_REASON_ERROR = "error"          # stream error / timeout / stall


def _media_options(cfg: Config) -> list[str]:
    """Build per-media VLC options from config."""
    opts = [
        f":network-caching={cfg.vlc_network_cache}",
        f":live-caching={cfg.vlc_live_cache}",
        f":http-user-agent={_UA}",
        ":adaptive-logic=highest",
        ":http-reconnect=true",     # reconnect at HTTP level (no Ended state)
        ":http-continuous=true",    # keep reading past EOF on live streams
    ]
    if cfg.cenc_decryption_key:
        opts.append(f":ts-csa-ck={cfg.cenc_decryption_key}")
    return opts


def _upscale_options(preset: str) -> list[str]:
    """Per-media VLC options for the given upscale preset (fullscreen only).

    Note: we do NOT set :avcodec-hw=none — let VLC use hardware decode
    and copy frames to CPU for the filter.  Full software decode causes
    massive frame drops on HD streams.
    """
    if preset == "lanczos":
        return [
            ":swscale-mode=9",              # Lanczos interpolation
        ]
    if preset == "sharpen_light":
        return [
            ":swscale-mode=9",
            ":video-filter=sharpen",
            ":sharpen-sigma=0.03",
        ]
    if preset == "sharpen_medium":
        return [
            ":swscale-mode=9",
            ":video-filter=sharpen",
            ":sharpen-sigma=0.06",
        ]
    if preset == "sharpen_strong":
        return [
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


def _reset_monitor_state() -> dict:
    """Return a fresh set of monitoring counters."""
    return dict(
        started=False,
        grace_ticks=0,
        connect_ticks=0,
        buffer_ticks=0,
        stall_ticks=0,
        last_time=-1,
        terminal_ticks=0,
        last_read_bytes=-1,
        read_stall_ticks=0,
        play_start_mono=0.0,
        max_data_gap=0.0,
        learning=True,
        smart_threshold=_SMART_MIN_SECS,
    )


async def capture_loop(widget, loop, cfg: Config) -> None:
    """
    Per-stream async loop: start VLC playback, monitor state, retry on failure.

    Parameters match the old ffmpeg-based signature so MainWindow.set_loop()
    works unchanged.
    """
    attempt = 0
    failure_streak = 0
    last_smart_reconnect_mono = 0.0
    max_retries = cfg.max_retries  # 0 = unlimited
    name = widget.channel.display_name()

    try:
        while True:
            if widget._released:
                return

            widget._restart_requested = False
            attempt += 1

            if max_retries and attempt > max_retries:
                _safe(widget.show_status, "Connection failed \u2014 no more retries", "error")
                logger.error("[%s] max retries (%d) reached", name, max_retries)
                return

            # ── Status message ────────────────────────────────────────────
            if attempt == 1:
                _safe(widget.show_status, "Connecting\u2026", "info")
            else:
                _safe(widget.show_status, "Reconnecting\u2026", "warn")

            # ── Pre-start delay ───────────────────────────────────────────
            _safe(widget.stop)
            jitter = widget.index * 0.3
            prestart_delay = 0.5 + jitter
            if failure_streak > 0:
                base = max(0.5, float(cfg.retry_delay))
                cap = max(base, float(cfg.max_retry_delay))
                backoff = min(cap, base * (2 ** (failure_streak - 1)))
                backoff *= random.uniform(1.0 - _RETRY_JITTER, 1.0 + _RETRY_JITTER)
                prestart_delay += backoff
                _safe(widget.show_status, f"Reconnecting in {backoff:.1f}s…", "warn")
                logger.info(
                    "[%s] reconnect backoff %.1fs (streak=%d)",
                    name, backoff, failure_streak,
                )
            await asyncio.sleep(prestart_delay)

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

            was_active = widget._active
            break_reason = _REASON_ERROR
            smart = cfg.smart_buffer
            m = _reset_monitor_state()

            # ── Inner monitoring loop ─────────────────────────────────────
            while True:
                if widget._released:
                    return

                if widget._restart_requested:
                    force_restart = bool(getattr(widget, "_restart_force", False))
                    if (not force_restart and
                            (not m["started"]
                             or (time.monotonic() - m["play_start_mono"]) < _RESTART_WARMUP)):
                        widget._restart_requested = False
                        logger.debug("[%s] restart ignored during warmup", name)
                        await asyncio.sleep(_POLL_INTERVAL)
                        continue
                    widget._restart_requested = False
                    widget._restart_force = False
                    logger.debug("[%s] restart requested", name)
                    break_reason = _REASON_RESTART
                    break

                # Toggle audio when active state changes.
                if widget._active != was_active:
                    _safe(widget.set_audio_active, widget._active and cfg.audio_enabled)
                    was_active = widget._active

                # Skip state monitoring during layout transitions (reparent).
                if time.monotonic() - widget._last_embed_time < _EMBED_GRACE:
                    m["grace_ticks"] += 1
                    await asyncio.sleep(_POLL_INTERVAL)
                    continue

                state = _safe(widget.get_state, default=vlc.State.Error)
                if state is None:
                    state = vlc.State.Error

                # ── Playing ───────────────────────────────────────────────
                if state == vlc.State.Playing:
                    if not m["started"]:
                        m["started"] = True
                        m["play_start_mono"] = time.monotonic()
                        _safe(widget.hide_status)
                        _safe(widget.set_audio_active, widget._active and cfg.audio_enabled)
                        QTimer.singleShot(500, lambda: _safe(widget.reapply_audio))
                        attempt = 0
                        logger.info("[%s] playback started", name)
                    elif (failure_streak > 0
                          and (time.monotonic() - m["play_start_mono"]) >= _STABLE_PLAY_RESET):
                        logger.info("[%s] stable playback %.0fs, clearing reconnect backoff",
                                    name, _STABLE_PLAY_RESET)
                        failure_streak = 0

                    m["buffer_ticks"] = 0
                    m["terminal_ticks"] = 0

                    cur_time = _safe(lambda: widget._player.get_time(), default=-1)
                    if cur_time is not None and cur_time == m["last_time"]:
                        m["stall_ticks"] += 1
                    else:
                        m["stall_ticks"] = 0
                    m["last_time"] = cur_time if cur_time is not None else m["last_time"]

                    if (_STALL_TIMEOUT > 0
                            and m["stall_ticks"] * _POLL_INTERVAL >= _STALL_TIMEOUT):
                        logger.warning("[%s] stall detected (no progress for %.0fs)",
                                       name, _STALL_TIMEOUT)
                        _safe(widget.show_status, "Stream stalled", "warn")
                        break_reason = _REASON_ERROR
                        break

                    # ── Smart buffer: auto-tuned network stall detection ──
                    if smart and widget._media is not None:
                        try:
                            stats = vlc.MediaStats()
                            if widget._media.get_stats(stats):
                                rb = stats.read_bytes
                                if m["last_read_bytes"] >= 0:
                                    if rb == m["last_read_bytes"]:
                                        m["read_stall_ticks"] += 1
                                        gap = m["read_stall_ticks"] * _POLL_INTERVAL
                                        if m["learning"]:
                                            m["max_data_gap"] = max(m["max_data_gap"], gap)
                                    else:
                                        m["read_stall_ticks"] = 0
                                else:
                                    m["read_stall_ticks"] = 0
                                m["last_read_bytes"] = rb

                                # End learning phase after _SMART_LEARN_SECS.
                                if m["learning"] and (time.monotonic() - m["play_start_mono"]) >= _SMART_LEARN_SECS:
                                    m["learning"] = False
                                    m["smart_threshold"] = max(
                                        m["max_data_gap"] * _SMART_GAP_MULT,
                                        _SMART_MIN_SECS,
                                    )
                                    logger.debug(
                                        "[%s] smart buffer: learned max_gap=%.1fs, threshold=%.1fs",
                                        name, m["max_data_gap"], m["smart_threshold"],
                                    )

                                # Trigger only when BOTH bytes AND playback stalled.
                                data_stall_secs = m["read_stall_ticks"] * _POLL_INTERVAL
                                playback_stall_secs = m["stall_ticks"] * _POLL_INTERVAL
                                smart_cooldown_ok = (
                                    (time.monotonic() - last_smart_reconnect_mono)
                                    >= _SMART_RECONNECT_COOLDOWN
                                )
                                if (not m["learning"]
                                        and data_stall_secs >= m["smart_threshold"]
                                        and playback_stall_secs >= _SMART_PLAY_STALL_MIN
                                        and smart_cooldown_ok):
                                    logger.info(
                                        "[%s] smart buffer: network+playback stall "
                                        "(%.1fs no bytes, %.1fs no progress), warning only",
                                        name, data_stall_secs,
                                        playback_stall_secs,
                                    )
                                    _safe(widget.show_status, "Network unstable…", "warn")
                                    last_smart_reconnect_mono = time.monotonic()
                        except Exception:
                            pass

                # ── Buffering ─────────────────────────────────────────────
                elif state == vlc.State.Buffering:
                    m["terminal_ticks"] = 0
                    if m["started"]:
                        m["buffer_ticks"] += 1
                        secs = m["buffer_ticks"] * _POLL_INTERVAL
                        if secs >= _BUFFER_TOLERANCE:
                            logger.warning("[%s] buffering too long (%.0fs), reconnecting",
                                           name, _BUFFER_TOLERANCE)
                            _safe(widget.show_status, "Connection lost", "error")
                            break_reason = _REASON_ERROR
                            break
                        elif secs >= _BUFFER_WARN:
                            _safe(widget.show_status,
                                  f"Buffering\u2026 {secs:.0f}s", "warn")
                        elif secs >= _BUFFER_SILENT:
                            _safe(widget.show_status, "Buffering\u2026", "info")
                        # else: brief hiccup — absorbed silently
                    else:
                        _safe(widget.show_status, "Buffering\u2026", "info")

                # ── Ended (after playback had started) ────────────────────
                elif state == vlc.State.Ended and m["started"]:
                    m["terminal_ticks"] += 1
                    if m["terminal_ticks"] * _POLL_INTERVAL >= _ENDED_CONFIRM:
                        # VLC's http-reconnect didn't recover within the
                        # confirmation window.  Try a seamless media swap
                        # (keeps last frame visible), else fall back to
                        # a full stop+restart.
                        url, options = _build_options(widget, cfg)
                        try:
                            widget.play_url(url, options, seamless=True)
                        except Exception:
                            break_reason = _REASON_ENDED
                            break
                        await asyncio.sleep(0.5)
                        new_state = _safe(widget.get_state, default=vlc.State.Error)
                        if new_state in (vlc.State.Opening, vlc.State.Buffering,
                                         vlc.State.Playing):
                            logger.debug("[%s] seamless reconnect (no stop)", name)
                            attempt = 0
                            m["terminal_ticks"] = 0
                            m["buffer_ticks"] = 0
                            m["stall_ticks"] = 0
                            m["last_time"] = -1
                            m["last_read_bytes"] = -1
                            m["read_stall_ticks"] = 0
                            continue
                        else:
                            logger.debug("[%s] seamless failed (state=%s), full reconnect",
                                         name, new_state)
                            break_reason = _REASON_ENDED
                            break

                # ── Ended (never started) or Error ────────────────────────
                elif state in (vlc.State.Ended, vlc.State.Error):
                    if m["started"] or m["grace_ticks"] > 50:
                        m["terminal_ticks"] += 1
                        if m["terminal_ticks"] * _POLL_INTERVAL >= 3.0:
                            kind = "ended" if state == vlc.State.Ended else "error"
                            logger.warning("[%s] stream %s (confirmed after %.1fs)",
                                           name, kind, m["terminal_ticks"] * _POLL_INTERVAL)
                            lvl = "info" if state == vlc.State.Ended else "error"
                            msg = "Stream ended" if state == vlc.State.Ended \
                                else "Stream error"
                            _safe(widget.show_status, msg, lvl)
                            break_reason = _REASON_ERROR
                            break

                else:
                    m["terminal_ticks"] = 0

                # Connection timeout (never reached Playing state).
                if not m["started"]:
                    m["connect_ticks"] += 1
                    elapsed = m["connect_ticks"] * _POLL_INTERVAL
                    if elapsed >= _CONNECT_TIMEOUT:
                        logger.warning("[%s] connection timeout (%.0fs), retrying",
                                       name, _CONNECT_TIMEOUT)
                        _safe(widget.show_status, "Connection timed out", "error")
                        break_reason = _REASON_ERROR
                        break
                    elif elapsed >= 5:
                        _safe(widget.show_status,
                              f"Connecting\u2026 {elapsed:.0f}s", "info")

                m["grace_ticks"] += 1
                await asyncio.sleep(_POLL_INTERVAL)

            # ── Post-loop: decide how to reconnect ────────────────────────
            _safe(widget.stop)
            logger.info("[%s] reconnect reason=%s (started=%s, streak=%d)",
                        name, break_reason, m["started"], failure_streak)

            if break_reason == _REASON_RESTART:
                attempt = 0
                failure_streak = 0
                continue

            if break_reason == _REASON_ENDED:
                # Seamless path broke out due to play_url failure.
                attempt = 0
                failure_streak = 0
                continue

            # Error / stall / timeout — normal reconnect with status.
            if m["started"]:
                attempt = 0
            failure_streak += 1

    except asyncio.CancelledError:
        try:
            widget.stop()
        except Exception:
            pass
