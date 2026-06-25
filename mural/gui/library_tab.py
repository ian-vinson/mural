# mural/gui/library_tab.py
#
# Mural — Animated Wallpaper Platform for Linux
# Copyright (C) 2024  Mural Contributors
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <https://www.gnu.org/licenses/>.

"""Local wallpaper library browser tab.

Scans one or more directories for wallpapers, displays them in a
responsive card grid, and forwards selection / apply signals to the
main window's preview panel.

Scan is performed on a background QThread so the UI remains responsive
for large libraries.  Discovered wallpapers stream in as the scan
progresses via Qt signals.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import ClassVar

from PySide6.QtCore import QSize, Qt, QThread, Signal
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from mural.gui.wallpaper_card import WallpaperCard, WallpaperInfo

# ---------------------------------------------------------------------------
# Wallpaper type detection constants
# ---------------------------------------------------------------------------

_VIDEO_EXTS = {".mp4", ".webm", ".mkv", ".avi", ".mov"}
_IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp"}

# Steam App ID 431960 = Wallpaper Engine
_WORKSHOP_CONTENT_ID = "431960"

_STEAM_ROOTS: tuple[str, ...] = (
    "~/.steam/steam",
    "~/.local/share/Steam",
    "~/.var/app/com.valvesoftware.Steam/.local/share/Steam",
    "~/snap/steam/common/.local/share/Steam",
)

_THUMBNAIL_NAMES = ("preview.jpg", "preview.png", "preview.gif", "thumbnail.jpg")

# Card grid spacing
_CARD_SPACING = 12
_CARD_W = 200
_CARD_H = 150


# ---------------------------------------------------------------------------
# Background scan worker
# ---------------------------------------------------------------------------

class _LibraryScanWorker(QThread):
    """Scans a list of directories for wallpapers on a background thread.

    Emits :attr:`wallpaper_found` for each discovered wallpaper so the
    grid can stream results in as the scan progresses.

    Args:
        directories: Paths to scan recursively for wallpapers.
    """

    wallpaper_found: ClassVar[Signal] = Signal(WallpaperInfo)
    scan_complete: ClassVar[Signal] = Signal(int)   # total count
    scan_error: ClassVar[Signal] = Signal(str)

    def __init__(self, directories: list[Path]) -> None:
        super().__init__()
        self._directories = directories

    def run(self) -> None:
        """Scan directories and emit :attr:`wallpaper_found` for each hit."""
        count = 0
        for directory in self._directories:
            if not directory.exists():
                continue
            try:
                count += self._scan_directory(directory)
            except PermissionError as exc:
                self.scan_error.emit(str(exc))
        self.scan_complete.emit(count)

    def _scan_directory(self, root: Path) -> int:
        """Recursively scan *root*; returns number of wallpapers found."""
        found = 0
        try:
            entries = list(root.iterdir())
        except PermissionError:
            return 0

        for entry in entries:
            if entry.is_file():
                info = _classify_file(entry)
                if info:
                    self.wallpaper_found.emit(info)
                    found += 1
            elif entry.is_dir():
                info = _classify_directory(entry)
                if info:
                    self.wallpaper_found.emit(info)
                    found += 1
                else:
                    # Recurse only one extra level to avoid scanning deep trees.
                    found += self._scan_directory(entry)

        return found


def _classify_file(path: Path) -> WallpaperInfo | None:
    """Return a :class:`WallpaperInfo` if *path* is a supported wallpaper file."""
    ext = path.suffix.lower()
    if ext in _VIDEO_EXTS:
        thumb = _find_sibling_thumbnail(path)
        return WallpaperInfo(
            name=path.stem,
            path=str(path),
            type="video",
            thumbnail_path=thumb,
            file_size=_safe_size(path),
            source="local",
        )
    if ext in _IMAGE_EXTS:
        return WallpaperInfo(
            name=path.stem,
            path=str(path),
            type="image",
            thumbnail_path=str(path),  # image is its own thumbnail
            file_size=_safe_size(path),
            source="local",
        )
    return None


def _classify_directory(path: Path) -> WallpaperInfo | None:
    """Return a :class:`WallpaperInfo` if *path* is a scene or web wallpaper folder."""
    # Scene wallpaper — linux-wallpaperengine format
    project_json = path / "project.json"
    if project_json.exists():
        name, wtype, resolution = _parse_project_json(project_json)
        thumb = _find_directory_thumbnail(path)
        return WallpaperInfo(
            name=name or path.name,
            path=str(path),
            type=wtype,
            thumbnail_path=thumb,
            resolution=resolution,
            source="local",
        )

    # Web wallpaper — folder contains index.html
    index_html = path / "index.html"
    if index_html.exists():
        thumb = _find_directory_thumbnail(path)
        return WallpaperInfo(
            name=path.name,
            path=str(path),
            type="web",
            thumbnail_path=thumb,
            source="local",
        )

    return None


def _parse_project_json(json_path: Path) -> tuple[str, str, str]:
    """Extract name, type, and resolution from a Wallpaper Engine project.json.

    Returns:
        Tuple of (name, type, resolution) — all may be empty strings on failure.
    """
    try:
        data: dict = json.loads(json_path.read_text(encoding="utf-8", errors="replace"))
        name = data.get("title") or data.get("name") or ""
        raw_type = (data.get("type") or "scene").lower()
        wtype = raw_type if raw_type in ("scene", "web", "video", "image") else "scene"
        resolution = ""
        if "width" in data and "height" in data:
            resolution = f"{data['width']}x{data['height']}"
        return name, wtype, resolution
    except Exception:
        return "", "scene", ""


def _find_directory_thumbnail(path: Path) -> str | None:
    """Return the path to a preview image inside *path*, or ``None``."""
    for name in _THUMBNAIL_NAMES:
        candidate = path / name
        if candidate.exists():
            return str(candidate)
    return None


def _find_sibling_thumbnail(video_path: Path) -> str | None:
    """Look for a thumbnail image next to *video_path* with the same stem."""
    parent = video_path.parent
    stem = video_path.stem
    for ext in (".jpg", ".png", ".jpeg", ".webp"):
        candidate = parent / (stem + ext)
        if candidate.exists():
            return str(candidate)
    return None


def _safe_size(path: Path) -> int:
    """Return file size in bytes, or 0 on error."""
    try:
        return path.stat().st_size
    except OSError:
        return 0


def _detect_steam_workshop_paths() -> list[Path]:
    """Return any Steam Workshop wallpaper_engine content directories found."""
    results: list[Path] = []
    for root_str in _STEAM_ROOTS:
        root = Path(root_str).expanduser()
        candidate = root / "steamapps" / "workshop" / "content" / _WORKSHOP_CONTENT_ID
        if candidate.is_dir():
            results.append(candidate)
    return results


# ---------------------------------------------------------------------------
# Responsive card grid
# ---------------------------------------------------------------------------

class _CardGrid(QWidget):
    """A widget that lays WallpaperCards out in a wrapping grid.

    Re-computes the number of columns whenever it is resized so the cards
    always fill the available width without a horizontal scrollbar.

    Args:
        parent: Optional Qt parent.
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._cards: list[WallpaperCard] = []
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(0)
        self._rows: list[QHBoxLayout] = []
        self._current_cols = 0

    def add_card(self, card: WallpaperCard) -> None:
        """Append *card* and re-flow the grid."""
        self._cards.append(card)
        self._relayout()

    def clear_cards(self) -> None:
        """Remove all cards from the grid."""
        for card in self._cards:
            card.setParent(None)  # type: ignore[arg-type]
            card.deleteLater()
        self._cards.clear()
        # Clear row widgets
        while self._layout.count():
            item = self._layout.takeAt(0)
            if item and item.widget():
                item.widget().deleteLater()
        self._rows.clear()

    def resizeEvent(self, event) -> None:  # type: ignore[override]
        super().resizeEvent(event)
        self._relayout()

    def _relayout(self) -> None:
        """Re-flow cards into rows based on the current widget width."""
        available = max(self.width(), _CARD_W + _CARD_SPACING)
        cols = max(1, (available + _CARD_SPACING) // (_CARD_W + _CARD_SPACING))

        if cols == self._current_cols and len(self._cards) == sum(
            row.count() for row in self._rows
        ):
            return  # nothing changed

        self._current_cols = cols

        # Remove existing row widgets from layout.
        while self._layout.count():
            item = self._layout.takeAt(0)
            if item and item.widget():
                item.widget().setParent(None)  # type: ignore[arg-type]

        self._rows.clear()

        # Re-add all cards in new rows.
        row_widget: QWidget | None = None
        row_layout: QHBoxLayout | None = None
        for i, card in enumerate(self._cards):
            if i % cols == 0:
                row_widget = QWidget()
                row_layout = QHBoxLayout(row_widget)
                row_layout.setContentsMargins(0, 0, 0, 0)
                row_layout.setSpacing(_CARD_SPACING)
                row_layout.setAlignment(Qt.AlignmentFlag.AlignLeft)
                self._layout.addWidget(row_widget)
                self._rows.append(row_layout)
            row_layout.addWidget(card)  # type: ignore[union-attr]

        self._layout.addStretch()

        # Recompute preferred height.
        n_rows = (len(self._cards) + cols - 1) // cols
        total_h = n_rows * _CARD_H + (n_rows - 1) * _CARD_SPACING
        self.setMinimumHeight(max(total_h, 0))


# ---------------------------------------------------------------------------
# Library tab
# ---------------------------------------------------------------------------

class LibraryTab(QWidget):
    """The local wallpaper library browser tab.

    Emits signals that the main window connects to its preview panel.

    Args:
        parent: Optional Qt parent widget.
    """

    # Forwarded from individual WallpaperCards.
    wallpaper_selected: ClassVar[Signal] = Signal(WallpaperInfo)
    wallpaper_apply_requested: ClassVar[Signal] = Signal(WallpaperInfo)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._all_infos: list[WallpaperInfo] = []
        self._visible_cards: list[WallpaperCard] = []
        self._selected_card: WallpaperCard | None = None
        self._active_filter = "all"
        self._scan_worker: _LibraryScanWorker | None = None
        self._extra_dirs: list[Path] = []

        self._build_ui()
        self._start_scan()

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(6)

        root.addLayout(self._build_toolbar())

        # Progress bar (hidden after scan completes)
        self._progress = QProgressBar()
        self._progress.setMaximum(0)  # indeterminate
        self._progress.setFixedHeight(4)
        self._progress.setTextVisible(False)
        root.addWidget(self._progress)

        # Scrollable card grid
        self._grid = _CardGrid()
        self._grid.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
        )

        scroll = QScrollArea()
        scroll.setWidget(self._grid)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        root.addWidget(scroll, 1)

        # Status bar
        self._status_label = QLabel("Scanning…")
        self._status_label.setStyleSheet("color: #888; font-size: 11px;")
        root.addWidget(self._status_label)

    def _build_toolbar(self) -> QHBoxLayout:
        toolbar = QHBoxLayout()
        toolbar.setSpacing(6)

        # Search box
        self._search = QLineEdit()
        self._search.setPlaceholderText("Search wallpapers…")
        self._search.setClearButtonEnabled(True)
        self._search.setFixedHeight(30)
        self._search.textChanged.connect(self._apply_filter)
        toolbar.addWidget(self._search, 1)

        # Type filter buttons
        for label, key in (("All", "all"), ("Video", "video"), ("Scene", "scene"),
                           ("Web", "web"), ("Image", "image")):
            btn = QPushButton(label)
            btn.setCheckable(True)
            btn.setChecked(key == "all")
            btn.setFixedHeight(30)
            btn.clicked.connect(lambda checked, k=key: self._set_type_filter(k))
            btn.setProperty("filterKey", key)
            setattr(self, f"_btn_{key}", btn)
            toolbar.addWidget(btn)

        toolbar.addSpacing(4)

        # Add folder button
        add_btn = QPushButton("+ Add Folder")
        add_btn.setFixedHeight(30)
        add_btn.clicked.connect(self._add_folder)
        toolbar.addWidget(add_btn)

        return toolbar

    # ------------------------------------------------------------------
    # Scanning
    # ------------------------------------------------------------------

    def _start_scan(self) -> None:
        """Begin a background scan of default + user-added directories."""
        dirs = list(_detect_steam_workshop_paths()) + self._extra_dirs
        if not dirs:
            self._status_label.setText(
                "No wallpaper directories found. Click '+ Add Folder' to add one."
            )
            self._progress.hide()
            return

        if self._scan_worker and self._scan_worker.isRunning():
            self._scan_worker.quit()
            self._scan_worker.wait()

        self._grid.clear_cards()
        self._all_infos.clear()
        self._visible_cards.clear()
        self._selected_card = None

        self._progress.show()
        self._status_label.setText("Scanning…")

        self._scan_worker = _LibraryScanWorker(dirs)
        self._scan_worker.wallpaper_found.connect(self._on_wallpaper_found)
        self._scan_worker.scan_complete.connect(self._on_scan_complete)
        self._scan_worker.scan_error.connect(
            lambda msg: self._status_label.setText(f"Scan error: {msg}")
        )
        self._scan_worker.start()

    def _on_wallpaper_found(self, info: WallpaperInfo) -> None:
        """Add a discovered wallpaper to the grid (called from main thread via signal)."""
        self._all_infos.append(info)
        if self._matches_filter(info):
            self._add_card(info)

    def _on_scan_complete(self, total: int) -> None:
        """Hide the progress bar and update the status label."""
        self._progress.hide()
        self._update_status()

    # ------------------------------------------------------------------
    # Filtering
    # ------------------------------------------------------------------

    def _set_type_filter(self, key: str) -> None:
        """Switch the active type filter and refresh the grid."""
        self._active_filter = key
        for k in ("all", "video", "scene", "web", "image"):
            btn: QPushButton = getattr(self, f"_btn_{k}")
            btn.setChecked(k == key)
        self._apply_filter()

    def _apply_filter(self) -> None:
        """Re-populate the grid based on the current filter and search text."""
        self._grid.clear_cards()
        self._visible_cards.clear()
        self._selected_card = None

        for info in self._all_infos:
            if self._matches_filter(info):
                self._add_card(info)

        self._update_status()

    def _matches_filter(self, info: WallpaperInfo) -> bool:
        """Return ``True`` if *info* passes both the type filter and search text."""
        if self._active_filter != "all" and info.type != self._active_filter:
            return False
        query = self._search.text().strip().lower()
        if query and query not in info.name.lower():
            return False
        return True

    # ------------------------------------------------------------------
    # Card management
    # ------------------------------------------------------------------

    def _add_card(self, info: WallpaperInfo) -> None:
        """Create a card for *info* and add it to the grid."""
        card = WallpaperCard(info)
        card.selected.connect(self._on_card_selected)
        card.apply_requested.connect(self.wallpaper_apply_requested)
        self._grid.add_card(card)
        self._visible_cards.append(card)

    def _on_card_selected(self, info: WallpaperInfo) -> None:
        """Update selection state and forward the signal."""
        for card in self._visible_cards:
            card.set_selected(card.info is info)
            if card.info is info:
                self._selected_card = card
        self.wallpaper_selected.emit(info)

    # ------------------------------------------------------------------
    # Folder management
    # ------------------------------------------------------------------

    def _add_folder(self) -> None:
        """Open a directory picker and add the chosen folder to the scan list."""
        directory = QFileDialog.getExistingDirectory(
            self,
            "Add Wallpaper Folder",
            str(Path.home()),
        )
        if directory:
            path = Path(directory)
            if path not in self._extra_dirs:
                self._extra_dirs.append(path)
                self._start_scan()

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _update_status(self) -> None:
        shown = len(self._visible_cards)
        total = len(self._all_infos)
        if shown == total:
            self._status_label.setText(f"{total} wallpaper{'s' if total != 1 else ''}")
        else:
            self._status_label.setText(f"{shown} of {total} wallpapers")

    def refresh(self) -> None:
        """Re-scan all directories (called by the main window's refresh action)."""
        self._start_scan()
