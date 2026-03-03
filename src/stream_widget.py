"""stream_widget.py – StreamWidget: VLC-based video player widget."""

import asyncio
import sys
import time

import vlc
from PyQt6.QtCore import Qt, QPoint, QTimer, pyqtSignal
from PyQt6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QMenu,
    QPushButton,
    QSizePolicy,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from config import Config
from models import Channel

_BTN_SS = (
    "QPushButton { color: white; background: transparent; border: none;"
    " font-size: 13px; padding: 0 2px; }"
    "QPushButton:hover { background: #333; border-radius: 3px; }"
)

_SLIDER_SS = (
    "QSlider::groove:horizontal { height: 4px; background: #555; border-radius: 2px; }"
    "QSlider::handle:horizontal { width: 10px; margin: -3px 0;"
    " background: white; border-radius: 5px; }"
    "QSlider::sub-page:horizontal { background: #0a84ff; border-radius: 2px; }"
)


class StreamWidget(QWidget):
    """
    Embeds a VLC media player for a single video stream.

    Border trick: VLC paints directly onto the native surface of
    ``_video_frame``, covering any Qt-drawn border.  To make the
    active-stream highlight visible, ``_video_frame`` sits inside
    ``_border_frame`` with a margin gap.  The gap reveals the
    background colour of ``_border_frame`` — that colour **is**
    the border.
    """

    clicked = pyqtSignal(int)
    double_clicked = pyqtSignal(int)
    context_menu_requested = pyqtSignal(int, QPoint)

    def __init__(
        self,
        channel: Channel,
        index: int,
        cfg: Config,
        vlc_instance: vlc.Instance,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.channel = channel
        self.index = index
        self._cfg = cfg
        self._active = False
        self._hovered = False
        self._border_visible = True
        self._controls_pinned = True  # always visible in grid mode
        self._restart_requested = False
        self._restart_force = False
        self._last_restart_request_time: float = 0.0
        self._paused_for_single_view = False
        self._released = False
        self._is_detached = False
        self._upscale_preset: str = "off"

        # VLC
        self._vlc_instance = vlc_instance
        self._player: vlc.MediaPlayer = vlc_instance.media_player_new()
        self._media: vlc.Media | None = None
        self._user_paused = False
        self._embedded_handle: int = 0
        self._last_embed_time: float = 0.0

        # Frame-drop tracking (rate-based, resets every window)
        self._frame_drops = 0
        self._last_lost_pics = 0
        self._drop_window_start: float = time.monotonic()

        # Quality selection
        self._variants: list = []
        self._variants_fetched = False
        self._variants_loading = False
        self._quality_url: str | None = None

        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setCursor(Qt.CursorShape.PointingHandCursor)  # grid mode default

        # ── Layout ───────────────────────────────────────────────────────────
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # Border container — background colour = border colour.
        self._border_frame = QFrame()
        self._border_frame.setStyleSheet("background: #333;")
        self._border_frame.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
        )
        self._border_layout = QVBoxLayout(self._border_frame)
        self._border_layout.setContentsMargins(1, 1, 1, 1)
        self._border_layout.setSpacing(0)

        # Video surface — VLC renders directly into this QFrame.
        self._video_frame = QFrame()
        self._video_frame.setStyleSheet("background: black;")
        self._border_layout.addWidget(self._video_frame)

        root.addWidget(self._border_frame, stretch=1)

        # Status overlay (centered on video frame, visible before playback).
        self._status_label = QLabel("Connecting…", self._video_frame)
        self._status_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._status_label.setStyleSheet(
            "color: #ccc; background: rgba(0,0,0,200); padding: 10px 20px;"
            " border-radius: 6px; font-size: 13px;"
        )
        self._status_label.adjustSize()

        # Control / info bar
        self._controls = self._build_controls()
        root.addWidget(self._controls)

        self._update_border()

        # Stats polling every 2 s
        self._stats_timer = QTimer(self)
        self._stats_timer.setInterval(2000)
        self._stats_timer.timeout.connect(self._poll_stats)
        self._stats_timer.start()

    # ── Control bar ──────────────────────────────────────────────────────────

    def _build_controls(self) -> QWidget:
        bar = QWidget()
        bar.setFixedHeight(26)
        bar.setStyleSheet("background: #1a1a1a;")
        h = QHBoxLayout(bar)
        h.setContentsMargins(4, 0, 4, 0)
        h.setSpacing(4)

        self._btn_play = QPushButton("⏸")
        self._btn_play.setFixedSize(22, 22)
        self._btn_play.setStyleSheet(_BTN_SS)
        self._btn_play.setToolTip("Play / Pause")
        self._btn_play.clicked.connect(self.toggle_pause)
        h.addWidget(self._btn_play)

        self._btn_mute = QPushButton("🔇")
        self._btn_mute.setFixedSize(22, 22)
        self._btn_mute.setStyleSheet(_BTN_SS)
        self._btn_mute.setToolTip("Mute / Unmute")
        self._btn_mute.clicked.connect(self.toggle_mute)
        h.addWidget(self._btn_mute)

        self._slider_vol = QSlider(Qt.Orientation.Horizontal)
        self._slider_vol.setRange(0, 100)
        self._slider_vol.setValue(80)
        self._slider_vol.setFixedWidth(60)
        self._slider_vol.setStyleSheet(_SLIDER_SS)
        self._slider_vol.valueChanged.connect(self._on_volume)
        h.addWidget(self._slider_vol)

        self._btn_quality = QPushButton("⚙")
        self._btn_quality.setFixedSize(26, 22)
        self._btn_quality.setStyleSheet(_BTN_SS)
        self._btn_quality.setToolTip("Video quality")
        self._btn_quality.clicked.connect(self._on_quality_click)
        h.addWidget(self._btn_quality)

        name = f"[{self.index + 1}] {self.channel.display_name()}"
        self._lbl_name = QLabel(name)
        self._lbl_name.setStyleSheet("color: #ddd; font-size: 11px;")
        self._lbl_name.setTextInteractionFlags(Qt.TextInteractionFlag.NoTextInteraction)
        h.addWidget(self._lbl_name, stretch=1)

        self._lbl_status = QLabel("")
        self._lbl_status.setStyleSheet("color: #888; font-size: 10px;")
        h.addWidget(self._lbl_status)

        self._lbl_drops = QLabel("")
        self._lbl_drops.setStyleSheet("color: red; font-size: 10px; font-weight: bold;")
        h.addWidget(self._lbl_drops)

        return bar

    # ── Playback controls ────────────────────────────────────────────────────

    def toggle_pause(self) -> None:
        if self._released:
            return
        try:
            if self._player.is_playing():
                self._player.pause()
                self._btn_play.setText("▶")
                self._user_paused = True
            else:
                self._player.play()
                self._btn_play.setText("⏸")
                self._user_paused = False
        except Exception:
            pass

    def toggle_mute(self) -> None:
        if self._released:
            return
        try:
            # Use button text as source of truth — VLC's audio_get_mute()
            # returns stale values right after playback starts.
            currently_muted = self._btn_mute.text() == "🔇"
            self._player.audio_set_mute(not currently_muted)
            self._btn_mute.setText("🔊" if currently_muted else "🔇")
        except Exception:
            pass

    def _on_volume(self, value: int) -> None:
        if self._released:
            return
        try:
            self._player.audio_set_volume(value)
        except Exception:
            pass

    # ── VLC integration ──────────────────────────────────────────────────────

    def embed_player(self) -> None:
        """Attach VLC to ``_video_frame``'s native surface (cached)."""
        if self._released:
            return
        try:
            handle = int(self._video_frame.winId())
        except RuntimeError:
            return
        if handle == self._embedded_handle:
            return
        self._embedded_handle = handle
        self._last_embed_time = time.monotonic()
        try:
            if sys.platform == "darwin":
                self._player.set_nsobject(handle)
            elif sys.platform.startswith("linux"):
                self._player.set_xwindow(handle)
            elif sys.platform == "win32":
                self._player.set_hwnd(handle)
        except Exception:
            pass

    def play_url(self, url: str, options: list[str] | None = None) -> None:
        if self._released:
            return
        try:
            self._player.stop()
        except Exception:
            pass
        try:
            media = self._vlc_instance.media_new(url)
            if options:
                for opt in options:
                    media.add_option(opt)
            self._media = media
            self._player.set_media(media)
            self.embed_player()
            # Start muted — capture_loop will unmute the active stream.
            self._player.audio_set_mute(True)
            if not self._user_paused:
                self._player.play()
                self._btn_play.setText("⏸")
        except Exception:
            pass
        self._frame_drops = 0
        self._last_lost_pics = 0
        self._lbl_drops.setText("")

    def stop(self) -> None:
        if self._released:
            return
        try:
            self._player.stop()
        except Exception:
            pass

    def get_state(self) -> vlc.State:
        if self._released:
            return vlc.State.Stopped
        try:
            return self._player.get_state()
        except Exception:
            return vlc.State.Error

    def release(self) -> None:
        """Release VLC resources.  Safe to call more than once."""
        if self._released:
            return
        self._released = True
        self._stats_timer.stop()
        try:
            self._player.stop()
        except Exception:
            pass
        try:
            self._player.release()
        except Exception:
            pass

    # ── Audio management ─────────────────────────────────────────────────────

    def set_audio_active(self, active: bool) -> None:
        if self._released:
            return
        try:
            self._player.audio_set_mute(not active)
        except Exception:
            pass
        self._btn_mute.setText("🔊" if active else "🔇")

    def reapply_audio(self) -> None:
        """Re-apply current mute state to VLC after audio subsystem inits."""
        if self._released:
            return
        muted = self._btn_mute.text() == "🔇"
        try:
            self._player.audio_set_mute(muted)
        except Exception:
            pass

    # ── Runtime upscale / enhance ────────────────────────────────────────────

    _UPSCALE_LABELS = {
        "off": None,
        "lanczos": "Lanczos",
        "sharpen_light": "Sharpen (Light)",
        "sharpen_medium": "Sharpen (Medium)",
        "sharpen_strong": "Sharpen (Strong)",
    }

    def set_upscale(self, preset: str) -> None:
        """Switch upscale preset (triggers a stream restart with status)."""
        if self._upscale_preset == preset:
            return
        self._upscale_preset = preset
        label = self._UPSCALE_LABELS.get(preset)
        if label:
            self.show_status(f"Enabling upscaler ({label})…", "info")
        else:
            self.show_status("Disabling upscaler…", "info")
        self.request_restart(force=True)

    # ── Status display ───────────────────────────────────────────────────────

    _STATUS_STYLE = "color: #ccc; background: rgba(0,0,0,180);"
    _STATUS_BASE = (
        " padding: 12px 24px; border-radius: 8px;"
        " font-size: 13px; font-weight: 500;"
    )

    def show_status(self, msg: str, level: str = "info") -> None:
        self._status_label.setStyleSheet(self._STATUS_STYLE + self._STATUS_BASE)
        self._status_label.setText(msg)
        self._status_label.adjustSize()
        self._status_label.setVisible(True)
        self._center_status()
        # Bottom bar gets the text directly.
        self._lbl_status.setText(msg)

    def hide_status(self) -> None:
        self._status_label.setVisible(False)
        self._lbl_status.setText("")

    show_placeholder = show_status  # backward-compat alias

    def _center_status(self) -> None:
        fw, fh = self._video_frame.width(), self._video_frame.height()
        sw, sh = self._status_label.width(), self._status_label.height()
        if fw > 0 and fh > 0:
            self._status_label.move(max(0, (fw - sw) // 2), max(0, (fh - sh) // 2))

    # ── Stats polling ────────────────────────────────────────────────────────

    def _poll_stats(self) -> None:
        if self._released:
            return
        if self._media is not None:
            try:
                stats = vlc.MediaStats()
                if self._media.get_stats(stats):
                    lost = stats.lost_pictures
                    if lost > self._last_lost_pics:
                        self._frame_drops += lost - self._last_lost_pics
                        self._last_lost_pics = lost
                    # Show drops only while they're happening (rolling 30s window).
                    elapsed = time.monotonic() - self._drop_window_start
                    if elapsed >= 30.0:
                        self._frame_drops = 0
                        self._drop_window_start = time.monotonic()
                    if self._frame_drops > 5:
                        self._lbl_drops.setText(f"{self._frame_drops} drops")
                    else:
                        self._lbl_drops.setText("")
            except Exception:
                pass
        try:
            state = self._player.get_state()
        except Exception:
            return
        if state == vlc.State.Buffering:
            self._lbl_status.setText("Buffering…")
        elif state == vlc.State.Playing:
            if self._lbl_status.text() in ("Buffering…", "Connecting…"):
                self._lbl_status.setText("")
            self._status_label.setVisible(False)

    # ── Index / label update ────────────────────────────────────────────────────

    def update_index(self, index: int) -> None:
        self.index = index
        self._lbl_name.setText(f"[{index + 1}] {self.channel.display_name()}")

    # ── Quality selection ────────────────────────────────────────────────────

    def prefetch_variants(self) -> None:
        """Start fetching quality variants in background (called on playback start)."""
        if self._variants_fetched or self._variants_loading:
            return
        self._variants_loading = True
        asyncio.ensure_future(self._prefetch_variants_bg())

    async def _prefetch_variants_bg(self) -> None:
        from models import parse_master_playlist
        try:
            self._variants = await asyncio.to_thread(
                parse_master_playlist, self.channel.url
            )
        except Exception:
            self._variants = []
        self._variants_fetched = True
        self._variants_loading = False
        self._btn_quality.setEnabled(True)
        self._btn_quality.setToolTip("Video quality")

    def _on_quality_click(self) -> None:
        if not self._variants_fetched:
            self._btn_quality.setEnabled(False)
            self._btn_quality.setToolTip("Loading quality options…")
            asyncio.ensure_future(self._prefetch_variants_bg())
            return
        self._show_quality_menu()

    def _show_quality_menu(self) -> None:
        menu = QMenu(self)
        act_auto = menu.addAction("Auto (adaptive)")
        act_auto.setCheckable(True)
        act_auto.setChecked(self._quality_url is None)
        act_auto.triggered.connect(lambda: self._set_quality(None))
        if self._variants:
            menu.addSeparator()
            for v in self._variants:
                act = menu.addAction(v.label)
                act.setCheckable(True)
                act.setChecked(self._quality_url == v.url)
                url = v.url
                act.triggered.connect(lambda checked, u=url: self._set_quality(u))
        elif self._variants_fetched:
            act = menu.addAction("No quality options found")
            act.setEnabled(False)
        menu.exec(self._btn_quality.mapToGlobal(
            self._btn_quality.rect().bottomLeft()
        ))

    def _set_quality(self, url: str | None) -> None:
        if url == self._quality_url:
            return
        self._quality_url = url
        self.request_restart(force=True)
        if url:
            label = next(
                (v.label for v in self._variants if v.url == url), "?"
            )
            self._btn_quality.setToolTip(f"Quality: {label}")
        else:
            self._btn_quality.setToolTip("Video quality")

    # ── Style / state ────────────────────────────────────────────────────────

    def set_active(self, active: bool) -> None:
        self._active = active
        self._update_border()
        if self._cfg.audio_enabled:
            self.set_audio_active(active)

    def request_restart(self, force: bool = False) -> None:
        if not force:
            now = time.monotonic()
            if now - self._last_restart_request_time < 90.0:
                return
            self._last_restart_request_time = now
        else:
            self._last_restart_request_time = time.monotonic()
            self._restart_force = True
        self._restart_requested = True

    def set_border_visible(self, visible: bool) -> None:
        """Hide border in single-stream view; show in grid."""
        if self._border_visible == visible:
            return
        self._border_visible = visible
        self._update_border()

    def set_controls_visible(self, visible: bool) -> None:
        """Hide the control bar for true full-screen single-stream view."""
        self._controls_pinned = visible
        self._controls.setVisible(visible)

    def _update_border(self) -> None:
        """Set border by adjusting the gap between _border_frame and _video_frame."""
        if not self._border_visible:
            self._border_frame.setStyleSheet("background: black;")
            self._border_layout.setContentsMargins(0, 0, 0, 0)
            return
        if self._active:
            px = self._cfg.active_border
            self._border_frame.setStyleSheet("background: lime;")
            self._border_layout.setContentsMargins(px, px, px, px)
        elif self._hovered:
            self._border_frame.setStyleSheet("background: rgb(80,80,80);")
            self._border_layout.setContentsMargins(2, 2, 2, 2)
        else:
            self._border_frame.setStyleSheet("background: #333;")
            self._border_layout.setContentsMargins(1, 1, 1, 1)

    _update_style = _update_border  # backward compat

    # ── Mouse events ─────────────────────────────────────────────────────────

    def mousePressEvent(self, ev) -> None:  # noqa: N802
        if ev.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit(self.index)
        super().mousePressEvent(ev)

    def mouseDoubleClickEvent(self, ev) -> None:  # noqa: N802
        if ev.button() == Qt.MouseButton.LeftButton:
            self.double_clicked.emit(self.index)
        super().mouseDoubleClickEvent(ev)

    def contextMenuEvent(self, ev) -> None:  # noqa: N802
        pos = ev.globalPosition().toPoint() if hasattr(ev, 'globalPosition') else ev.globalPos()
        self.context_menu_requested.emit(self.index, pos)

    def enterEvent(self, ev) -> None:  # noqa: N802
        self._hovered = True
        self._update_border()
        super().enterEvent(ev)

    def leaveEvent(self, ev) -> None:  # noqa: N802
        self._hovered = False
        self._update_border()
        super().leaveEvent(ev)

    def resizeEvent(self, ev) -> None:  # noqa: N802
        super().resizeEvent(ev)
        self._center_status()
