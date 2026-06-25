# mural/gui/playlist_tab.py
#
# Mural — Animated Wallpaper Platform for Linux
# Copyright (C) 2024  Mural Contributors
# GPL v3 — see LICENSE

"""Playlist tab — ordered wallpaper rotation with drag-and-drop reordering.

Users add wallpapers via right-click → Add to Playlist in the Library tab.
Drag rows to reorder.  Toggle Shuffle to randomise within the list.
When the playlist is empty the auto-rotate timer picks randomly from the
full library instead.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, ClassVar

from PySide6.QtCore import QByteArray, QSize, Qt
from PySide6.QtGui import QFont, QIcon, QImageReader, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from mural.gui.wallpaper_card import WallpaperInfo

_THUMB_W = 64
_THUMB_H = 48

_THUMBNAIL_NAMES = ("preview.jpg", "preview.png", "preview.gif",
                    "preview.webp", "thumbnail.jpg", "thumbnail.png")


def _load_icon(wallpaper_path: str) -> QIcon | None:
    """Load a small thumbnail icon from a wallpaper directory."""
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
    """Playlist management panel.

    Args:
        core_proxy: dasbus proxy for ``com.mural.Core``, or ``None``.
        parent: Optional Qt parent.
    """

    def __init__(self, core_proxy: Any | None = None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._core = core_proxy
        self._build_ui()
        self._reload()

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 12, 16, 12)
        layout.setSpacing(10)

        # Toolbar
        toolbar = QHBoxLayout()
        self._shuffle_chk = QCheckBox("Shuffle")
        self._shuffle_chk.setToolTip("Play items in random order instead of top-to-bottom")
        self._shuffle_chk.toggled.connect(self._on_shuffle_toggled)
        toolbar.addWidget(self._shuffle_chk)

        toolbar.addStretch()

        remove_btn = QPushButton("Remove Selected")
        remove_btn.setFixedHeight(28)
        remove_btn.clicked.connect(self._remove_selected)
        toolbar.addWidget(remove_btn)

        clear_btn = QPushButton("Clear All")
        clear_btn.setFixedHeight(28)
        clear_btn.clicked.connect(self._clear_all)
        toolbar.addWidget(clear_btn)

        layout.addLayout(toolbar)

        # Status label
        self._status_label = QLabel()
        self._status_label.setStyleSheet("color: #888; font-size: 11px;")
        layout.addWidget(self._status_label)

        # List widget
        self._list = QListWidget()
        self._list.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        self._list.setDefaultDropAction(Qt.DropAction.MoveAction)
        self._list.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self._list.setIconSize(QSize(_THUMB_W, _THUMB_H))
        self._list.setSpacing(2)
        self._list.setAlternatingRowColors(True)
        self._list.setStyleSheet(
            "QListWidget { background: #141421; border: 1px solid #2a2a2a; }"
            "QListWidget::item { color: #e0e0e0; padding: 4px 8px; height: 56px; }"
            "QListWidget::item:selected { background: #2979FF; color: #fff; }"
            "QListWidget::item:alternate { background: #1A1A2E; }"
        )
        self._list.model().rowsMoved.connect(self._on_rows_moved)
        layout.addWidget(self._list, 1)

        # Hint
        hint = QLabel("Right-click any wallpaper in the Library tab to add it here.\n"
                      "Drag rows to reorder.")
        hint.setStyleSheet("color: #555; font-size: 11px;")
        hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(hint)

    # ------------------------------------------------------------------
    # Data loading / saving
    # ------------------------------------------------------------------

    def _reload(self) -> None:
        """Fetch the current playlist from the service and repopulate the list."""
        items: list[str] = []
        shuffle = False
        if self._core:
            try:
                items = list(self._core.GetPlaylist())
                shuffle = bool(self._core.GetPlaylistShuffle())
            except Exception:
                pass

        self._list.blockSignals(True)
        self._list.clear()
        for path in items:
            self._add_list_item(path)
        self._list.blockSignals(False)

        self._shuffle_chk.blockSignals(True)
        self._shuffle_chk.setChecked(shuffle)
        self._shuffle_chk.blockSignals(False)

        self._update_status(items)

    def _add_list_item(self, path: str) -> None:
        name = Path(path).name
        icon = _load_icon(path)
        item = QListWidgetItem(name)
        item.setData(Qt.ItemDataRole.UserRole, path)
        item.setToolTip(path)
        if icon:
            item.setIcon(icon)
        self._list.addItem(item)

    def _current_paths(self) -> list[str]:
        return [
            self._list.item(i).data(Qt.ItemDataRole.UserRole)
            for i in range(self._list.count())
        ]

    def _push_to_service(self) -> None:
        """Send the current list order to the Core Service."""
        if self._core:
            try:
                self._core.SetPlaylist(self._current_paths())
            except Exception:
                pass
        self._update_status(self._current_paths())

    def _update_status(self, items: list[str]) -> None:
        n = len(items)
        if n == 0:
            self._status_label.setText(
                "No playlist — auto-rotate will pick randomly from your library"
            )
        else:
            mode = "shuffle" if self._shuffle_chk.isChecked() else "ordered"
            self._status_label.setText(f"{n} item{'s' if n != 1 else ''} · {mode}")

    # ------------------------------------------------------------------
    # Public API (called from MainWindow)
    # ------------------------------------------------------------------

    def add_item(self, info: WallpaperInfo) -> None:
        """Add *info* to the playlist if it is not already present."""
        path = info.path
        existing = self._current_paths()
        if path in existing:
            return
        if self._core:
            try:
                self._core.AddToPlaylist(path)
            except Exception:
                pass
        self._add_list_item(path)
        self._update_status(self._current_paths())

    def set_core_proxy(self, proxy: Any) -> None:
        """Update the service proxy and refresh."""
        self._core = proxy
        self._reload()

    # ------------------------------------------------------------------
    # Slot handlers
    # ------------------------------------------------------------------

    def _on_shuffle_toggled(self, checked: bool) -> None:
        if self._core:
            try:
                self._core.SetPlaylistShuffle(checked)
            except Exception:
                pass
        self._update_status(self._current_paths())

    def _on_rows_moved(self, *_args) -> None:
        self._push_to_service()

    def _remove_selected(self) -> None:
        for item in self._list.selectedItems():
            self._list.takeItem(self._list.row(item))
        self._push_to_service()

    def _clear_all(self) -> None:
        self._list.clear()
        self._push_to_service()
