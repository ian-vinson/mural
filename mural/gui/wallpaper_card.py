# mural/gui/wallpaper_card.py
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

"""Wallpaper card widget — the individual tile shown in the library grid.

Each card displays:
  - A thumbnail image (or placeholder when none is available)
  - A type badge: VIDEO / SCENE / WEB / IMAGE
  - The wallpaper name, truncated with ellipsis
  - On hover: duration or file size in a translucent overlay

Signals:
  ``selected``       — emitted on single click; carries the :class:`WallpaperInfo`
  ``apply_requested``— emitted on double click; carries the :class:`WallpaperInfo`
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import ClassVar

from PySide6.QtCore import QPoint, QSize, Qt, Signal
from PySide6.QtGui import (
    QColor,
    QFont,
    QFontMetrics,
    QImageReader,
    QPainter,
    QPainterPath,
    QPixmap,
)
from PySide6.QtWidgets import QMenu, QSizePolicy, QWidget

# Card geometry constants
_CARD_W = 200
_CARD_H = 150
_THUMB_H = 115       # height of the thumbnail area
_BADGE_MARGIN = 6    # distance from top-right corner to badge edge
_CORNER_RADIUS = 6

# Badge colours per wallpaper type
_BADGE_COLOURS: dict[str, QColor] = {
    "video": QColor("#2979FF"),
    "scene": QColor("#00C853"),
    "web":   QColor("#FF6D00"),
    "image": QColor("#757575"),
}
_BADGE_DEFAULT = QColor("#455A64")

# Hover overlay
_HOVER_OVERLAY = QColor(0, 0, 0, 130)

# Selection highlight border
_SELECT_COLOUR = QColor("#2979FF")
_SELECT_WIDTH = 2


@dataclass
class WallpaperInfo:
    """Metadata for a single wallpaper entry.

    Attributes:
        name: Display name of the wallpaper.
        path: Absolute path to the wallpaper directory or file.
        type: One of ``"video"``, ``"scene"``, ``"web"``, ``"image"``.
        thumbnail_path: Path to a preview image, or ``None``.
        duration: Human-readable duration string, e.g. ``"0:32"``.
        file_size: Size in bytes; 0 when unknown.
        resolution: Resolution string, e.g. ``"3440x1440"``.
        author: Author / creator name.
        tags: List of tag strings.
        source: ``"local"`` or ``"platform"``.
        description: Long-form description from project.json, or ``""``.
    """

    name: str
    path: str
    type: str = "video"
    thumbnail_path: str | None = None
    duration: str | None = None
    file_size: int = 0
    resolution: str = ""
    author: str = ""
    tags: list[str] = field(default_factory=list)
    source: str = "local"
    description: str = ""

    def type_label(self) -> str:
        """Return the uppercase badge label for this type."""
        return self.type.upper()

    def hover_detail(self) -> str:
        """Return the string shown in the hover overlay (duration or size)."""
        if self.duration:
            return self.duration
        if self.file_size:
            return _format_size(self.file_size)
        return ""


def _format_size(size_bytes: int) -> str:
    """Return a human-readable file size string."""
    for unit in ("B", "KB", "MB", "GB"):
        if size_bytes < 1024:
            return f"{size_bytes:.0f} {unit}"
        size_bytes //= 1024
    return f"{size_bytes:.0f} GB"


# ---------------------------------------------------------------------------
# Wallpaper card widget
# ---------------------------------------------------------------------------

class WallpaperCard(QWidget):
    """A single wallpaper tile for use in the library and platform grids.

    Args:
        info: Metadata for the wallpaper this card represents.
        parent: Optional Qt parent widget.
    """

    # Emitted when the card is clicked (single).
    selected: ClassVar[Signal] = Signal(WallpaperInfo)
    # Emitted when the card is double-clicked.
    apply_requested: ClassVar[Signal] = Signal(WallpaperInfo)
    # Emitted when "Add to Playlist" is chosen from the right-click menu.
    add_to_playlist_requested: ClassVar[Signal] = Signal(WallpaperInfo)

    _PLACEHOLDER: QPixmap | None = None  # shared across all instances

    def __init__(self, info: WallpaperInfo, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._info = info
        self._thumbnail: QPixmap | None = None
        self._hovered = False
        self._selected = False
        self._has_props = self._check_has_props()

        self.setFixedSize(_CARD_W, _CARD_H)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setMouseTracking(True)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)

        self._load_thumbnail()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @property
    def info(self) -> WallpaperInfo:
        """The wallpaper metadata this card represents."""
        return self._info

    def set_selected(self, selected: bool) -> None:
        """Mark this card as selected (draws a highlight border).

        Args:
            selected: ``True`` to show the selection border.
        """
        if self._selected != selected:
            self._selected = selected
            self.update()

    def set_thumbnail(self, pixmap: QPixmap) -> None:
        """Replace the thumbnail with an externally-supplied pixmap.

        Useful for async thumbnail loading from the platform tab.

        Args:
            pixmap: The thumbnail image to display.
        """
        self._thumbnail = pixmap.scaled(
            _CARD_W,
            _THUMB_H,
            Qt.AspectRatioMode.KeepAspectRatioByExpanding,
            Qt.TransformationMode.SmoothTransformation,
        )
        self.update()

    # ------------------------------------------------------------------
    # Painting
    # ------------------------------------------------------------------

    def paintEvent(self, _event) -> None:  # type: ignore[override]
        """Draw the card: thumbnail, overlay, badge, name, selection border."""
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)

        # Rounded clip region for the whole card.
        clip = QPainterPath()
        clip.addRoundedRect(0, 0, _CARD_W, _CARD_H, _CORNER_RADIUS, _CORNER_RADIUS)
        painter.setClipPath(clip)

        self._draw_thumbnail(painter)
        self._draw_name_bar(painter)

        if self._hovered:
            self._draw_hover_overlay(painter)

        self._draw_type_badge(painter)

        if self._has_props:
            self._draw_props_indicator(painter)

        if self._selected:
            self._draw_selection_border(painter)

        painter.end()

    def _draw_thumbnail(self, p: QPainter) -> None:
        """Fill the thumbnail area with the pixmap or a dark placeholder."""
        if self._thumbnail:
            # Centre-crop the pixmap into the thumbnail area.
            src_w = self._thumbnail.width()
            src_h = self._thumbnail.height()
            x_off = max(0, (src_w - _CARD_W) // 2)
            y_off = max(0, (src_h - _THUMB_H) // 2)
            p.drawPixmap(
                0, 0, _CARD_W, _THUMB_H,
                self._thumbnail,
                x_off, y_off, _CARD_W, _THUMB_H,
            )
        else:
            p.fillRect(0, 0, _CARD_W, _THUMB_H, QColor("#1E1E1E"))
            p.setPen(QColor("#444444"))
            p.drawText(
                0, 0, _CARD_W, _THUMB_H,
                Qt.AlignmentFlag.AlignCenter,
                "No preview",
            )

    def _draw_name_bar(self, p: QPainter) -> None:
        """Draw the dark name bar at the bottom of the card."""
        bar_top = _THUMB_H
        bar_h = _CARD_H - _THUMB_H
        p.fillRect(0, bar_top, _CARD_W, bar_h, QColor("#1A1A2E"))

        font = QFont()
        font.setPixelSize(12)
        p.setFont(font)
        p.setPen(QColor("#E0E0E0"))

        fm = QFontMetrics(font)
        text = fm.elidedText(
            self._info.name, Qt.TextElideMode.ElideRight, _CARD_W - 12
        )
        p.drawText(6, bar_top + 4, _CARD_W - 12, bar_h - 8, Qt.AlignmentFlag.AlignVCenter, text)

    def _draw_hover_overlay(self, p: QPainter) -> None:
        """Draw a translucent overlay over the thumbnail showing the detail string."""
        p.fillRect(0, 0, _CARD_W, _THUMB_H, _HOVER_OVERLAY)

        detail = self._info.hover_detail()
        if detail:
            font = QFont()
            font.setPixelSize(14)
            font.setBold(True)
            p.setFont(font)
            p.setPen(Qt.GlobalColor.white)
            p.drawText(
                0, 0, _CARD_W, _THUMB_H,
                Qt.AlignmentFlag.AlignCenter,
                detail,
            )

    def _draw_type_badge(self, p: QPainter) -> None:
        """Draw the coloured type badge in the top-right corner."""
        label = self._info.type_label()
        colour = _BADGE_COLOURS.get(self._info.type.lower(), _BADGE_DEFAULT)

        font = QFont()
        font.setPixelSize(10)
        font.setBold(True)
        p.setFont(font)

        fm = QFontMetrics(font)
        text_w = fm.horizontalAdvance(label)
        pad_x, pad_y = 6, 3
        badge_w = text_w + pad_x * 2
        badge_h = fm.height() + pad_y * 2

        bx = _CARD_W - badge_w - _BADGE_MARGIN
        by = _BADGE_MARGIN

        # Badge background
        badge_path = QPainterPath()
        badge_path.addRoundedRect(bx, by, badge_w, badge_h, 3, 3)
        p.fillPath(badge_path, colour)

        # Badge text
        p.setPen(Qt.GlobalColor.white)
        p.drawText(bx + pad_x, by + pad_y, text_w, fm.height(), 0, label)

    def _draw_props_indicator(self, p: QPainter) -> None:
        """Draw a ⚙ icon in the bottom-left corner for scene wallpapers with properties."""
        font = QFont()
        font.setPixelSize(13)
        p.setFont(font)
        # Semi-transparent dark backing so the icon is readable over any thumbnail.
        p.fillRect(2, _THUMB_H - 20, 20, 18, QColor(0, 0, 0, 120))
        p.setPen(QColor("#FFD54F"))
        p.drawText(2, _THUMB_H - 20, 20, 18, Qt.AlignmentFlag.AlignCenter, "⚙")

    def _draw_selection_border(self, p: QPainter) -> None:
        """Draw a coloured border around the card when selected."""
        pen = p.pen()
        pen.setColor(_SELECT_COLOUR)
        pen.setWidth(_SELECT_WIDTH)
        p.setPen(pen)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRoundedRect(
            _SELECT_WIDTH // 2,
            _SELECT_WIDTH // 2,
            _CARD_W - _SELECT_WIDTH,
            _CARD_H - _SELECT_WIDTH,
            _CORNER_RADIUS,
            _CORNER_RADIUS,
        )

    # ------------------------------------------------------------------
    # Mouse events
    # ------------------------------------------------------------------

    def mousePressEvent(self, event) -> None:  # type: ignore[override]
        if event.button() == Qt.MouseButton.LeftButton:
            self.selected.emit(self._info)
        super().mousePressEvent(event)

    def mouseDoubleClickEvent(self, event) -> None:  # type: ignore[override]
        if event.button() == Qt.MouseButton.LeftButton:
            self.apply_requested.emit(self._info)
        super().mouseDoubleClickEvent(event)

    def enterEvent(self, event) -> None:  # type: ignore[override]
        self._hovered = True
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:  # type: ignore[override]
        self._hovered = False
        self.update()
        super().leaveEvent(event)

    def contextMenuEvent(self, event) -> None:  # type: ignore[override]
        menu = QMenu(self)
        menu.setStyleSheet(
            "QMenu { background: #1E1E2E; color: #e0e0e0; border: 1px solid #333; }"
            "QMenu::item:selected { background: #2979FF; color: #fff; }"
            "QMenu::item { padding: 4px 16px; }"
        )
        add_action = menu.addAction("Add to Playlist")
        chosen = menu.exec(event.globalPos())
        if chosen is add_action:
            self.add_to_playlist_requested.emit(self._info)

    # ------------------------------------------------------------------
    # Thumbnail loading
    # ------------------------------------------------------------------

    def _load_thumbnail(self) -> None:
        """Load the thumbnail image and store a pre-scaled copy for painting."""
        thumb = self._info.thumbnail_path
        if not thumb:
            return
        path = Path(thumb)
        if not path.exists():
            return
        try:
            # QImageReader detects format from file content (magic bytes), not
            # just the extension.  This correctly handles animated GIFs (loads
            # the first frame), JPEG, PNG, and WebP without relying on the
            # extension matching the actual format — common in Steam Workshop.
            reader = QImageReader(str(path))
            reader.setAutoTransform(True)  # honour EXIF rotation for JPEGs
            image = reader.read()
            if not image.isNull():
                px = QPixmap.fromImage(image)
            else:
                # Fall back to the extension-based loader as a last resort.
                px = QPixmap(str(path))
            if px.isNull():
                return
            self._thumbnail = px.scaled(
                _CARD_W,
                _THUMB_H,
                Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                Qt.TransformationMode.SmoothTransformation,
            )
        except Exception:
            pass

    def sizeHint(self) -> QSize:  # type: ignore[override]
        return QSize(_CARD_W, _CARD_H)

    def _check_has_props(self) -> bool:
        """Return True if this is a scene wallpaper with configurable properties."""
        if self._info.type.lower() != "scene":
            return False
        try:
            from mural.utils.properties import has_properties
            return has_properties(self._info.path)
        except Exception:
            return False
