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


# ── Load Playlist ─────────────────────────────────────────────────────────────

class LoadPlaylistDialog(QDialog):
    """Prompt for a local M3U file or a remote URL."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Load Playlist")
        self.setMinimumWidth(500)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Playlist file path or URL (.m3u / .m3u8):"))

        row = QHBoxLayout()
        self._src = QLineEdit()
        self._src.setPlaceholderText("/path/to/playlist.m3u  or  http://host/pl.m3u8")
        row.addWidget(self._src)
        browse = QPushButton("Browse…")
        browse.clicked.connect(self._browse)
        row.addWidget(browse)
        layout.addLayout(row)

        btns = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        btns.accepted.connect(self._accept)
        btns.rejected.connect(self.reject)
        layout.addWidget(btns)

    def _browse(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Open Playlist", "", "Playlists (*.m3u *.m3u8);;All files (*)"
        )
        if path:
            self._src.setText(path)

    def _accept(self) -> None:
        if self._src.text().strip():
            self.accept()

    def source(self) -> str:
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
        self._net_cache.setRange(500, 10000)
        self._net_cache.setSingleStep(500)
        self._net_cache.setValue(self._cfg.vlc_network_cache)
        self._net_cache.setSuffix(" ms")
        gf.addRow("Network buffer:", self._net_cache)

        self._live_cache = QSpinBox()
        self._live_cache.setRange(200, 5000)
        self._live_cache.setSingleStep(200)
        self._live_cache.setValue(self._cfg.vlc_live_cache)
        self._live_cache.setSuffix(" ms")
        gf.addRow("Live stream buffer:", self._live_cache)

        buf_note = QLabel(
            "Higher values improve stability but add latency.\n"
            "VLC handles decoding with hardware acceleration."
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
        self.accept()


# ── Favourites ────────────────────────────────────────────────────────────────

class FavouritesDialog(QDialog):
    """Manage favourite channels: add, remove, reorder, and load."""

    def __init__(self, favourites: list[dict], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Favourites")
        self.setMinimumSize(560, 420)
        layout = QVBoxLayout(self)

        self._list = QListWidget()
        self._list.setSelectionMode(QListWidget.SelectionMode.SingleSelection)
        for fav in favourites:
            self._add_item(Channel.from_dict(fav))
        layout.addWidget(self._list)

        btn_row = QHBoxLayout()
        btn_up = QPushButton("▲ Up")
        btn_up.clicked.connect(self._move_up)
        btn_down = QPushButton("▼ Down")
        btn_down.clicked.connect(self._move_down)
        btn_remove = QPushButton("✕ Remove")
        btn_remove.clicked.connect(self._remove)
        btn_add = QPushButton("+ Add…")
        btn_add.clicked.connect(self._add)
        btn_row.addWidget(btn_up)
        btn_row.addWidget(btn_down)
        btn_row.addWidget(btn_remove)
        btn_row.addStretch()
        btn_row.addWidget(btn_add)
        layout.addLayout(btn_row)

        btns = QHBoxLayout()
        btn_load = QPushButton("Load Checked")
        btn_load.setToolTip("Add checked channels to the current view")
        btn_load.clicked.connect(self.accept)
        btn_close = QPushButton("Close")
        btn_close.clicked.connect(self.reject)
        btns.addStretch()
        btns.addWidget(btn_load)
        btns.addWidget(btn_close)
        layout.addLayout(btns)

    def _add_item(self, ch: Channel) -> None:
        item = QListWidgetItem(f"★ {ch.display_name()}  —  {ch.url}")
        item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
        item.setCheckState(Qt.CheckState.Checked)
        item.setData(Qt.ItemDataRole.UserRole, ch.to_dict())
        self._list.addItem(item)

    def _move_up(self) -> None:
        row = self._list.currentRow()
        if row > 0:
            item = self._list.takeItem(row)
            self._list.insertItem(row - 1, item)
            self._list.setCurrentRow(row - 1)

    def _move_down(self) -> None:
        row = self._list.currentRow()
        if 0 <= row < self._list.count() - 1:
            item = self._list.takeItem(row)
            self._list.insertItem(row + 1, item)
            self._list.setCurrentRow(row + 1)

    def _remove(self) -> None:
        row = self._list.currentRow()
        if row >= 0:
            self._list.takeItem(row)

    def _add(self) -> None:
        dlg = AddStreamDialog(self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self._add_item(dlg.result_channel())

    def all_favourites(self) -> list[dict]:
        """Return the full favourites list in current order."""
        return [
            self._list.item(i).data(Qt.ItemDataRole.UserRole)
            for i in range(self._list.count())
        ]

    def checked_channels(self) -> list[Channel]:
        """Return only the checked channels (for loading)."""
        return [
            Channel.from_dict(self._list.item(i).data(Qt.ItemDataRole.UserRole))
            for i in range(self._list.count())
            if self._list.item(i).checkState() == Qt.CheckState.Checked
        ]


# ── Saved Playlists ───────────────────────────────────────────────────────────

class PlaylistManagerDialog(QDialog):
    """Manage saved playlist URLs: add, remove, and load."""

    def __init__(self, playlists: list[dict], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Saved Playlists")
        self.setMinimumSize(560, 360)
        layout = QVBoxLayout(self)

        self._list = QListWidget()
        self._list.setSelectionMode(QListWidget.SelectionMode.SingleSelection)
        for pl in playlists:
            item = QListWidgetItem(f"{pl.get('name', 'Untitled')}  —  {pl['url']}")
            item.setData(Qt.ItemDataRole.UserRole, pl)
            self._list.addItem(item)
        layout.addWidget(self._list)

        btn_row = QHBoxLayout()
        btn_add = QPushButton("+ Add")
        btn_add.clicked.connect(self._add)
        btn_remove = QPushButton("✕ Remove")
        btn_remove.clicked.connect(self._remove)
        btn_row.addWidget(btn_add)
        btn_row.addWidget(btn_remove)
        btn_row.addStretch()
        layout.addLayout(btn_row)

        btns = QHBoxLayout()
        btn_load = QPushButton("Load Selected")
        btn_load.setToolTip("Parse the selected playlist and pick channels")
        btn_load.clicked.connect(self.accept)
        btn_close = QPushButton("Close")
        btn_close.clicked.connect(self.reject)
        btns.addStretch()
        btns.addWidget(btn_load)
        btns.addWidget(btn_close)
        layout.addLayout(btns)

        self._list.itemDoubleClicked.connect(lambda _: self.accept())

    def _add(self) -> None:
        dlg = _AddPlaylistDialog(self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            pl = dlg.result()
            item = QListWidgetItem(f"{pl['name']}  —  {pl['url']}")
            item.setData(Qt.ItemDataRole.UserRole, pl)
            self._list.addItem(item)

    def _remove(self) -> None:
        row = self._list.currentRow()
        if row >= 0:
            self._list.takeItem(row)

    def all_playlists(self) -> list[dict]:
        """Return all playlists in current order."""
        return [
            self._list.item(i).data(Qt.ItemDataRole.UserRole)
            for i in range(self._list.count())
        ]

    def selected_playlist(self) -> dict | None:
        """Return the currently selected playlist."""
        item = self._list.currentItem()
        return item.data(Qt.ItemDataRole.UserRole) if item else None


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


# ── Grid Presets ──────────────────────────────────────────────────────────────


class SaveGridPresetDialog(QDialog):
    """Prompt for a name when saving the current grid as a preset."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Save Grid Preset")
        self.setMinimumWidth(380)
        layout = QVBoxLayout(self)

        layout.addWidget(QLabel("Preset name:"))
        self._name = QLineEdit()
        self._name.setPlaceholderText("e.g. News 2×2")
        layout.addWidget(self._name)

        btns = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        btns.accepted.connect(self._accept)
        btns.rejected.connect(self.reject)
        layout.addWidget(btns)

    def _accept(self) -> None:
        if self._name.text().strip():
            self.accept()

    def preset_name(self) -> str:
        return self._name.text().strip()


class LoadGridPresetDialog(QDialog):
    """Select a grid preset to load or delete."""

    def __init__(self, presets: list[dict], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Grid Presets")
        self.setMinimumSize(480, 360)
        layout = QVBoxLayout(self)

        self._list = QListWidget()
        self._list.setSelectionMode(QListWidget.SelectionMode.SingleSelection)
        for p in presets:
            grid = "dynamic" if p.get("dynamic") else f"{p.get('rows', 2)}×{p.get('cols', 2)}"
            n_ch = len(p.get("channels", []))
            item = QListWidgetItem(f"{p['name']}  —  {grid}  ({n_ch} streams)")
            item.setData(Qt.ItemDataRole.UserRole, p)
            self._list.addItem(item)
        self._list.doubleClicked.connect(self.accept)
        layout.addWidget(self._list)

        row = QHBoxLayout()
        btn_del = QPushButton("Delete")
        btn_del.clicked.connect(self._remove)
        row.addWidget(btn_del)
        row.addStretch()
        layout.addLayout(row)

        btns = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)
        layout.addWidget(btns)

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
