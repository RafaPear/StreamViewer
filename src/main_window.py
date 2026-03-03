"""main_window.py – MainWindow: the application's primary window."""

import asyncio
import math

import vlc
from PyQt6.QtCore import Qt, QTimer, QEvent
from PyQt6.QtGui import QAction
from PyQt6.QtWidgets import (
    QApplication,
    QDialog,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from capture import capture_loop
from config import Config, save_config
from dialogs import (
    AddStreamDialog,
    ChannelPickerDialog,
    FavouritesDialog,
    LoadGridPresetDialog,
    LoadPlaylistDialog,
    PlaylistManagerDialog,
    SaveGridPresetDialog,
    SettingsDialog,
)
from models import Channel, parse_m3u
from stream_widget import StreamWidget


class MainWindow(QMainWindow):
    """
    Main application window with single-stream and paginated grid views.

    Keyboard:  ← / → switch  |  G grid  |  A add  |  L playlist
               PgUp / PgDn page  |  Del remove  |  Q quit
    Mouse:     click = select  |  double-click = single-stream mode
    """

    def __init__(
        self,
        channels: list[Channel],
        cfg: Config,
        vlc_instance: vlc.Instance,
    ) -> None:
        super().__init__()
        self._cfg = cfg
        self._vlc_instance = vlc_instance
        self._channels: list[Channel] = list(channels)
        self._widgets: list[StreamWidget] = []
        self._tasks: dict[StreamWidget, asyncio.Task] = {}
        self._loop: asyncio.AbstractEventLoop | None = None
        self._active_index = 0
        self._grid_mode = cfg.last_grid_mode
        self._current_page = 0

        self.setWindowTitle("StreamsClient")

        # Restore window geometry.
        if cfg.window_x >= 0 and cfg.window_y >= 0:
            self.setGeometry(cfg.window_x, cfg.window_y, cfg.window_w, cfg.window_h)
        else:
            self.resize(cfg.window_w, cfg.window_h)

        # ── Grid page ────────────────────────────────────────────────────────
        grid_container = QWidget()
        grid_vbox = QVBoxLayout(grid_container)
        grid_vbox.setContentsMargins(2, 2, 2, 2)
        grid_vbox.setSpacing(2)

        self._grid_page = QWidget()
        self._grid_layout = QGridLayout(self._grid_page)
        self._grid_layout.setSpacing(2)
        self._grid_layout.setContentsMargins(0, 0, 0, 0)
        grid_vbox.addWidget(self._grid_page, stretch=1)

        # Empty-state welcome overlay (hidden when streams are loaded).
        self._empty_widget = self._build_empty_state()
        grid_vbox.addWidget(self._empty_widget)

        # Bottom toolbar: quick-action buttons + page navigation
        self._page_bar = QWidget()
        self._page_bar.setFixedHeight(34)
        self._page_bar.setStyleSheet("background: #1a1a1a;")
        pb = QHBoxLayout(self._page_bar)
        pb.setContentsMargins(8, 0, 8, 0)
        pb.setSpacing(6)

        _tb_btn = (
            "QPushButton { color: #ccc; background: #2a2a2a; border: 1px solid #444;"
            " border-radius: 4px; padding: 2px 10px; font-size: 11px; }"
            "QPushButton:hover { background: #3a3a3a; border-color: #0a84ff; color: white; }"
        )

        self._tb_add = QPushButton("Add Stream")
        self._tb_add.setStyleSheet(_tb_btn)
        self._tb_add.clicked.connect(self._action_add_stream)
        pb.addWidget(self._tb_add)

        self._tb_playlist = QPushButton("Load Playlist")
        self._tb_playlist.setStyleSheet(_tb_btn)
        self._tb_playlist.clicked.connect(self._action_load_playlist)
        pb.addWidget(self._tb_playlist)

        self._tb_favs = QPushButton("Favourites")
        self._tb_favs.setStyleSheet(_tb_btn)
        self._tb_favs.clicked.connect(self._action_manage_favourites)
        pb.addWidget(self._tb_favs)

        self._tb_preset = QPushButton("Presets")
        self._tb_preset.setStyleSheet(_tb_btn)
        self._tb_preset.clicked.connect(self._action_load_grid_preset)
        pb.addWidget(self._tb_preset)

        pb.addStretch()

        # Pagination controls (centered)
        self._btn_prev = QPushButton("< Prev")
        self._btn_prev.setStyleSheet(_tb_btn)
        self._btn_prev.clicked.connect(self._page_prev)
        pb.addWidget(self._btn_prev)

        self._lbl_page = QLabel("Page 1 / 1")
        self._lbl_page.setStyleSheet("color: #ccc; font-size: 11px;")
        pb.addWidget(self._lbl_page)

        self._btn_next = QPushButton("Next >")
        self._btn_next.setStyleSheet(_tb_btn)
        self._btn_next.clicked.connect(self._page_next)
        pb.addWidget(self._btn_next)

        pb.addStretch()

        # Right side: settings + grid toggle
        self._tb_grid = QPushButton("Grid")
        self._tb_grid.setStyleSheet(_tb_btn)
        self._tb_grid.setCheckable(True)
        self._tb_grid.setChecked(self._grid_mode)
        self._tb_grid.clicked.connect(self._toggle_grid)
        pb.addWidget(self._tb_grid)

        self._tb_settings = QPushButton("Settings")
        self._tb_settings.setStyleSheet(_tb_btn)
        self._tb_settings.clicked.connect(self._action_settings)
        pb.addWidget(self._tb_settings)

        grid_vbox.addWidget(self._page_bar)

        # ── Single-stream page ───────────────────────────────────────────────
        self._single_page = QWidget()
        self._single_layout = QVBoxLayout(self._single_page)
        self._single_layout.setContentsMargins(0, 0, 0, 0)

        # Stack: index 0 = single, index 1 = grid.
        self._stack = QStackedWidget()
        self._stack.addWidget(self._single_page)
        self._stack.addWidget(grid_container)
        self.setCentralWidget(self._stack)
        self.statusBar().setStyleSheet("color: #888; font-size: 11px;")

        # Auto-hide cursor after 2 s of inactivity in single-stream view.
        self._cursor_hidden = False
        self._cursor_timer = QTimer(self)
        self._cursor_timer.setSingleShot(True)
        self._cursor_timer.setInterval(2000)
        self._cursor_timer.timeout.connect(self._hide_overlay)

        # Catch mouse moves globally (child widgets consume mouseMoveEvent).
        QApplication.instance().installEventFilter(self)

        for i, ch in enumerate(self._channels):
            self._create_widget(ch, i)

        self._rebuild_grid()

        if self._widgets:
            # Restore active index safely.
            active = min(cfg.last_active_index, max(0, len(self._widgets) - 1))
            if self._grid_mode:
                self._set_active(active)
                self._stack.setCurrentIndex(1)
            else:
                self._set_active(active)
                self._show_single(active)
        else:
            # Empty start — show grid view.
            self._stack.setCurrentIndex(1)

        self._build_menu()

    # ── Empty-state welcome ───────────────────────────────────────────────────

    def _build_empty_state(self) -> QWidget:
        """Welcome screen shown when no streams are loaded."""
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(40, 40, 40, 40)
        v.addStretch(2)

        title = QLabel("StreamsClient")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title.setStyleSheet("color: #ddd; font-size: 28px; font-weight: bold;")
        v.addWidget(title)

        subtitle = QLabel("No streams loaded — get started:")
        subtitle.setAlignment(Qt.AlignmentFlag.AlignCenter)
        subtitle.setStyleSheet("color: #888; font-size: 14px; margin-bottom: 20px;")
        v.addWidget(subtitle)

        btn_style = (
            "QPushButton { color: white; background: #333; border: 1px solid #555;"
            " border-radius: 6px; padding: 10px 24px; font-size: 14px; }"
            "QPushButton:hover { background: #444; border-color: #0a84ff; }"
        )

        row = QHBoxLayout()
        row.addStretch()

        btn_add = QPushButton("Add Stream")
        btn_add.setStyleSheet(btn_style)
        btn_add.clicked.connect(self._action_add_stream)
        row.addWidget(btn_add)

        btn_playlist = QPushButton("Load Playlist")
        btn_playlist.setStyleSheet(btn_style)
        btn_playlist.clicked.connect(self._action_load_playlist)
        row.addWidget(btn_playlist)

        btn_favs = QPushButton("Favourites")
        btn_favs.setStyleSheet(btn_style)
        btn_favs.clicked.connect(self._action_manage_favourites)
        row.addWidget(btn_favs)

        if self._cfg.grid_presets:
            btn_preset = QPushButton("Load Preset")
            btn_preset.setStyleSheet(btn_style)
            btn_preset.clicked.connect(self._action_load_grid_preset)
            row.addWidget(btn_preset)

        row.addStretch()
        v.addLayout(row)

        v.addSpacing(20)
        hint = QLabel(
            "Tip: Press  A  to add a stream  ·  L  to load a playlist  ·  Ctrl+Q  to quit"
        )
        hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        hint.setStyleSheet("color: #666; font-size: 11px;")
        v.addWidget(hint)

        v.addStretch(3)
        return w

    def _update_empty_state(self) -> None:
        """Show welcome screen when no streams, hide otherwise."""
        has_streams = bool(self._widgets)
        self._empty_widget.setVisible(not has_streams)
        self._grid_page.setVisible(has_streams)
        self._page_bar.setVisible(has_streams)

    # ── Cursor / controls auto-hide ──────────────────────────────────────────

    def eventFilter(self, obj, event) -> bool:  # noqa: N802
        if event.type() == QEvent.Type.MouseMove and not self._grid_mode:
            # Skip overlay toggling while a popup menu is active to avoid
            # showing/hiding the controls bar mid-QMenu.exec() (crash).
            if QApplication.activePopupWidget() is None:
                self._show_overlay()
                self._cursor_timer.start()
        return False

    def _hide_overlay(self) -> None:
        """Hide cursor and control bar after idle timeout in single-stream view."""
        if self._grid_mode:
            return
        # Don't hide while a popup menu (e.g. quality menu) is open.
        if QApplication.activePopupWidget() is not None:
            self._cursor_timer.start()  # retry after another interval
            return
        if not self._cursor_hidden:
            self._cursor_hidden = True
            QApplication.setOverrideCursor(Qt.CursorShape.BlankCursor)
        if 0 <= self._active_index < len(self._widgets):
            self._widgets[self._active_index]._controls.hide()

    def _show_overlay(self) -> None:
        """Restore cursor and control bar on mouse activity."""
        if self._cursor_hidden:
            self._cursor_hidden = False
            QApplication.restoreOverrideCursor()
        if not self._grid_mode and 0 <= self._active_index < len(self._widgets):
            self._widgets[self._active_index]._controls.show()

    # ── Pagination helpers ────────────────────────────────────────────────────

    @property
    def _effective_grid(self) -> tuple[int, int]:
        """Return (rows, cols) respecting dynamic grid mode."""
        if self._cfg.dynamic_grid:
            n = len(self._widgets)
            if n <= 0:
                return 1, 1
            cols = math.ceil(math.sqrt(n))
            rows = math.ceil(n / cols)
            return rows, cols
        return self._cfg.grid_rows, self._cfg.grid_cols

    @property
    def _streams_per_page(self) -> int:
        rows, cols = self._effective_grid
        return max(1, rows * cols)

    @property
    def _page_count(self) -> int:
        return max(1, math.ceil(len(self._widgets) / self._streams_per_page))

    def _page_prev(self) -> None:
        if self._current_page > 0:
            self._current_page -= 1
            self._rebuild_grid()

    def _page_next(self) -> None:
        if self._current_page < self._page_count - 1:
            self._current_page += 1
            self._rebuild_grid()

    # ── Menu bar ──────────────────────────────────────────────────────────────

    def _build_menu(self) -> None:
        mb = self.menuBar()

        # ── File menu ────────────────────────────────────────────────────────
        file_menu = mb.addMenu("&File")

        act_add = QAction("&Add Stream...", self)
        act_add.setShortcut("A")
        act_add.triggered.connect(self._action_add_stream)
        file_menu.addAction(act_add)

        act_load = QAction("&Load Playlist...", self)
        act_load.setShortcut("L")
        act_load.triggered.connect(self._action_load_playlist)
        file_menu.addAction(act_load)

        file_menu.addSeparator()

        act_fav_add = QAction("Add to &Favourites", self)
        act_fav_add.setShortcut("Ctrl+D")
        act_fav_add.triggered.connect(self._action_add_favourite)
        file_menu.addAction(act_fav_add)

        act_fav_manage = QAction("Manage &Favourites...", self)
        act_fav_manage.triggered.connect(self._action_manage_favourites)
        file_menu.addAction(act_fav_manage)

        act_pl_manage = QAction("Saved &Playlists...", self)
        act_pl_manage.triggered.connect(self._action_manage_playlists)
        file_menu.addAction(act_pl_manage)

        file_menu.addSeparator()

        act_settings = QAction("&Preferences...", self)
        act_settings.setShortcut(",")
        act_settings.triggered.connect(self._action_settings)
        file_menu.addAction(act_settings)

        file_menu.addSeparator()

        act_quit = QAction("&Quit", self)
        act_quit.setShortcut("Ctrl+Q")
        act_quit.triggered.connect(self.close)
        file_menu.addAction(act_quit)

        # ── Streams menu ─────────────────────────────────────────────────────
        streams = mb.addMenu("&Streams")

        act_remove = QAction("&Remove Active Stream", self)
        act_remove.setShortcut("Del")
        act_remove.triggered.connect(self.remove_active)
        streams.addAction(act_remove)

        streams.addSeparator()

        act_up = QAction("Move Stream &Up", self)
        act_up.setShortcut("Ctrl+Up")
        act_up.triggered.connect(lambda: self._move_stream(-1))
        streams.addAction(act_up)

        act_down = QAction("Move Stream &Down", self)
        act_down.setShortcut("Ctrl+Down")
        act_down.triggered.connect(lambda: self._move_stream(1))
        streams.addAction(act_down)

        # ── View menu ────────────────────────────────────────────────────────
        view = mb.addMenu("&View")

        self._act_grid = QAction("&Grid Mode", self)
        self._act_grid.setShortcut("G")
        self._act_grid.setCheckable(True)
        self._act_grid.setChecked(self._grid_mode)
        self._act_grid.triggered.connect(self._toggle_grid)
        view.addAction(self._act_grid)

        view.addSeparator()

        act_prev = QAction("&Previous Stream", self)
        act_prev.setShortcut("Left")
        act_prev.triggered.connect(
            lambda: self._switch_stream((self._active_index - 1) % len(self._widgets))
        )
        view.addAction(act_prev)

        act_next = QAction("&Next Stream", self)
        act_next.setShortcut("Right")
        act_next.triggered.connect(
            lambda: self._switch_stream((self._active_index + 1) % len(self._widgets))
        )
        view.addAction(act_next)

        view.addSeparator()

        act_pgup = QAction("Previous &Page", self)
        act_pgup.setShortcut("PgUp")
        act_pgup.triggered.connect(self._page_prev)
        view.addAction(act_pgup)

        act_pgdn = QAction("Next Pa&ge", self)
        act_pgdn.setShortcut("PgDown")
        act_pgdn.triggered.connect(self._page_next)
        view.addAction(act_pgdn)

        view.addSeparator()

        act_save_preset = QAction("Save Grid &Preset...", self)
        act_save_preset.triggered.connect(self._action_save_grid_preset)
        view.addAction(act_save_preset)

        act_load_preset = QAction("&Load Grid Preset...", self)
        act_load_preset.triggered.connect(self._action_load_grid_preset)
        view.addAction(act_load_preset)

        view.addSeparator()

        act_fs = QAction("&Fullscreen", self)
        act_fs.setShortcut("F")
        act_fs.triggered.connect(
            lambda: self.showNormal() if self.isFullScreen() else self.showFullScreen()
        )
        view.addAction(act_fs)

    # ── Event loop wiring ─────────────────────────────────────────────────────

    def set_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop
        for w in self._widgets:
            self._tasks[w] = asyncio.ensure_future(capture_loop(w, loop, self._cfg))

    # ── Runtime stream management ─────────────────────────────────────────────

    def add_channel(self, channel: Channel) -> None:
        self.add_channels([channel])

    def add_channels(self, channels: list[Channel]) -> None:
        """Add multiple channels with a single grid rebuild."""
        if not channels:
            return
        n = len(channels)
        if n > 1:
            self.statusBar().showMessage(f"Loading {n} streams…")
        start_index = len(self._channels)
        for ch in channels:
            index = len(self._channels)
            self._channels.append(ch)
            w = self._create_widget(ch, index)
            if self._loop:
                self._tasks[w] = asyncio.ensure_future(
                    capture_loop(w, self._loop, self._cfg)
                )
        self._rebuild_grid()
        if len(self._widgets) > 1 and not self._grid_mode:
            # Auto-enter grid mode when more than one stream.
            self._grid_mode = True
            self._act_grid.setChecked(True)
            self._tb_grid.setChecked(True)
            self._stack.setCurrentIndex(1)
        if self._grid_mode:
            self._set_active(start_index)
        else:
            self._switch_stream(start_index)
        if n > 1:
            self.statusBar().showMessage(f"Loaded {n} streams", 3000)

    def remove_active(self) -> None:
        if not self._widgets:
            return
        self._remove_stream_at(self._active_index)

    def _remove_stream_at(self, idx: int) -> None:
        """Remove the stream at *idx* (no minimum-count guard)."""
        widget = self._widgets.pop(idx)
        task = self._tasks.pop(widget, None)
        del self._channels[idx]

        # Non-blocking: mark released and hide immediately.
        widget._released = True
        widget._stats_timer.stop()
        widget.setParent(None)  # type: ignore[arg-type]
        widget.hide()

        # Defer expensive VLC stop/release to a background thread.
        if self._loop and task:
            asyncio.ensure_future(self._release_widget_async(widget, task))
        else:
            if task:
                task.cancel()
            try:
                widget.release()
            except Exception:
                pass
            widget.deleteLater()

        for i, w in enumerate(self._widgets):
            w.update_index(i)
        new_idx = min(idx, max(len(self._widgets) - 1, 0))
        self._active_index = -1
        # Clamp page
        self._current_page = min(self._current_page, max(self._page_count - 1, 0))
        self._rebuild_grid()
        if self._widgets:
            if len(self._widgets) == 1 and self._grid_mode:
                # Auto-exit grid mode when only one stream remains.
                self._grid_mode = False
                self._act_grid.setChecked(False)
                self._tb_grid.setChecked(False)
            self._set_active(new_idx)
            if not self._grid_mode:
                self._show_single(new_idx)
        else:
            # No streams left — restore cursor/overlay and show empty state.
            self._cursor_timer.stop()
            self._show_overlay()
            self.statusBar().show()
        self.setFocus()  # re-grab keyboard focus after grid rebuild

    def _clear_all_streams(self) -> None:
        """Remove every stream (for preset loading)."""
        while self._widgets:
            self._remove_stream_at(0)

    async def _release_widget_async(
        self, widget: StreamWidget, task: asyncio.Task
    ) -> None:
        """Cancel the capture task, then release VLC off the UI thread."""
        task.cancel()
        try:
            await asyncio.gather(task, return_exceptions=True)
        except Exception:
            pass

        def _vlc_release() -> None:
            try:
                widget._player.stop()
            except Exception:
                pass
            try:
                widget._player.release()
            except Exception:
                pass

        try:
            await asyncio.to_thread(_vlc_release)
        except Exception:
            pass
        widget.deleteLater()

    # ── Keyboard ──────────────────────────────────────────────────────────────

    def keyPressEvent(self, event) -> None:  # noqa: N802
        key = event.key()
        ctrl = bool(event.modifiers() & Qt.KeyboardModifier.ControlModifier)
        if ctrl and key == Qt.Key.Key_Up:
            self._move_stream(-1)
        elif ctrl and key == Qt.Key.Key_Down:
            self._move_stream(1)
        elif key == Qt.Key.Key_Right:
            self._switch_stream((self._active_index + 1) % len(self._widgets))
        elif key == Qt.Key.Key_Left:
            self._switch_stream((self._active_index - 1) % len(self._widgets))
        elif key == Qt.Key.Key_PageUp:
            self._page_prev()
        elif key == Qt.Key.Key_PageDown:
            self._page_next()
        elif key == Qt.Key.Key_G:
            self._toggle_grid()
        elif key == Qt.Key.Key_A:
            self._action_add_stream()
        elif key == Qt.Key.Key_L:
            self._action_load_playlist()
        elif key in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace):
            self.remove_active()
        elif key == Qt.Key.Key_Q:
            self.close()
        else:
            super().keyPressEvent(event)

    # ── Close / save session ──────────────────────────────────────────────────

    def closeEvent(self, event) -> None:  # noqa: N802
        # Mark all widgets as released so capture_loop coroutines exit their
        # polling loops promptly; then cancel the tasks.  Do NOT call
        # w.release() here – the capture_loop may still be mid-iteration.
        # The async _run() finally block in streams_client.py handles ordered
        # shutdown: cancel tasks → gather → release players → release instance.
        for w in self._widgets:
            w._released = True
        for t in self._tasks.values():
            t.cancel()

        # Persist runtime state.
        if self._cfg.remember_session:
            self._cfg.last_channels = [ch.to_dict() for ch in self._channels]
            self._cfg.last_active_index = self._active_index
            self._cfg.last_grid_mode = self._grid_mode

        geo = self.geometry()
        self._cfg.window_x = geo.x()
        self._cfg.window_y = geo.y()
        self._cfg.window_w = geo.width()
        self._cfg.window_h = geo.height()

        save_config(self._cfg)
        event.accept()

    # ── Dialog actions ────────────────────────────────────────────────────────

    def _action_add_stream(self) -> None:
        dlg = AddStreamDialog(self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self.add_channel(dlg.result_channel())

    def _action_load_playlist(self) -> None:
        dlg = LoadPlaylistDialog(self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        if self._loop:
            asyncio.ensure_future(self._load_playlist_async(dlg.source()))

    async def _load_playlist_async(self, source: str) -> None:
        """Fetch + parse a playlist in a background thread, then show the picker."""
        self.statusBar().showMessage("Loading playlist…")
        try:
            all_channels = await asyncio.to_thread(parse_m3u, source)
        except Exception as exc:
            self.statusBar().clearMessage()
            # Schedule dialog outside async task to avoid nested-event-loop crash.
            QTimer.singleShot(0, lambda: QMessageBox.critical(self, "Playlist Error", str(exc)))
            return
        self.statusBar().clearMessage()
        if not all_channels:
            QTimer.singleShot(0, lambda: QMessageBox.information(self, "Empty Playlist", "No streams found."))
            return
        # Show the picker outside the async task context.
        QTimer.singleShot(0, lambda: self._show_channel_picker(all_channels))

    def _show_channel_picker(self, channels: list) -> None:
        """Open the channel picker dialog (must be called outside async tasks)."""
        picker = ChannelPickerDialog(channels, self)
        if picker.exec() == QDialog.DialogCode.Accepted:
            selected = picker.selected_channels()
            if selected:
                self.add_channels(selected)

    def _action_settings(self) -> None:
        dlg = SettingsDialog(self._cfg, self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            for w in self._widgets:
                w._cfg = self._cfg
                w._update_border()
                w.request_restart()
            if self._grid_mode:
                self._rebuild_grid()
                self._stack.setCurrentIndex(1)
            save_config(self._cfg)

    # ── Stream reorder ────────────────────────────────────────────────────────

    def _move_stream(self, direction: int) -> None:
        """Move the active stream up (-1) or down (+1) in the list."""
        idx = self._active_index
        new_idx = idx + direction
        if new_idx < 0 or new_idx >= len(self._widgets):
            return
        # Swap in both lists
        self._channels[idx], self._channels[new_idx] = (
            self._channels[new_idx], self._channels[idx]
        )
        self._widgets[idx], self._widgets[new_idx] = (
            self._widgets[new_idx], self._widgets[idx]
        )
        self._widgets[idx].update_index(idx)
        self._widgets[new_idx].update_index(new_idx)
        self._active_index = new_idx
        self._rebuild_grid()
        if not self._grid_mode:
            self._show_single(new_idx)

    # ── Favourites / playlists ────────────────────────────────────────────────

    def _action_add_favourite(self) -> None:
        if not self._widgets:
            return
        ch = self._channels[self._active_index]
        for f in self._cfg.favourites:
            if f.get("url") == ch.url:
                return  # already saved
        self._cfg.favourites.append(ch.to_dict())
        save_config(self._cfg)

    def _action_manage_favourites(self) -> None:
        dlg = FavouritesDialog(self._cfg.favourites, self)
        result = dlg.exec()
        self._cfg.favourites = dlg.all_favourites()
        save_config(self._cfg)
        if result == QDialog.DialogCode.Accepted:
            selected = dlg.checked_channels()
            if selected:
                self.add_channels(selected)

    def _action_manage_playlists(self) -> None:
        dlg = PlaylistManagerDialog(self._cfg.saved_playlists, self)
        result = dlg.exec()
        self._cfg.saved_playlists = dlg.all_playlists()
        save_config(self._cfg)
        if result == QDialog.DialogCode.Accepted:
            pl = dlg.selected_playlist()
            if pl and self._loop:
                asyncio.ensure_future(self._load_playlist_async(pl["url"]))

    # ── Grid presets ─────────────────────────────────────────────────────────

    def _action_save_grid_preset(self) -> None:
        if not self._channels:
            QMessageBox.information(self, "No Streams", "Load some streams first.")
            return
        dlg = SaveGridPresetDialog(self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        preset: dict = {
            "name": dlg.preset_name(),
            "rows": self._cfg.grid_rows,
            "cols": self._cfg.grid_cols,
            "dynamic": self._cfg.dynamic_grid,
            "channels": [ch.to_dict() for ch in self._channels],
        }
        self._cfg.grid_presets.append(preset)
        save_config(self._cfg)
        self.statusBar().showMessage(f"Saved preset \"{preset['name']}\"", 3000)

    def _action_load_grid_preset(self) -> None:
        if not self._cfg.grid_presets:
            QMessageBox.information(self, "No Presets", "No saved grid presets.")
            return
        dlg = LoadGridPresetDialog(self._cfg.grid_presets, self)
        result = dlg.exec()
        self._cfg.grid_presets = dlg.all_presets()
        save_config(self._cfg)
        if result != QDialog.DialogCode.Accepted:
            return
        preset = dlg.selected_preset()
        if not preset:
            return
        # Apply grid settings from preset.
        self._cfg.grid_rows = preset.get("rows", 2)
        self._cfg.grid_cols = preset.get("cols", 2)
        self._cfg.dynamic_grid = preset.get("dynamic", False)
        save_config(self._cfg)
        # Remove existing streams.
        self._clear_all_streams()
        # Load channels from preset.
        channels = [Channel.from_dict(d) for d in preset.get("channels", [])]
        if channels:
            self.add_channels(channels)
        self.statusBar().showMessage(f"Loaded preset \"{preset['name']}\"", 3000)

    # ── Internal helpers ──────────────────────────────────────────────────────

    def _create_widget(self, channel: Channel, index: int) -> StreamWidget:
        w = StreamWidget(channel, index, self._cfg, self._vlc_instance, self._grid_page)
        w.clicked.connect(self._set_active)
        w.double_clicked.connect(self._on_double_click)
        self._widgets.append(w)
        return w

    def _on_double_click(self, index: int) -> None:
        self._set_active(index)
        if self._grid_mode:
            self._grid_mode = False
            self._act_grid.setChecked(False)
        self._show_single(index)

    def _rebuild_grid(self) -> None:
        # Remove all widgets from grid layout
        while self._grid_layout.count():
            self._grid_layout.takeAt(0)
        # Return any widget from single layout
        while self._single_layout.count():
            item = self._single_layout.takeAt(0)
            if item and item.widget():
                item.widget().setParent(self._grid_page)  # type: ignore[arg-type]

        # Hide all widgets; only invalidate embed handle if parent changed.
        for w in self._widgets:
            if w.parent() is not self._grid_page:
                w.setParent(self._grid_page)  # type: ignore[arg-type]
                w._embedded_handle = 0
            w.hide()

        # Show only the current page
        cols = max(1, self._effective_grid[1])
        per_page = self._streams_per_page
        start = self._current_page * per_page
        end = min(start + per_page, len(self._widgets))

        for i, w in enumerate(self._widgets[start:end]):
            row, col = divmod(i, cols)
            self._grid_layout.addWidget(w, row, col)
            w.set_border_visible(True)
            w.set_controls_visible(True)  # always show controls in grid
            w.show()
            w.embed_player()

        # Update page bar
        pc = self._page_count
        self._lbl_page.setText(f"Page {self._current_page + 1} / {pc}")
        self._btn_prev.setEnabled(self._current_page > 0)
        self._btn_next.setEnabled(self._current_page < pc - 1)
        if pc <= 1:
            self._page_bar.hide()

        self._update_empty_state()

    def _set_active(self, index: int) -> None:
        if 0 <= self._active_index < len(self._widgets):
            self._widgets[self._active_index].set_active(False)
        self._active_index = index
        if 0 <= index < len(self._widgets):
            self._widgets[index].set_active(True)

    def _switch_stream(self, index: int) -> None:
        self._set_active(index)
        if self._grid_mode:
            # Auto-switch page if the new active stream is on a different page
            page = index // self._streams_per_page
            if page != self._current_page:
                self._current_page = page
                self._rebuild_grid()
        else:
            self._show_single(index)

    def _show_single(self, index: int) -> None:
        self._rebuild_grid()
        target = self._widgets[index]
        self._grid_layout.removeWidget(target)
        target.setParent(self._single_page)  # type: ignore[arg-type]
        target._embedded_handle = 0  # invalidate so embed_player re-attaches
        self._single_layout.addWidget(target)
        target.show()
        target.embed_player()
        target.set_border_visible(False)  # no border in single-stream view
        target.set_controls_visible(False)  # hide controls; shown on hover
        self.statusBar().hide()
        self._cursor_timer.start()  # start hide-cursor countdown
        self._stack.setCurrentIndex(0)

    def _toggle_grid(self) -> None:
        self._grid_mode = not self._grid_mode
        self._act_grid.setChecked(self._grid_mode)
        self._tb_grid.setChecked(self._grid_mode)
        if self._grid_mode:
            self._cursor_timer.stop()
            self._show_overlay()
            self.statusBar().show()
            self._rebuild_grid()
            self._stack.setCurrentIndex(1)
        else:
            self._show_single(self._active_index)
