# mural/gui/playlist_tab.py
#
# Mural — Animated Wallpaper Platform for Linux
# Copyright (C) 2024  Mural Contributors
# GPL v3 — see LICENSE

"""Playlist editor tab.

Layout:
  Left  — named playlist list + Create / Delete buttons
  Right — editor for the selected playlist:
            Name, Shuffle, Interval, Monitor assignments, Wallpaper list
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from PySide6.QtCore import QSize, Qt, QModelIndex
from PySide6.QtGui import QIcon, QImageReader, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QFileDialog,
    QGroupBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from mural.gui.wallpaper_card import WallpaperInfo

_THUMB_W = 56
_THUMB_H = 42
_THUMBNAIL_NAMES = ("preview.jpg", "preview.png", "preview.gif",
                    "preview.webp", "thumbnail.jpg", "thumbnail.png")


def _load_icon(wallpaper_path: str) -> QIcon | None:
    p = Path(wallpaper_path)
    if not p.is_dir():
        return None
    for name in _THUMBNAIL_NAMES:
        candidate = p / name
        if candidate.exists():
            reader = QImageReader(str(candidate))
            reader.setAutoTransform(True)
            img = reader.read()
            if not img.isNull():
                px = QPixmap.fromImage(img).scaled(
                    _THUMB_W, _THUMB_H,
                    Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                    Qt.TransformationMode.SmoothTransformation,
                )
                return QIcon(px)
    return None


class PlaylistTab(QWidget):
    """Full playlist editor.

    Args:
        core_proxy: dasbus proxy for ``com.mural.Core``.  May be ``None``.
        parent: Optional Qt parent.
    """

    def __init__(self, core_proxy: Any | None = None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._core = core_proxy
        self._selected_id: str | None = None  # currently selected playlist id
        self._playlists: list[dict] = []       # cache from last GetPlaylists()
        self._monitor_names: list[str] = []    # connected monitor names
        self._build_ui()
        self._reload()

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setHandleWidth(1)
        splitter.setChildrenCollapsible(False)
        root.addWidget(splitter)

        splitter.addWidget(self._build_left_panel())
        splitter.addWidget(self._build_right_panel())
        splitter.setSizes([220, 600])

    # ------ Left panel: playlist list ------

    def _build_left_panel(self) -> QWidget:
        w = QWidget()
        w.setMinimumWidth(180)
        w.setMaximumWidth(260)
        layout = QVBoxLayout(w)
        layout.setContentsMargins(8, 8, 4, 8)
        layout.setSpacing(6)

        header = QLabel("Playlists")
        header.setStyleSheet("font-weight: bold; font-size: 13px; color: #e0e0e0;")
        layout.addWidget(header)

        self._pl_list = QListWidget()
        self._pl_list.setStyleSheet(
            "QListWidget { background: #141421; border: 1px solid #2a2a2a; }"
            "QListWidget::item { color: #e0e0e0; padding: 6px 8px; }"
            "QListWidget::item:selected { background: #2979FF; color: #fff; }"
        )
        self._pl_list.currentItemChanged.connect(self._on_playlist_selected)
        layout.addWidget(self._pl_list, 1)

        btn_row = QHBoxLayout()
        new_btn = QPushButton("+ New")
        new_btn.setFixedHeight(28)
        new_btn.clicked.connect(self._create_playlist)
        btn_row.addWidget(new_btn)

        self._del_btn = QPushButton("Delete")
        self._del_btn.setFixedHeight(28)
        self._del_btn.setEnabled(False)
        self._del_btn.clicked.connect(self._delete_playlist)
        btn_row.addWidget(self._del_btn)

        layout.addLayout(btn_row)

        self._status_label = QLabel()
        self._status_label.setWordWrap(True)
        self._status_label.setStyleSheet("color: #888; font-size: 10px;")
        layout.addWidget(self._status_label)

        return w

    # ------ Right panel: editor ------

    def _build_right_panel(self) -> QWidget:
        self._right_stack_widget = QWidget()
        layout = QVBoxLayout(self._right_stack_widget)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(0)

        # Empty-state hint shown when nothing is selected.
        self._empty_label = QLabel("Select or create a playlist")
        self._empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._empty_label.setStyleSheet("color: #555; font-size: 13px;")
        layout.addWidget(self._empty_label)

        # Editor widget (hidden when nothing selected).
        self._editor = QWidget()
        self._editor.hide()
        ed_layout = QVBoxLayout(self._editor)
        ed_layout.setContentsMargins(0, 0, 0, 0)
        ed_layout.setSpacing(12)

        # Name
        name_row = QHBoxLayout()
        name_row.addWidget(QLabel("Name:"))
        self._name_edit = QLineEdit()
        self._name_edit.setFixedHeight(28)
        self._name_edit.editingFinished.connect(self._on_name_changed)
        name_row.addWidget(self._name_edit)
        ed_layout.addLayout(name_row)

        # Options row: shuffle + interval
        opts_row = QHBoxLayout()
        self._shuffle_chk = QCheckBox("Shuffle")
        self._shuffle_chk.toggled.connect(self._on_shuffle_toggled)
        opts_row.addWidget(self._shuffle_chk)

        opts_row.addSpacing(20)
        opts_row.addWidget(QLabel("Interval:"))
        self._interval_spin = QSpinBox()
        self._interval_spin.setRange(0, 1440)
        self._interval_spin.setSuffix(" min")
        self._interval_spin.setFixedWidth(90)
        self._interval_spin.setToolTip("0 = use the global setting in Settings → Playlist")
        self._interval_spin.valueChanged.connect(self._on_interval_changed)
        opts_row.addWidget(self._interval_spin)
        opts_row.addWidget(QLabel("(0 = global)"))
        opts_row.addStretch()
        ed_layout.addLayout(opts_row)

        # Monitor assignments
        mon_box = QGroupBox("Assign to monitors")
        self._mon_layout = QHBoxLayout(mon_box)
        self._mon_layout.setContentsMargins(8, 4, 8, 4)
        self._mon_checkboxes: dict[str, QCheckBox] = {}
        ed_layout.addWidget(mon_box)

        # Wallpaper list
        wp_box = QGroupBox("Wallpapers")
        wp_layout = QVBoxLayout(wp_box)
        wp_layout.setContentsMargins(6, 6, 6, 6)
        wp_layout.setSpacing(6)

        self._wp_list = QListWidget()
        self._wp_list.setIconSize(QSize(_THUMB_W, _THUMB_H))
        self._wp_list.setSpacing(2)
        self._wp_list.setAlternatingRowColors(True)
        self._wp_list.setStyleSheet(
            "QListWidget { background: #141421; border: 1px solid #2a2a2a; }"
            "QListWidget::item { color: #e0e0e0; padding: 3px 6px; height: 50px; }"
            "QListWidget::item:selected { background: #2979FF; color: #fff; }"
            "QListWidget::item:alternate { background: #1A1A2E; }"
        )
        self._wp_list.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        self._wp_list.setDefaultDropAction(Qt.DropAction.MoveAction)
        self._wp_list.currentRowChanged.connect(self._on_wp_row_changed)
        self._wp_list.model().rowsMoved.connect(self._on_wp_reordered)

        # Empty-state overlay shown when the playlist has no wallpapers.
        self._wp_empty_label = QLabel(
            "No wallpapers yet — add from the Library tab or click Add"
        )
        self._wp_empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._wp_empty_label.setStyleSheet("color: #555; font-size: 12px;")
        self._wp_empty_label.hide()

        wp_layout.addWidget(self._wp_list, 1)
        wp_layout.addWidget(self._wp_empty_label, 1)

        # Per-item duration override
        dur_row = QHBoxLayout()
        dur_row.addWidget(QLabel("Duration for selected:"))
        self._item_dur_spin = QSpinBox()
        self._item_dur_spin.setRange(0, 1440)
        self._item_dur_spin.setSuffix(" min")
        self._item_dur_spin.setFixedWidth(90)
        self._item_dur_spin.setToolTip("0 = use playlist default interval")
        self._item_dur_spin.setEnabled(False)
        self._item_dur_spin.valueChanged.connect(self._on_item_duration_changed)
        dur_row.addWidget(self._item_dur_spin)
        dur_row.addWidget(QLabel("(0 = playlist default)"))
        dur_row.addStretch()
        wp_layout.addLayout(dur_row)

        wp_btn_row = QHBoxLayout()
        add_wp_btn = QPushButton("+ Add Wallpaper")
        add_wp_btn.clicked.connect(self._add_wallpaper)
        wp_btn_row.addWidget(add_wp_btn)

        up_btn = QPushButton("↑")
        up_btn.setFixedWidth(32)
        up_btn.clicked.connect(self._move_up)
        wp_btn_row.addWidget(up_btn)

        down_btn = QPushButton("↓")
        down_btn.setFixedWidth(32)
        down_btn.clicked.connect(self._move_down)
        wp_btn_row.addWidget(down_btn)

        wp_btn_row.addStretch()

        remove_wp_btn = QPushButton("Remove")
        remove_wp_btn.clicked.connect(self._remove_wallpaper)
        wp_btn_row.addWidget(remove_wp_btn)
        wp_layout.addLayout(wp_btn_row)

        self._wp_status = QLabel()
        self._wp_status.setStyleSheet("color: #888; font-size: 11px;")
        wp_layout.addWidget(self._wp_status)

        ed_layout.addWidget(wp_box, 1)
        layout.addWidget(self._editor, 1)

        return self._right_stack_widget

    # ------------------------------------------------------------------
    # Data loading
    # ------------------------------------------------------------------

    def _reload(self) -> None:
        """Refresh playlist list and monitor list from the service."""
        self._playlists = []
        if self._core:
            try:
                self._playlists = json.loads(self._core.GetPlaylists())
                self._status_label.setText("")
            except Exception as exc:
                self._status_label.setText(f"GetPlaylists error: {exc}")
            try:
                self._monitor_names = list(self._core.GetMonitors())
            except Exception:
                pass

        # Build monitor checkboxes FIRST so _populate_editor sees fresh widgets
        # when _rebuild_pl_list fires the selection signal.
        self._rebuild_monitor_checkboxes()
        self._rebuild_pl_list()

    def _get_playlist(self, playlist_id: str) -> dict | None:
        return next((p for p in self._playlists if p["id"] == playlist_id), None)

    @staticmethod
    def _pl_display_text(pl: dict) -> str:
        """Return display string: name + optional shuffle badge + item count."""
        name = pl.get("name", "Untitled")
        count = len(pl.get("wallpaper_paths", []))
        badge = " ⇌" if pl.get("shuffle", False) else ""
        return f"{name}{badge}  ({count})"

    def _rebuild_pl_list(self) -> None:
        prev_id = self._selected_id
        self._pl_list.blockSignals(True)
        self._pl_list.clear()
        for pl in self._playlists:
            item = QListWidgetItem(self._pl_display_text(pl))
            item.setData(Qt.ItemDataRole.UserRole, pl["id"])
            self._pl_list.addItem(item)
        self._pl_list.blockSignals(False)

        # Restore selection.
        if prev_id:
            for i in range(self._pl_list.count()):
                it = self._pl_list.item(i)
                if it and it.data(Qt.ItemDataRole.UserRole) == prev_id:
                    self._pl_list.setCurrentItem(it)
                    return
        # Nothing selected.
        self._selected_id = None
        self._show_editor(False)

    def _rebuild_monitor_checkboxes(self) -> None:
        """Rebuild the monitor assignment checkboxes."""
        # Clear ALL layout items — this removes stale checkboxes, the
        # "No monitors" fallback label, and accumulated stretch spacers.
        while self._mon_layout.count():
            item = self._mon_layout.takeAt(0)
            w = item.widget()
            if w:
                w.deleteLater()
        self._mon_checkboxes.clear()

        for mon in self._monitor_names:
            chk = QCheckBox(mon)
            chk.toggled.connect(lambda checked, m=mon: self._on_monitor_toggled(m, checked))
            self._mon_layout.addWidget(chk)
            self._mon_checkboxes[mon] = chk

        if not self._monitor_names:
            lbl = QLabel("No monitors detected")
            lbl.setStyleSheet("color: #555;")
            self._mon_layout.addWidget(lbl)

        self._mon_layout.addStretch()

    def _populate_editor(self, pl: dict) -> None:
        """Fill the editor widgets from playlist dict *pl*."""
        self._name_edit.blockSignals(True)
        self._name_edit.setText(pl.get("name", ""))
        self._name_edit.blockSignals(False)

        self._shuffle_chk.blockSignals(True)
        self._shuffle_chk.setChecked(bool(pl.get("shuffle", False)))
        self._shuffle_chk.blockSignals(False)

        self._interval_spin.blockSignals(True)
        self._interval_spin.setValue(int(pl.get("interval_minutes", 0)))
        self._interval_spin.blockSignals(False)

        # Monitor checkboxes
        assigned = set(pl.get("monitor_assignments", []))
        for mon, chk in self._mon_checkboxes.items():
            chk.blockSignals(True)
            chk.setChecked(mon in assigned)
            chk.blockSignals(False)

        # Wallpaper list
        self._wp_list.clear()
        paths = pl.get("wallpaper_paths", [])
        durations = pl.get("item_durations", [])
        for i, path in enumerate(paths):
            dur = durations[i] if i < len(durations) else 0
            self._add_wp_item(path, dur)

        self._item_dur_spin.blockSignals(True)
        self._item_dur_spin.setValue(0)
        self._item_dur_spin.blockSignals(False)
        self._item_dur_spin.setEnabled(False)

        self._update_wp_status(pl)

    def _add_wp_item(self, path: str, duration_minutes: int = 0) -> None:
        name = Path(path).name
        icon = _load_icon(path)
        item = QListWidgetItem(name)
        item.setData(Qt.ItemDataRole.UserRole, path)
        item.setData(Qt.ItemDataRole.UserRole + 1, duration_minutes)
        item.setToolTip(path)
        if icon:
            item.setIcon(icon)
        self._wp_list.addItem(item)

    def _update_wp_status(self, pl: dict) -> None:
        paths = pl.get("wallpaper_paths", [])
        n = len(paths)
        missing = sum(1 for p in paths if not Path(p).exists())
        empty = n == 0
        self._wp_list.setVisible(not empty)
        self._wp_empty_label.setVisible(empty)
        if not empty:
            txt = f"{n} item{'s' if n != 1 else ''}"
            if missing:
                txt += f"  ({missing} missing from disk)"
            self._wp_status.setText(txt)
        else:
            self._wp_status.setText("")

    def _show_editor(self, visible: bool) -> None:
        self._empty_label.setVisible(not visible)
        self._editor.setVisible(visible)
        self._del_btn.setEnabled(visible)

    # ------------------------------------------------------------------
    # Left-panel actions
    # ------------------------------------------------------------------

    def _on_playlist_selected(self, current: QListWidgetItem | None, _prev) -> None:
        if not current:
            self._selected_id = None
            self._show_editor(False)
            return
        playlist_id = current.data(Qt.ItemDataRole.UserRole)
        self._selected_id = playlist_id
        pl = self._get_playlist(playlist_id)
        if pl:
            self._show_editor(True)
            self._populate_editor(pl)

    def _create_playlist(self) -> None:
        if not self._core:
            self._status_label.setText("Service unavailable — start mural-core.service")
            return
        name, ok = QInputDialog.getText(self, "New Playlist", "Playlist name:")
        if not ok or not name.strip():
            return
        try:
            new_id = self._core.CreatePlaylist(name.strip())
        except Exception as exc:
            self._status_label.setText(f"Error: {exc}")
            return
        self._status_label.setText("")
        self._selected_id = new_id
        self._reload()
        # _rebuild_pl_list already restored the selection; just focus the name field.
        self._name_edit.selectAll()
        self._name_edit.setFocus()

    def _delete_playlist(self) -> None:
        if not self._core or not self._selected_id:
            return
        try:
            self._core.DeletePlaylist(self._selected_id)
        except Exception as exc:
            self._status_label.setText(f"Error: {exc}")
            return
        self._selected_id = None
        self._reload()

    # ------------------------------------------------------------------
    # Editor change handlers (each pushes to service immediately)
    # ------------------------------------------------------------------

    def _on_name_changed(self) -> None:
        if not self._core or not self._selected_id:
            return
        try:
            self._core.SetPlaylistName(self._selected_id, self._name_edit.text())
        except Exception:
            return
        # Keep local cache in sync first, then update list item text with badge.
        pl = self._get_playlist(self._selected_id)
        if pl:
            pl["name"] = self._name_edit.text()
        for i in range(self._pl_list.count()):
            it = self._pl_list.item(i)
            if it and it.data(Qt.ItemDataRole.UserRole) == self._selected_id:
                it.setText(self._pl_display_text(pl) if pl else self._name_edit.text())
                break

    def _on_shuffle_toggled(self, checked: bool) -> None:
        if not self._core or not self._selected_id:
            return
        try:
            self._core.SetPlaylistShuffle(self._selected_id, checked)
        except Exception:
            return
        pl = self._get_playlist(self._selected_id)
        if pl:
            pl["shuffle"] = checked
            # Refresh the ⇌ indicator in the left panel.
            for i in range(self._pl_list.count()):
                it = self._pl_list.item(i)
                if it and it.data(Qt.ItemDataRole.UserRole) == self._selected_id:
                    it.setText(self._pl_display_text(pl))
                    break

    def _on_interval_changed(self, value: int) -> None:
        if not self._core or not self._selected_id:
            return
        try:
            self._core.SetPlaylistInterval(self._selected_id, value)
        except Exception:
            return
        pl = self._get_playlist(self._selected_id)
        if pl:
            pl["interval_minutes"] = value

    def _on_monitor_toggled(self, monitor: str, checked: bool) -> None:
        if not self._core or not self._selected_id:
            return
        try:
            if checked:
                self._core.AssignPlaylistToMonitor(self._selected_id, monitor)
                # Other playlists may have lost this monitor — refresh.
                self._reload_playlists_only()
            else:
                self._core.UnassignPlaylistFromMonitor(self._selected_id, monitor)
        except Exception:
            return
        pl = self._get_playlist(self._selected_id)
        if pl:
            mons = set(pl.get("monitor_assignments", []))
            if checked:
                mons.add(monitor)
            else:
                mons.discard(monitor)
            pl["monitor_assignments"] = list(mons)

    def _reload_playlists_only(self) -> None:
        """Refresh _playlists cache without touching the UI."""
        if not self._core:
            return
        try:
            self._playlists = json.loads(self._core.GetPlaylists())
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Wallpaper list actions
    # ------------------------------------------------------------------

    def _on_wp_row_changed(self, row: int) -> None:
        """Update the duration spinbox when the selected wallpaper changes."""
        if row < 0:
            self._item_dur_spin.blockSignals(True)
            self._item_dur_spin.setValue(0)
            self._item_dur_spin.blockSignals(False)
            self._item_dur_spin.setEnabled(False)
            return
        item = self._wp_list.item(row)
        dur = item.data(Qt.ItemDataRole.UserRole + 1) if item else 0
        self._item_dur_spin.blockSignals(True)
        self._item_dur_spin.setValue(dur or 0)
        self._item_dur_spin.blockSignals(False)
        self._item_dur_spin.setEnabled(True)

    def _on_item_duration_changed(self, value: int) -> None:
        """Push per-item duration change to the service."""
        if not self._core or not self._selected_id:
            return
        row = self._wp_list.currentRow()
        if row < 0:
            return
        item = self._wp_list.item(row)
        if item:
            item.setData(Qt.ItemDataRole.UserRole + 1, value)
        try:
            self._core.SetItemDuration(self._selected_id, row, value)
        except Exception:
            pass
        # Update local cache
        pl = self._get_playlist(self._selected_id)
        if pl:
            durs = pl.setdefault("item_durations", [])
            while len(durs) <= row:
                durs.append(0)
            durs[row] = value

    def _on_wp_reordered(
        self,
        _src_parent: QModelIndex,
        src_first: int,
        src_last: int,
        _dst_parent: QModelIndex,
        dst_row: int,
    ) -> None:
        """Called after internal drag-drop; persist new order to service."""
        if src_first != src_last:
            return  # multi-item moves not supported
        from_index = src_first
        to_index = dst_row - 1 if dst_row > src_first else dst_row
        if from_index == to_index or not self._core or not self._selected_id:
            return
        try:
            self._core.ReorderPlaylist(self._selected_id, from_index, to_index)
        except Exception:
            return
        pl = self._get_playlist(self._selected_id)
        if pl:
            paths = pl.get("wallpaper_paths", [])
            moved = paths.pop(from_index)
            paths.insert(to_index, moved)
            durs = pl.get("item_durations", [])
            if durs and from_index < len(durs):
                d = durs.pop(from_index)
                durs.insert(min(to_index, len(durs)), d)

    def _add_wallpaper(self) -> None:
        if not self._core or not self._selected_id:
            return
        path = QFileDialog.getExistingDirectory(
            self, "Select Wallpaper Directory", str(Path.home())
        )
        if not path:
            return
        try:
            ok = self._core.AddToPlaylist(self._selected_id, path)
        except Exception as exc:
            self._status_label.setText(f"AddToPlaylist error: {exc}")
            return
        if ok:
            self._add_wp_item(path, 0)
            pl = self._get_playlist(self._selected_id)
            if pl:
                pl.setdefault("wallpaper_paths", []).append(path)
                pl.setdefault("item_durations", []).append(0)
                self._update_wp_status(pl)
                self._refresh_pl_count_badge()

    def _remove_wallpaper(self) -> None:
        if not self._core or not self._selected_id:
            return
        row = self._wp_list.currentRow()
        if row < 0:
            return
        try:
            ok = self._core.RemoveFromPlaylist(self._selected_id, row)
        except Exception:
            return
        if ok:
            self._wp_list.takeItem(row)
            pl = self._get_playlist(self._selected_id)
            if pl and 0 <= row < len(pl.get("wallpaper_paths", [])):
                pl["wallpaper_paths"].pop(row)
                durs = pl.get("item_durations", [])
                if row < len(durs):
                    durs.pop(row)
                self._update_wp_status(pl)
                self._refresh_pl_count_badge()

    def _move_up(self) -> None:
        row = self._wp_list.currentRow()
        if row <= 0:
            return
        self._reorder(row, row - 1)

    def _move_down(self) -> None:
        row = self._wp_list.currentRow()
        if row < 0 or row >= self._wp_list.count() - 1:
            return
        self._reorder(row, row + 1)

    def _reorder(self, from_row: int, to_row: int) -> None:
        if not self._core or not self._selected_id:
            return
        try:
            ok = self._core.ReorderPlaylist(self._selected_id, from_row, to_row)
        except Exception:
            return
        if ok:
            item = self._wp_list.takeItem(from_row)
            self._wp_list.insertItem(to_row, item)
            self._wp_list.setCurrentRow(to_row)
            pl = self._get_playlist(self._selected_id)
            if pl:
                paths = pl.get("wallpaper_paths", [])
                moved = paths.pop(from_row)
                paths.insert(to_row, moved)

    def _refresh_pl_count_badge(self) -> None:
        """Update the count badge for the currently selected playlist."""
        if not self._selected_id:
            return
        pl = self._get_playlist(self._selected_id)
        if not pl:
            return
        for i in range(self._pl_list.count()):
            it = self._pl_list.item(i)
            if it and it.data(Qt.ItemDataRole.UserRole) == self._selected_id:
                it.setText(self._pl_display_text(pl))
                break

    # ------------------------------------------------------------------
    # Public API (called from MainWindow)
    # ------------------------------------------------------------------

    def add_item_to_playlist(self, info: WallpaperInfo, playlist_id: str) -> None:
        """Add *info.path* to the playlist identified by *playlist_id*.

        Called by MainWindow after the user picks a playlist from the
        chooser menu shown on right-click → Add to Playlist.
        """
        if not self._core:
            return
        try:
            ok = self._core.AddToPlaylist(playlist_id, info.path)
        except Exception:
            return
        if ok and playlist_id == self._selected_id:
            self._add_wp_item(info.path, 0)
            pl = self._get_playlist(playlist_id)
            if pl:
                pl.setdefault("wallpaper_paths", []).append(info.path)
                pl.setdefault("item_durations", []).append(0)
                self._update_wp_status(pl)
                self._refresh_pl_count_badge()

    def add_item(self, info: WallpaperInfo) -> None:
        """Add *info* to the currently selected playlist (or first playlist).

        Kept for backward compatibility; prefer ``add_item_to_playlist``.
        """
        if not self._core:
            self._status_label.setText("Service unavailable — start mural-core.service")
            return
        target_id = self._selected_id
        if not target_id and self._playlists:
            target_id = self._playlists[0]["id"]
        if not target_id:
            self._status_label.setText("No playlist selected — create one first")
            return
        self.add_item_to_playlist(info, target_id)

    def set_core_proxy(self, proxy: Any) -> None:
        """Update the service proxy and refresh."""
        self._core = proxy
        self._reload()
