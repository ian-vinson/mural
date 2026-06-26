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

from PySide6.QtCore import QSize, Qt, QThread, QTimer, Signal
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

from shiboken6 import isValid

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


def _get_pkg_version(wallpaper_path: Path) -> str | None:
    """Read the PKGV version string from scene.pkg, or None if absent/unreadable."""
    pkg_file = wallpaper_path / "scene.pkg"
    if not pkg_file.exists():
        return None
    try:
        with open(pkg_file, "rb") as f:
            f.seek(8)
            raw = f.read(8)
            version = raw.split(b"\x00")[0].decode("ascii", errors="ignore")
            return version if version.startswith("PKGV") else None
    except OSError:
        return None


def _pkg_compatibility_warning(version: str | None) -> str:
    """Return a warning string if *version* exceeds the safe range, else ''."""
    if version is None:
        return ""
    try:
        ver_num = int(version.replace("PKGV", ""))
    except ValueError:
        return ""
    if ver_num > 8:
        return (
            f"This wallpaper uses scene package format {version} which may not "
            f"render correctly with your version of linux-wallpaperengine. "
            f"Video and web wallpapers are unaffected."
        )
    return ""


def _check_aspect_mismatch(resolution: str) -> bool:
    """Return True if width/height < 2.0 — may not fill an ultrawide screen correctly."""
    if not resolution or "x" not in resolution.lower():
        return False
    try:
        w, h = (int(v) for v in resolution.lower().split("x", 1))
        return h > 0 and w / h < 2.0
    except (ValueError, TypeError):
        return False


def _classify_directory(path: Path) -> WallpaperInfo | None:
    """Return a :class:`WallpaperInfo` if *path* is a scene or web wallpaper folder."""
    # Scene wallpaper — linux-wallpaperengine format
    project_json = path / "project.json"
    if project_json.exists():
        meta = _parse_project_json(project_json)
        # Prefer the preview filename from project.json, fall back to known names.
        thumb: str | None = None
        if meta["preview"]:
            candidate = path / meta["preview"]
            # Only accept the project.json preview if it is an image file;
            # some wallpapers set the preview to a video file which QPixmap
            # cannot load, and we don't want that to block the image fallback.
            if candidate.exists() and candidate.suffix.lower() in _IMAGE_EXTS:
                thumb = str(candidate)
        if thumb is None:
            thumb = _find_directory_thumbnail(path)
        pkg_ver = _get_pkg_version(path)
        return WallpaperInfo(
            name=meta["name"] or path.name,
            path=str(path),
            type=meta["type"],
            thumbnail_path=thumb,
            resolution=meta["resolution"],
            author=meta["author"],
            tags=meta["tags"],
            description=meta["description"],
            file_size=_dir_size(path),
            source="local",
            aspect_mismatch=_check_aspect_mismatch(meta["resolution"]),
            compatibility_warning=_pkg_compatibility_warning(pkg_ver),
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
            file_size=_dir_size(path),
            source="local",
        )

    return None


def _parse_project_json(json_path: Path) -> dict:
    """Extract metadata from a Wallpaper Engine project.json.

    Returns a dict with keys: name, type, resolution, author, tags,
    description, preview.  All values default to safe empty values on error.
    """
    empty: dict = {
        "name": "", "type": "scene", "resolution": "",
        "author": "", "tags": [], "description": "", "preview": "",
    }
    try:
        data: dict = json.loads(json_path.read_text(encoding="utf-8", errors="replace"))
        name = data.get("title") or data.get("name") or ""
        raw_type = (data.get("type") or "scene").lower()
        wtype = raw_type if raw_type in ("scene", "web", "video", "image") else "scene"

        resolution = ""
        if "width" in data and "height" in data:
            resolution = f"{data['width']}x{data['height']}"

        raw_tags = data.get("tags") or []
        if isinstance(raw_tags, list):
            tags = [str(t) for t in raw_tags if t]
        elif isinstance(raw_tags, str):
            tags = [t.strip() for t in raw_tags.split(",") if t.strip()]
        else:
            tags = []

        return {
            "name": name,
            "type": wtype,
            "resolution": resolution,
            "author": data.get("author") or data.get("workshopid") or "",
            "tags": tags,
            "description": data.get("description") or "",
            "preview": data.get("preview") or "",
        }
    except Exception:
        return empty


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


def _dir_size(path: Path) -> int:
    """Return total size of all files directly inside *path* (non-recursive), or 0."""
    total = 0
    try:
        for entry in path.iterdir():
            if entry.is_file():
                total += _safe_size(entry)
    except OSError:
        pass
    return total


def _detect_steam_workshop_paths() -> list[Path]:
    """Return Steam Workshop wallpaper_engine content directories, deduplicated by real path.

    Steam installs often create ``~/.steam/steam`` as a symlink to
    ``~/.local/share/Steam``, which would otherwise produce duplicate entries.
    """
    seen_real: set[Path] = set()
    results: list[Path] = []
    for root_str in _STEAM_ROOTS:
        root = Path(root_str).expanduser()
        candidate = root / "steamapps" / "workshop" / "content" / _WORKSHOP_CONTENT_ID
        if not candidate.is_dir():
            continue
        try:
            real = candidate.resolve()
        except OSError:
            real = candidate
        if real not in seen_real:
            seen_real.add(real)
            results.append(candidate)
    return results


# ---------------------------------------------------------------------------
# Background thumbnail generation worker
# ---------------------------------------------------------------------------

class _ThumbnailGenWorker(QThread):
    """Generates thumbnails for wallpapers that have no preview image.

    Runs lwe --screenshot in a background thread for each candidate.
    Emits ``thumbnail_ready(wallpaper_path, thumbnail_path)`` on success.
    """

    thumbnail_ready: ClassVar[Signal] = Signal(str, str)

    def __init__(self, candidates: list[tuple[str, str]], lwe_binary: str) -> None:
        super().__init__()
        self._candidates = candidates
        self._lwe_binary = lwe_binary

    def run(self) -> None:
        from mural.utils.thumbnail_gen import generate_thumbnail
        for wallpaper_path, out_path in self._candidates:
            if generate_thumbnail(self._lwe_binary, wallpaper_path, out_path):
                self.thumbnail_ready.emit(wallpaper_path, out_path)


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
        self._current_cols = 0

        # Single timer coalesces rapid add_card() calls and resize events.
        self._relayout_timer = QTimer(self)
        self._relayout_timer.setSingleShot(True)
        self._relayout_timer.setInterval(50)
        self._relayout_timer.timeout.connect(self._relayout)

    def add_card(self, card: WallpaperCard) -> None:
        """Append *card* and schedule a deferred re-flow."""
        card.setParent(self)
        card.hide()  # hidden until _relayout() places it
        self._cards.append(card)
        self._relayout_timer.start()

    def clear_cards(self) -> None:
        """Remove all cards from the grid."""
        self._relayout_timer.stop()
        for card in self._cards:
            card.setParent(None)  # type: ignore[arg-type]
            card.deleteLater()
        self._cards.clear()
        self._destroy_rows()
        self._current_cols = 0

    def schedule_relayout(self) -> None:
        """Request a deferred re-flow (safe to call from outside the grid)."""
        self._relayout_timer.stop()
        self._relayout_timer.start()

    def resizeEvent(self, event) -> None:  # type: ignore[override]
        super().resizeEvent(event)
        self._relayout_timer.stop()
        self._relayout_timer.start()

    def showEvent(self, event) -> None:  # type: ignore[override]
        super().showEvent(event)
        # Defer one tick so the scroll-area has assigned our final width.
        QTimer.singleShot(0, self._relayout)

    def _destroy_rows(self) -> None:
        """Remove all row widgets from the layout and schedule their deletion.

        Cards must already be reparented away from the row widgets before
        this is called, otherwise Qt will delete them as children.
        """
        while self._layout.count():
            item = self._layout.takeAt(0)
            w = item.widget() if item else None
            if w:
                w.deleteLater()

    def _relayout(self) -> None:
        """Re-flow cards into rows based on the current available width.

        When the grid is inside a QScrollArea the parent is the viewport.
        We read the *viewport* width rather than self.width() because the
        grid's own size may still reflect the previous (wider) layout while
        its row-widgets impose a large implicit minimum width — the scroll
        area can't shrink us below that, so our width doesn't update until
        after we rebuild the rows.
        """
        vp = self.parentWidget()
        raw_w = vp.width() if vp is not None else self.width()
        available = max(raw_w, _CARD_W + _CARD_SPACING)
        cols = max(2, (available + _CARD_SPACING) // (_CARD_W + _CARD_SPACING))
        self._current_cols = cols

        # Reparent every card to self BEFORE destroying row widgets.
        # addWidget() transfers Qt parent ownership to the row; if we delete
        # the row first the cards go with it (shiboken crash).
        for card in self._cards:
            if isValid(card):
                card.setParent(self)  # type: ignore[arg-type]

        self._destroy_rows()

        # Rebuild rows with the correct column count.
        row_widget: QWidget | None = None
        row_layout: QHBoxLayout | None = None
        for i, card in enumerate(self._cards):
            if i % cols == 0:
                row_widget = QWidget(self)
                row_layout = QHBoxLayout(row_widget)
                row_layout.setContentsMargins(0, 0, 0, 0)
                row_layout.setSpacing(_CARD_SPACING)
                row_layout.setAlignment(Qt.AlignmentFlag.AlignLeft)
                self._layout.addWidget(row_widget)
            if isValid(card):
                row_layout.addWidget(card)  # type: ignore[union-attr]
                card.show()

        self._layout.addStretch()

        n_rows = (len(self._cards) + cols - 1) // cols if self._cards else 0
        total_h = n_rows * _CARD_H + max(0, n_rows - 1) * _CARD_SPACING
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
    add_to_playlist_requested: ClassVar[Signal] = Signal(WallpaperInfo)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._all_infos: list[WallpaperInfo] = []
        self._visible_cards: list[WallpaperCard] = []
        self._selected_card: WallpaperCard | None = None
        self._active_filter = "all"
        self._active_tags: set[str] = set()
        self._tag_buttons: dict[str, QPushButton] = {}
        self._scan_worker: _LibraryScanWorker | None = None
        self._thumb_worker: _ThumbnailGenWorker | None = None
        self._extra_dirs: list[Path] = []
        self._scanning: bool = False
        self._seen_paths: set[str] = set()
        self._seen_real_paths: set[str] = set()
        self._path_to_card: dict[str, WallpaperCard] = {}

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
        root.addWidget(self._build_tag_row_widget())

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

    def _build_tag_row_widget(self) -> QWidget:
        """Build the tag-chip filter row (hidden until the scan finds tags)."""
        self._tag_row_widget = QWidget()
        outer = QHBoxLayout(self._tag_row_widget)
        outer.setContentsMargins(0, 2, 0, 0)
        outer.setSpacing(6)

        # Inner chip container — replaced wholesale by _rebuild_tag_row().
        self._chips_widget = QWidget()
        self._chips_layout = QHBoxLayout(self._chips_widget)
        self._chips_layout.setContentsMargins(0, 0, 0, 0)
        self._chips_layout.setSpacing(4)
        outer.addWidget(self._chips_widget)

        outer.addStretch()

        self._clear_btn = QPushButton("✕ Clear filters")
        self._clear_btn.setFlat(True)
        self._clear_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._clear_btn.setStyleSheet(
            "QPushButton { color: #888; font-size: 11px; padding: 0 4px; }"
            "QPushButton:hover { color: #ccc; }"
        )
        self._clear_btn.clicked.connect(self._clear_filters)
        self._clear_btn.hide()
        outer.addWidget(self._clear_btn)

        self._tag_row_widget.hide()
        return self._tag_row_widget

    # ------------------------------------------------------------------
    # Scanning
    # ------------------------------------------------------------------

    def _start_scan(self) -> None:
        """Begin a background scan of default + user-added directories."""
        if self._scanning:
            return
        seen_dirs: set[str] = set()
        dirs: list[Path] = []
        for d in list(_detect_steam_workshop_paths()) + self._extra_dirs:
            key = str(d)
            if key not in seen_dirs:
                seen_dirs.add(key)
                dirs.append(d)
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
        self._path_to_card.clear()
        self._selected_card = None
        self._active_tags.clear()
        self._tag_buttons.clear()
        self._tag_row_widget.hide()
        self._clear_btn.hide()
        self._seen_paths.clear()
        self._seen_real_paths.clear()
        self._scanning = True

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
        if info.path in self._seen_paths:
            return
        try:
            real_path = str(Path(info.path).resolve())
        except OSError:
            real_path = info.path
        if real_path in self._seen_real_paths:
            return
        self._seen_paths.add(info.path)
        self._seen_real_paths.add(real_path)
        self._all_infos.append(info)
        if self._matches_filter(info):
            self._add_card(info)

    def _on_scan_complete(self, total: int) -> None:
        """Hide the progress bar, rebuild tag chips, and update the status label."""
        self._scanning = False
        self._progress.hide()
        self._rebuild_tag_row()
        self._update_status()
        self._start_thumbnail_generation()

    def _start_thumbnail_generation(self) -> None:
        """Start a background thread to generate thumbnails for wallpapers that lack one."""
        from mural.backend.discovery import find_lwe_binary
        from mural.utils.thumbnail_gen import thumbnail_cache_path

        binary = find_lwe_binary()
        if not binary:
            return

        candidates: list[tuple[str, str]] = []
        for info in self._all_infos:
            if info.thumbnail_path:
                continue
            out = thumbnail_cache_path(info.path)
            if not out.exists():
                candidates.append((info.path, str(out)))

        if not candidates:
            return

        if self._thumb_worker and self._thumb_worker.isRunning():
            self._thumb_worker.quit()

        self._thumb_worker = _ThumbnailGenWorker(candidates, str(binary))
        self._thumb_worker.thumbnail_ready.connect(self._on_thumbnail_ready)
        self._thumb_worker.start(priority=self._thumb_worker.Priority.LowestPriority)

    def _on_thumbnail_ready(self, wallpaper_path: str, thumbnail_path: str) -> None:
        """Update the card for *wallpaper_path* with the newly generated thumbnail."""
        card = self._path_to_card.get(wallpaper_path)
        if card is None:
            return
        from PySide6.QtGui import QPixmap
        px = QPixmap(thumbnail_path)
        if not px.isNull():
            card.set_thumbnail(px)
        card.info.thumbnail_path = thumbnail_path

    # ------------------------------------------------------------------
    # Filtering
    # ------------------------------------------------------------------

    # Wallpaper Engine tags shown first when present in the library.
    _PRIORITY_TAGS: tuple[str, ...] = (
        "Anime", "Gaming", "Nature", "Abstract", "Cyberpunk",
        "Relaxing", "Music", "Fantasy", "Dark", "Cute",
    )

    def _rebuild_tag_row(self) -> None:
        """Repopulate the tag chip row from the current library."""
        # Count how many wallpapers carry each tag.
        counts: dict[str, int] = {}
        for info in self._all_infos:
            for tag in info.tags:
                if tag:
                    counts[tag] = counts.get(tag, 0) + 1

        # Only show tags that appear on 2+ wallpapers.
        eligible = {t: c for t, c in counts.items() if c >= 2}
        if not eligible:
            self._tag_row_widget.hide()
            return

        def _sort_key(tag: str) -> tuple[int, int, str]:
            try:
                pri = self._PRIORITY_TAGS.index(tag)
            except ValueError:
                pri = len(self._PRIORITY_TAGS)
            return (pri, -eligible[tag], tag.lower())

        chosen = sorted(eligible, key=_sort_key)[:8]

        # Rebuild the chips widget from scratch.
        self._tag_buttons = {}
        while self._chips_layout.count():
            item = self._chips_layout.takeAt(0)
            if item and item.widget():
                item.widget().deleteLater()

        for tag in chosen:
            btn = QPushButton(tag)
            btn.setCheckable(True)
            btn.setChecked(tag in self._active_tags)
            btn.setFixedHeight(24)
            btn.setStyleSheet(
                "QPushButton {"
                "  background:#2a2a2a; border:1px solid #444;"
                "  border-radius:10px; color:#bbb; padding:0 8px;"
                "  font-size:11px;"
                "}"
                "QPushButton:checked {"
                "  background:#2979FF; border-color:#2979FF; color:#fff;"
                "}"
                "QPushButton:hover:!checked { border-color:#666; color:#eee; }"
            )
            btn.clicked.connect(lambda _chk, t=tag: self._toggle_tag(t))
            self._chips_layout.addWidget(btn)
            self._tag_buttons[tag] = btn

        self._tag_row_widget.show()
        self._update_clear_btn()

    def _toggle_tag(self, tag: str) -> None:
        """Toggle *tag* in the active-tag set and re-filter."""
        if tag in self._active_tags:
            self._active_tags.discard(tag)
        else:
            self._active_tags.add(tag)
        # Sync button checked state in case the click already toggled it.
        if tag in self._tag_buttons:
            self._tag_buttons[tag].setChecked(tag in self._active_tags)
        self._update_clear_btn()
        self._apply_filter()

    def _update_clear_btn(self) -> None:
        """Show 'Clear filters' when any filter is active."""
        active = (
            self._active_filter != "all"
            or bool(self._active_tags)
            or bool(self._search.text().strip())
        )
        self._clear_btn.setVisible(active)

    def _clear_filters(self) -> None:
        """Reset type filter, tag filter, and search box to defaults."""
        self._active_tags.clear()
        for btn in self._tag_buttons.values():
            btn.setChecked(False)
        self._search.blockSignals(True)
        self._search.clear()
        self._search.blockSignals(False)
        self._set_type_filter("all")  # calls _apply_filter internally

    def _set_type_filter(self, key: str) -> None:
        """Switch the active type filter and refresh the grid."""
        self._active_filter = key
        for k in ("all", "video", "scene", "web", "image"):
            btn: QPushButton = getattr(self, f"_btn_{k}")
            btn.setChecked(k == key)
        self._apply_filter()

    def _apply_filter(self) -> None:
        """Re-populate the grid based on all active filters."""
        self._update_clear_btn()
        self._grid.clear_cards()
        self._visible_cards.clear()
        self._selected_card = None

        for info in self._all_infos:
            if self._matches_filter(info):
                self._add_card(info)

        self._update_status()

    def _matches_filter(self, info: WallpaperInfo) -> bool:
        """Return ``True`` if *info* passes type, tag, and search filters."""
        # Type filter
        if self._active_filter != "all" and info.type != self._active_filter:
            return False
        # Tag filter — AND: wallpaper must carry every selected tag.
        if self._active_tags:
            card_tags = {t.lower() for t in info.tags}
            if not all(t.lower() in card_tags for t in self._active_tags):
                return False
        # Search text
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
        card.add_to_playlist_requested.connect(self.add_to_playlist_requested)
        self._grid.add_card(card)
        self._visible_cards.append(card)
        self._path_to_card[info.path] = card

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

    def resizeEvent(self, event) -> None:  # type: ignore[override]
        super().resizeEvent(event)
        # Trigger grid relayout when the outer container changes width (e.g. splitter
        # moved, window un-maximized).  The grid itself may not receive a resize event
        # if its current width exceeds the viewport width due to row-widget constraints.
        self._grid.schedule_relayout()

    def refresh(self) -> None:
        """Re-scan all directories (called by the main window's refresh action)."""
        self._start_scan()

    def refresh_card_indicators(self, path: str) -> None:
        """Recompute dynamic indicators for the card at *path* after scaling changes."""
        card = self._path_to_card.get(path)
        if card is not None:
            card.refresh_indicators()
