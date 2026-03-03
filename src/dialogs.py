"""dialogs.py – All application dialogs."""

from PyQt6.QtCore import Qt, QTimer, QAbstractListModel, QModelIndex, QSortFilterProxyModel
from PyQt6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from config import Config
from models import Channel


# ── Add Stream ────────────────────────────────────────────────────────────────

class AddStreamDialog(QDialog):
    """Prompt for a stream URL and optional display name."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Add Stream")
        self.setMinimumWidth(460)
        layout = QVBoxLayout(self)

        layout.addWidget(QLabel("Stream URL (RTSP, HTTP, HLS, or local file):"))
        self._url = QLineEdit()
        self._url.setPlaceholderText("rtsp://… or http://… or /path/to/file.mp4")
        layout.addWidget(self._url)

        layout.addWidget(QLabel("Display name (optional):"))
        self._name = QLineEdit()
        self._name.setPlaceholderText("My Camera 1")
        layout.addWidget(self._name)

        btns = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        btns.accepted.connect(self._accept)
        btns.rejected.connect(self.reject)
        layout.addWidget(btns)
        self._url.returnPressed.connect(self._accept)

    def _accept(self) -> None:
        if self._url.text().strip():
            self.accept()

    def result_channel(self) -> Channel:
        return Channel(url=self._url.text().strip(), name=self._name.text().strip())


# ── Add Source (Stream URL + Playlist) ────────────────────────────────────────


class AddSourceDialog(QDialog):
    """Tabbed dialog: add a stream URL *or* load a playlist."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Add Source")
        self.setMinimumWidth(500)
        layout = QVBoxLayout(self)

        self._tabs = QTabWidget()
        self._tabs.addTab(self._stream_tab(), "Stream URL")
        self._tabs.addTab(self._playlist_tab(), "Playlist")
        layout.addWidget(self._tabs)

        btns = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        btns.accepted.connect(self._accept)
        btns.rejected.connect(self.reject)
        layout.addWidget(btns)

    def _stream_tab(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.addWidget(QLabel("Stream URL (RTSP, HTTP, HLS, or local file):"))
        self._url = QLineEdit()
        self._url.setPlaceholderText("rtsp://… or http://… or /path/to/file.mp4")
        lay.addWidget(self._url)
        lay.addWidget(QLabel("Display name (optional):"))
        self._name = QLineEdit()
        self._name.setPlaceholderText("My Camera 1")
        lay.addWidget(self._name)
        self._url.returnPressed.connect(self._accept)
        self._name.returnPressed.connect(self._accept)
        lay.addStretch()
        return w

    def _playlist_tab(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.addWidget(QLabel("Playlist file path or URL (.m3u / .m3u8):"))
        row = QHBoxLayout()
        self._src = QLineEdit()
        self._src.setPlaceholderText("/path/to/playlist.m3u  or  http://host/pl.m3u8")
        row.addWidget(self._src)
        browse = QPushButton("Browse…")
        browse.clicked.connect(self._browse)
        row.addWidget(browse)
        lay.addLayout(row)
        self._src.returnPressed.connect(self._accept)
        lay.addStretch()
        return w

    def _browse(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Open Playlist", "", "Playlists (*.m3u *.m3u8);;All files (*)"
        )
        if path:
            self._src.setText(path)

    def _accept(self) -> None:
        if self._tabs.currentIndex() == 0:
            if self._url.text().strip():
                self.accept()
        else:
            if self._src.text().strip():
                self.accept()

    def active_tab(self) -> int:
        return self._tabs.currentIndex()

    def result_channel(self) -> Channel:
        return Channel(url=self._url.text().strip(), name=self._name.text().strip())

    def playlist_source(self) -> str:
        return self._src.text().strip()


# ── Channel Picker ────────────────────────────────────────────────────────────

class _ChannelListModel(QAbstractListModel):
    """Lightweight model that holds channels + check state without creating widgets."""

    def __init__(self, channels: list[Channel], parent=None) -> None:
        super().__init__(parent)
        self._channels = channels
        self._checked: list[bool] = [True] * len(channels)

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:  # noqa: N802
        return len(self._channels)

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None
        row = index.row()
        if role == Qt.ItemDataRole.DisplayRole:
            ch = self._channels[row]
            return f"{ch.display_name()}  —  {ch.url}"
        if role == Qt.ItemDataRole.CheckStateRole:
            return Qt.CheckState.Checked if self._checked[row] else Qt.CheckState.Unchecked
        if role == Qt.ItemDataRole.UserRole:
            return self._channels[row]
        return None

    def setData(self, index: QModelIndex, value, role: int = Qt.ItemDataRole.EditRole) -> bool:  # noqa: N802
        if role == Qt.ItemDataRole.CheckStateRole and index.isValid():
            self._checked[index.row()] = value == Qt.CheckState.Checked.value
            self.dataChanged.emit(index, index, [role])
            return True
        return False

    def flags(self, index: QModelIndex) -> Qt.ItemFlag:
        base = super().flags(index)
        if index.isValid():
            return base | Qt.ItemFlag.ItemIsUserCheckable
        return base

    def set_all_checked(self, checked: bool) -> None:
        if not self._channels:
            return
        self._checked = [checked] * len(self._channels)
        self.dataChanged.emit(
            self.index(0), self.index(len(self._channels) - 1),
            [Qt.ItemDataRole.CheckStateRole],
        )

    def selected_channels(self) -> list[Channel]:
        return [ch for ch, ok in zip(self._channels, self._checked) if ok]


class _ChannelFilterProxy(QSortFilterProxyModel):
    """Case-insensitive filter on display text."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setFilterCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)

    def filterAcceptsRow(self, row: int, parent: QModelIndex) -> bool:  # noqa: N802
        pattern = self.filterRegularExpression().pattern()
        if not pattern:
            return True
        idx = self.sourceModel().index(row, 0, parent)
        text = self.sourceModel().data(idx, Qt.ItemDataRole.DisplayRole) or ""
        return pattern.lower() in text.lower()


class ChannelPickerDialog(QDialog):
    """Searchable checklist – uses a virtual model so even 50k channels open instantly."""

    def __init__(self, channels: list[Channel], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"Select Channels  ({len(channels)} found)")
        self.setMinimumSize(560, 480)
        layout = QVBoxLayout(self)

        self._search = QLineEdit()
        self._search.setPlaceholderText("Filter channels…")
        layout.addWidget(self._search)

        # Debounce search.
        self._filter_timer = QTimer(self)
        self._filter_timer.setSingleShot(True)
        self._filter_timer.setInterval(200)
        self._filter_timer.timeout.connect(self._apply_filter)
        self._search.textChanged.connect(lambda _: self._filter_timer.start())

        from PyQt6.QtWidgets import QListView

        self._model = _ChannelListModel(channels, self)
        self._proxy = _ChannelFilterProxy(self)
        self._proxy.setSourceModel(self._model)

        self._view = QListView()
        self._view.setModel(self._proxy)
        self._view.setUniformItemSizes(True)  # big perf win for large lists
        layout.addWidget(self._view)

        row = QHBoxLayout()
        sel_all = QPushButton("Select All")
        sel_all.clicked.connect(lambda: self._model.set_all_checked(True))
        sel_none = QPushButton("Deselect All")
        sel_none.clicked.connect(lambda: self._model.set_all_checked(False))
        row.addWidget(sel_all)
        row.addWidget(sel_none)
        row.addStretch()
        layout.addLayout(row)

        btns = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)
        layout.addWidget(btns)

    def _apply_filter(self) -> None:
        text = self._search.text()
        self._proxy.setFilterFixedString(text)

    def selected_channels(self) -> list[Channel]:
        return self._model.selected_channels()


# ── Settings ──────────────────────────────────────────────────────────────────

class SettingsDialog(QDialog):
    """
    Tabbed settings dialog.  Edits a copy of Config; call accepted_config()
    after exec() == Accepted to get the updated values.
    """

    def __init__(self, cfg: Config, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Settings")
        self.setMinimumWidth(480)
        self._cfg = cfg

        layout = QVBoxLayout(self)
        tabs = QTabWidget()
        tabs.addTab(self._general_tab(), "General")
        tabs.addTab(self._grid_tab(), "Grid")
        tabs.addTab(self._audio_tab(), "Playback")
        layout.addWidget(tabs)

        btns = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        btns.accepted.connect(self._on_accept)
        btns.rejected.connect(self.reject)
        layout.addWidget(btns)

    # ── Tabs ──────────────────────────────────────────────────────────────────

    def _general_tab(self) -> QWidget:
        w = QWidget()
        form = QFormLayout(w)
        form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)

        self._playlist_url = QLineEdit(self._cfg.default_playlist)
        form.addRow("Default playlist URL:", self._playlist_url)

        self._remember = QCheckBox("Remember last session")
        self._remember.setChecked(self._cfg.remember_session)
        self._remember.setToolTip(
            "On exit, saves the current stream list, active channel,\n"
            "view mode and window size so the next launch restores them."
        )
        form.addRow(self._remember)

        self._retry = QDoubleSpinBox()
        self._retry.setRange(0.5, 30.0)
        self._retry.setSingleStep(0.5)
        self._retry.setValue(self._cfg.retry_delay)
        self._retry.setSuffix(" s")
        form.addRow("Initial retry delay:", self._retry)

        self._max_retry = QDoubleSpinBox()
        self._max_retry.setRange(5.0, 300.0)
        self._max_retry.setSingleStep(5.0)
        self._max_retry.setValue(self._cfg.max_retry_delay)
        self._max_retry.setSuffix(" s")
        form.addRow("Max retry delay:", self._max_retry)

        return w

    def _grid_tab(self) -> QWidget:
        w = QWidget()
        form = QFormLayout(w)

        self._dynamic = QCheckBox("Dynamic grid (auto-size to fit all streams)")
        self._dynamic.setChecked(self._cfg.dynamic_grid)
        self._dynamic.toggled.connect(self._on_dynamic_toggle)
        form.addRow(self._dynamic)

        self._grid_rows = QSpinBox()
        self._grid_rows.setRange(1, 6)
        self._grid_rows.setValue(self._cfg.grid_rows)
        self._grid_rows.setEnabled(not self._cfg.dynamic_grid)
        form.addRow("Grid rows:", self._grid_rows)

        self._grid_cols = QSpinBox()
        self._grid_cols.setRange(1, 6)
        self._grid_cols.setValue(self._cfg.grid_cols)
        self._grid_cols.setEnabled(not self._cfg.dynamic_grid)
        form.addRow("Grid columns:", self._grid_cols)

        self._border = QSpinBox()
        self._border.setRange(1, 10)
        self._border.setValue(self._cfg.active_border)
        self._border.setSuffix(" px")
        form.addRow("Active stream border:", self._border)

        note = QLabel(
            "When dynamic grid is off, streams exceeding the grid size\n"
            "are shown on additional pages (Page Up / Page Down)."
        )
        note.setWordWrap(True)
        note.setStyleSheet("color: gray; font-size: 11px;")
        form.addRow(note)

        return w

    def _on_dynamic_toggle(self, checked: bool) -> None:
        self._grid_rows.setEnabled(not checked)
        self._grid_cols.setEnabled(not checked)

    def _audio_tab(self) -> QWidget:
        w = QWidget()
        form = QFormLayout(w)

        self._audio_enabled = QCheckBox("Enable audio for active stream")
        self._audio_enabled.setChecked(self._cfg.audio_enabled)
        form.addRow(self._audio_enabled)

        form.addRow(QLabel(""))   # spacer

        # ── VLC buffering ─────────────────────────────────────────────────────
        grp = QGroupBox("VLC Buffering")
        gf = QFormLayout(grp)

        self._net_cache = QSpinBox()
        self._net_cache.setRange(500, 30000)
        self._net_cache.setSingleStep(500)
        self._net_cache.setValue(self._cfg.vlc_network_cache)
        self._net_cache.setSuffix(" ms")
        gf.addRow("Network buffer:", self._net_cache)

        self._live_cache = QSpinBox()
        self._live_cache.setRange(200, 30000)
        self._live_cache.setSingleStep(200)
        self._live_cache.setValue(self._cfg.vlc_live_cache)
        self._live_cache.setSuffix(" ms")
        gf.addRow("Live stream buffer:", self._live_cache)

        buf_note = QLabel(
            "Higher values improve stability but add latency.\n"
            "Smart buffer only reports instability so you can tune\n"
            "buffers without extra reconnect churn."
        )
        buf_note.setWordWrap(True)
        buf_note.setStyleSheet("color: gray; font-size: 11px;")
        gf.addRow(buf_note)

        form.addRow(grp)

        form.addRow(QLabel(""))   # spacer

        # ── CENC / DRM ────────────────────────────────────────────────────────
        drm_label = QLabel("CENC decryption key (hex):")
        self._cenc_key = QLineEdit(self._cfg.cenc_decryption_key)
        self._cenc_key.setPlaceholderText(
            "e.g. a2226def4bc8f249de2daf36b7c12b1e  (M3UPT key)"
        )
        self._cenc_key.setToolTip(
            "Static AES key for CENC-encrypted streams.\n"
            "Widevine / FairPlay / PlayReady cannot be decrypted here."
        )
        form.addRow(drm_label, self._cenc_key)

        drm_note = QLabel(
            "Leave blank if your streams are not CENC-encrypted.\n"
            "M3UPT key: a2226def4bc8f249de2daf36b7c12b1e"
        )
        drm_note.setWordWrap(True)
        drm_note.setStyleSheet("color: gray; font-size: 11px;")
        form.addRow(drm_note)

        form.addRow(QLabel(""))   # spacer

        # ── Upscaling ────────────────────────────────────────────────────────
        grp_upscale = QGroupBox("Upscaling (fullscreen only)")
        uf = QFormLayout(grp_upscale)

        from PyQt6.QtWidgets import QComboBox
        self._upscale_combo = QComboBox()
        self._upscale_presets = [
            ("off", "Off"),
            ("lanczos", "Lanczos scaling"),
            ("sharpen_light", "Lanczos + Sharpen (Light)"),
            ("sharpen_medium", "Lanczos + Sharpen (Medium)"),
            ("sharpen_strong", "Lanczos + Sharpen (Strong)"),
        ]
        for key, label in self._upscale_presets:
            self._upscale_combo.addItem(label, key)
        current = self._cfg.upscale_preset
        idx = next((i for i, (k, _) in enumerate(self._upscale_presets) if k == current), 0)
        self._upscale_combo.setCurrentIndex(idx)
        uf.addRow("Preset:", self._upscale_combo)

        upscale_note = QLabel(
            "Upscaling uses software decoding for the fullscreen stream.\n"
            "Higher presets sharpen the image but use more CPU."
        )
        upscale_note.setWordWrap(True)
        upscale_note.setStyleSheet("color: gray; font-size: 11px;")
        uf.addRow(upscale_note)

        form.addRow(grp_upscale)

        return w

    # ── Save ──────────────────────────────────────────────────────────────────

    def _on_accept(self) -> None:
        # Write form values back into the config object.
        self._cfg.default_playlist = self._playlist_url.text().strip()
        self._cfg.remember_session = self._remember.isChecked()
        self._cfg.retry_delay = self._retry.value()
        self._cfg.max_retry_delay = self._max_retry.value()
        self._cfg.dynamic_grid = self._dynamic.isChecked()
        self._cfg.grid_rows = self._grid_rows.value()
        self._cfg.grid_cols = self._grid_cols.value()
        self._cfg.active_border = self._border.value()
        self._cfg.audio_enabled = self._audio_enabled.isChecked()
        self._cfg.vlc_network_cache = self._net_cache.value()
        self._cfg.vlc_live_cache = self._live_cache.value()
        self._cfg.cenc_decryption_key = self._cenc_key.text().strip()
        self._cfg.upscale_preset = self._upscale_combo.currentData()
        self.accept()


# ── Favourites (Streams + Playlists tabs) ─────────────────────────────────────

class FavouritesDialog(QDialog):
    """Unified favourites: Streams tab + Saved Playlists tab."""

    def __init__(
        self,
        favourites: list[dict],
        saved_playlists: list[dict],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Favourites")
        self.setMinimumSize(580, 480)
        layout = QVBoxLayout(self)

        self._tabs = QTabWidget()
        self._tabs.addTab(self._streams_tab(favourites), "★ Streams")
        self._tabs.addTab(self._playlists_tab(saved_playlists), "♫ Playlists")
        layout.addWidget(self._tabs)

        btns = QHBoxLayout()
        btn_load = QPushButton("Load")
        btn_load.setToolTip("Load checked streams or selected playlist")
        btn_load.clicked.connect(self.accept)
        btn_close = QPushButton("Close")
        btn_close.clicked.connect(self.reject)
        btns.addStretch()
        btns.addWidget(btn_load)
        btns.addWidget(btn_close)
        layout.addLayout(btns)

    # ── Streams tab ──────────────────────────────────────────────────────────

    def _streams_tab(self, favourites: list[dict]) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)

        self._fav_list = QListWidget()
        self._fav_list.setSelectionMode(QListWidget.SelectionMode.SingleSelection)
        for fav in favourites:
            self._add_fav_item(Channel.from_dict(fav))
        layout.addWidget(self._fav_list)

        btn_row = QHBoxLayout()
        btn_up = QPushButton("▲ Up")
        btn_up.clicked.connect(self._fav_move_up)
        btn_down = QPushButton("▼ Down")
        btn_down.clicked.connect(self._fav_move_down)
        btn_remove = QPushButton("✕ Remove")
        btn_remove.clicked.connect(self._fav_remove)
        btn_add = QPushButton("+ Add…")
        btn_add.clicked.connect(self._fav_add)
        btn_row.addWidget(btn_up)
        btn_row.addWidget(btn_down)
        btn_row.addWidget(btn_remove)
        btn_row.addStretch()
        btn_row.addWidget(btn_add)
        layout.addLayout(btn_row)
        return w

    def _add_fav_item(self, ch: Channel) -> None:
        item = QListWidgetItem(f"★ {ch.display_name()}  —  {ch.url}")
        item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
        item.setCheckState(Qt.CheckState.Checked)
        item.setData(Qt.ItemDataRole.UserRole, ch.to_dict())
        self._fav_list.addItem(item)

    def _fav_move_up(self) -> None:
        row = self._fav_list.currentRow()
        if row > 0:
            item = self._fav_list.takeItem(row)
            self._fav_list.insertItem(row - 1, item)
            self._fav_list.setCurrentRow(row - 1)

    def _fav_move_down(self) -> None:
        row = self._fav_list.currentRow()
        if 0 <= row < self._fav_list.count() - 1:
            item = self._fav_list.takeItem(row)
            self._fav_list.insertItem(row + 1, item)
            self._fav_list.setCurrentRow(row + 1)

    def _fav_remove(self) -> None:
        row = self._fav_list.currentRow()
        if row >= 0:
            self._fav_list.takeItem(row)

    def _fav_add(self) -> None:
        dlg = AddStreamDialog(self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self._add_fav_item(dlg.result_channel())

    # ── Playlists tab ────────────────────────────────────────────────────────

    def _playlists_tab(self, playlists: list[dict]) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)

        self._pl_list = QListWidget()
        self._pl_list.setSelectionMode(QListWidget.SelectionMode.SingleSelection)
        for pl in playlists:
            item = QListWidgetItem(f"{pl.get('name', 'Untitled')}  —  {pl['url']}")
            item.setData(Qt.ItemDataRole.UserRole, pl)
            self._pl_list.addItem(item)
        layout.addWidget(self._pl_list)

        btn_row = QHBoxLayout()
        btn_add = QPushButton("+ Add")
        btn_add.clicked.connect(self._pl_add)
        btn_remove = QPushButton("✕ Remove")
        btn_remove.clicked.connect(self._pl_remove)
        btn_row.addWidget(btn_add)
        btn_row.addWidget(btn_remove)
        btn_row.addStretch()
        layout.addLayout(btn_row)

        self._pl_list.itemDoubleClicked.connect(lambda _: self.accept())
        return w

    def _pl_add(self) -> None:
        dlg = _AddPlaylistDialog(self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            pl = dlg.result()
            item = QListWidgetItem(f"{pl['name']}  —  {pl['url']}")
            item.setData(Qt.ItemDataRole.UserRole, pl)
            self._pl_list.addItem(item)

    def _pl_remove(self) -> None:
        row = self._pl_list.currentRow()
        if row >= 0:
            self._pl_list.takeItem(row)

    # ── Public result accessors ──────────────────────────────────────────────

    def active_tab(self) -> int:
        return self._tabs.currentIndex()

    def all_favourites(self) -> list[dict]:
        return [
            self._fav_list.item(i).data(Qt.ItemDataRole.UserRole)
            for i in range(self._fav_list.count())
        ]

    def checked_channels(self) -> list[Channel]:
        return [
            Channel.from_dict(self._fav_list.item(i).data(Qt.ItemDataRole.UserRole))
            for i in range(self._fav_list.count())
            if self._fav_list.item(i).checkState() == Qt.CheckState.Checked
        ]

    def all_playlists(self) -> list[dict]:
        return [
            self._pl_list.item(i).data(Qt.ItemDataRole.UserRole)
            for i in range(self._pl_list.count())
        ]

    def selected_playlist(self) -> dict | None:
        item = self._pl_list.currentItem()
        return item.data(Qt.ItemDataRole.UserRole) if item else None


# ── Saved Playlists (private helper) ─────────────────────────────────────────


class _AddPlaylistDialog(QDialog):
    """Small dialog to enter a playlist name and URL."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Add Playlist")
        self.setMinimumWidth(420)
        layout = QVBoxLayout(self)

        layout.addWidget(QLabel("Playlist name:"))
        self._name = QLineEdit()
        self._name.setPlaceholderText("My IPTV list")
        layout.addWidget(self._name)

        layout.addWidget(QLabel("Playlist URL:"))
        self._url = QLineEdit()
        self._url.setPlaceholderText("https://example.com/playlist.m3u8")
        layout.addWidget(self._url)

        btns = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        btns.accepted.connect(self._accept)
        btns.rejected.connect(self.reject)
        layout.addWidget(btns)

    def _accept(self) -> None:
        if self._url.text().strip():
            self.accept()

    def result(self) -> dict:
        return {
            "name": self._name.text().strip() or "Untitled",
            "url": self._url.text().strip(),
        }


# ── Grid Presets (Save + Load) ────────────────────────────────────────────────


class GridPresetsDialog(QDialog):
    """Manage grid presets: load existing, save current, or delete."""

    def __init__(
        self,
        presets: list[dict],
        current_channels: list | None = None,
        current_grid: dict | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Grid Presets")
        self.setMinimumSize(500, 380)
        self._current_channels = current_channels
        self._current_grid = current_grid
        layout = QVBoxLayout(self)

        self._list = QListWidget()
        self._list.setSelectionMode(QListWidget.SelectionMode.SingleSelection)
        for p in presets:
            self._add_preset_item(p)
        self._list.doubleClicked.connect(self.accept)
        layout.addWidget(self._list)

        row = QHBoxLayout()
        if current_channels:
            btn_save = QPushButton("Save Current Grid…")
            btn_save.clicked.connect(self._save_current)
            row.addWidget(btn_save)
        btn_del = QPushButton("Delete")
        btn_del.clicked.connect(self._remove)
        row.addWidget(btn_del)
        row.addStretch()
        layout.addLayout(row)

        btns = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        btns.button(QDialogButtonBox.StandardButton.Ok).setText("Load")
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)
        layout.addWidget(btns)

    def _add_preset_item(self, p: dict) -> None:
        grid = "dynamic" if p.get("dynamic") else f"{p.get('rows', 2)}×{p.get('cols', 2)}"
        n_ch = len(p.get("channels", []))
        item = QListWidgetItem(f"{p['name']}  —  {grid}  ({n_ch} streams)")
        item.setData(Qt.ItemDataRole.UserRole, p)
        self._list.addItem(item)

    def _save_current(self) -> None:
        name, ok = _prompt_text(self, "Save Grid Preset", "Preset name:", "e.g. News 2×2")
        if not ok or not name:
            return
        preset: dict = {
            "name": name,
            "rows": self._current_grid.get("rows", 2) if self._current_grid else 2,
            "cols": self._current_grid.get("cols", 2) if self._current_grid else 2,
            "dynamic": self._current_grid.get("dynamic", False) if self._current_grid else False,
            "channels": self._current_channels or [],
        }
        self._add_preset_item(preset)

    def _remove(self) -> None:
        row = self._list.currentRow()
        if row >= 0:
            self._list.takeItem(row)

    def all_presets(self) -> list[dict]:
        return [
            self._list.item(i).data(Qt.ItemDataRole.UserRole)
            for i in range(self._list.count())
        ]

    def selected_preset(self) -> dict | None:
        item = self._list.currentItem()
        return item.data(Qt.ItemDataRole.UserRole) if item else None


def _prompt_text(parent, title: str, label: str, placeholder: str = "") -> tuple[str, bool]:
    """Simple text input dialog (avoids QInputDialog for consistent styling)."""
    dlg = QDialog(parent)
    dlg.setWindowTitle(title)
    dlg.setMinimumWidth(360)
    lay = QVBoxLayout(dlg)
    lay.addWidget(QLabel(label))
    edit = QLineEdit()
    edit.setPlaceholderText(placeholder)
    lay.addWidget(edit)
    btns = QDialogButtonBox(
        QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
    )
    btns.accepted.connect(dlg.accept)
    btns.rejected.connect(dlg.reject)
    lay.addWidget(btns)
    edit.returnPressed.connect(dlg.accept)
    if dlg.exec() == QDialog.DialogCode.Accepted:
        return edit.text().strip(), True
    return "", False
