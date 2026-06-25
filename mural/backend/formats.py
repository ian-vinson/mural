# mural/backend/formats.py
#
# Mural — Animated Wallpaper Platform for Linux
# Copyright (C) 2024  Mural Contributors
# GPL v3 — see LICENSE

"""Wallpaper type detection and format utilities.

Classifies wallpaper paths as ``"video"``, ``"scene"``, ``"web"``,
``"image"``, or ``"unknown"`` by inspecting the file extension and
directory contents — no subprocess calls.

Used by :class:`~mural.backend.runner.BackendRunner` to decide which
arguments to pass to linux-wallpaperengine, and by the library scanner
to badge cards correctly.
"""

from __future__ import annotations

import json
from enum import Enum
from pathlib import Path


class WallpaperType(str, Enum):
    """Enumeration of supported wallpaper formats."""

    VIDEO   = "video"
    SCENE   = "scene"
    WEB     = "web"
    IMAGE   = "image"
    UNKNOWN = "unknown"


_VIDEO_EXTS: frozenset[str] = frozenset({
    ".mp4", ".webm", ".mkv", ".avi", ".mov", ".flv", ".m4v",
})
_IMAGE_EXTS: frozenset[str] = frozenset({
    ".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp", ".tiff", ".tif",
})


def detect_type(path: str | Path) -> WallpaperType:
    """Determine the wallpaper type of *path*.

    Detection order:

    1. If *path* is a file, classify by extension.
    2. If *path* is a directory, inspect contents:
       - ``project.json`` present → scene (reads ``type`` field if available).
       - ``index.html`` present → web.
    3. Falls back to ``WallpaperType.UNKNOWN``.

    Args:
        path: Absolute path to a wallpaper file or directory.

    Returns:
        A :class:`WallpaperType` enum member.
    """
    p = Path(path)

    if p.is_file():
        ext = p.suffix.lower()
        if ext in _VIDEO_EXTS:
            return WallpaperType.VIDEO
        if ext in _IMAGE_EXTS:
            return WallpaperType.IMAGE
        return WallpaperType.UNKNOWN

    if p.is_dir():
        return _classify_directory(p)

    return WallpaperType.UNKNOWN


def _classify_directory(p: Path) -> WallpaperType:
    """Classify a wallpaper directory by its contents."""
    project_json = p / "project.json"
    if project_json.exists():
        # Try to read the type field from project.json.
        try:
            data = json.loads(project_json.read_text(encoding="utf-8", errors="replace"))
            raw = (data.get("type") or "scene").lower()
            if raw == "web":
                return WallpaperType.WEB
            if raw == "video":
                return WallpaperType.VIDEO
        except Exception:
            pass
        return WallpaperType.SCENE

    if (p / "index.html").exists():
        return WallpaperType.WEB

    # Check if the directory contains video files.
    for child in p.iterdir():
        if child.suffix.lower() in _VIDEO_EXTS:
            return WallpaperType.VIDEO
        if child.suffix.lower() in _IMAGE_EXTS:
            return WallpaperType.IMAGE

    return WallpaperType.UNKNOWN


def lwe_flags_for_type(wp_type: WallpaperType) -> list[str]:
    """Return any extra CLI flags linux-wallpaperengine needs for *wp_type*.

    Args:
        wp_type: The detected wallpaper type.

    Returns:
        List of additional CLI argument strings (may be empty).
    """
    if wp_type == WallpaperType.WEB:
        # CEF / web rendering — no extra flags needed; lwe auto-detects.
        return []
    if wp_type == WallpaperType.VIDEO:
        return []
    if wp_type == WallpaperType.SCENE:
        return []
    return []


def find_preview_image(path: str | Path) -> Path | None:
    """Find a preview/thumbnail image for a wallpaper path.

    Checks common preview file names inside a scene directory, or returns
    the path itself when *path* is an image file.

    Args:
        path: Wallpaper file or directory path.

    Returns:
        :class:`~pathlib.Path` to a preview image, or ``None``.
    """
    p = Path(path)

    if p.is_file() and p.suffix.lower() in _IMAGE_EXTS:
        return p

    if p.is_dir():
        for name in ("preview.jpg", "preview.png", "preview.gif",
                     "thumbnail.jpg", "thumbnail.png"):
            candidate = p / name
            if candidate.exists():
                return candidate

        # Fallback: first image found in the directory.
        for child in p.iterdir():
            if child.suffix.lower() in _IMAGE_EXTS:
                return child

    return None


def is_supported(path: str | Path) -> bool:
    """Return ``True`` if *path* points to a supported wallpaper.

    Args:
        path: File or directory to check.
    """
    return detect_type(path) != WallpaperType.UNKNOWN
