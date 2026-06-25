# mural/platform/cache.py
#
# Mural — Animated Wallpaper Platform for Linux
# Copyright (C) 2024  Mural Contributors
# GPL v3 — see LICENSE

"""Local download cache for Mural platform wallpapers.

Manages the ``~/.local/share/mural/downloads/`` directory: tracks which
wallpapers have been downloaded, maps platform IDs to local paths, and
provides cleanup utilities.

The index is stored as ``~/.cache/mural/download_index.json``.
"""

from __future__ import annotations

import json
import logging
import shutil
from pathlib import Path
from typing import Any

from mural.platform.models import PlatformWallpaper

logger = logging.getLogger(__name__)

_DOWNLOAD_DIR  = Path("~/.local/share/mural/downloads").expanduser()
_CACHE_DIR     = Path("~/.cache/mural").expanduser()
_INDEX_FILE    = _CACHE_DIR / "download_index.json"


class DownloadCache:
    """Manages locally cached platform wallpapers.

    Maintains a JSON index mapping platform wallpaper IDs to local file
    paths, so the GUI can immediately show which wallpapers are already
    downloaded without scanning the filesystem.
    """

    def __init__(
        self,
        download_dir: Path = _DOWNLOAD_DIR,
        index_file: Path = _INDEX_FILE,
    ) -> None:
        self._download_dir = download_dir
        self._index_file = index_file
        self._index: dict[str, str] = {}
        self._load_index()

    # ------------------------------------------------------------------
    # Index management
    # ------------------------------------------------------------------

    def _load_index(self) -> None:
        """Load the download index from disk."""
        if self._index_file.exists():
            try:
                self._index = json.loads(
                    self._index_file.read_text(encoding="utf-8")
                )
            except Exception as exc:
                logger.warning("Could not read download index: %s", exc)
                self._index = {}

    def _save_index(self) -> None:
        """Persist the current index to disk."""
        self._cache_dir_ensure()
        try:
            self._index_file.write_text(
                json.dumps(self._index, indent=2), encoding="utf-8"
            )
        except OSError as exc:
            logger.error("Could not save download index: %s", exc)

    def _cache_dir_ensure(self) -> None:
        self._download_dir.mkdir(parents=True, exist_ok=True)
        _CACHE_DIR.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def is_cached(self, wallpaper_id: str) -> bool:
        """Return ``True`` if *wallpaper_id* is in the index and its file exists.

        Args:
            wallpaper_id: Platform wallpaper UUID.
        """
        local_path = self._index.get(wallpaper_id)
        if not local_path:
            return False
        return Path(local_path).exists()

    def get_local_path(self, wallpaper_id: str) -> Path | None:
        """Return the local :class:`~pathlib.Path` for a cached wallpaper.

        Args:
            wallpaper_id: Platform wallpaper UUID.

        Returns:
            Local path, or ``None`` if not cached.
        """
        path_str = self._index.get(wallpaper_id)
        if path_str and Path(path_str).exists():
            return Path(path_str)
        return None

    def dest_path_for(self, wallpaper: PlatformWallpaper) -> Path:
        """Compute the local destination path for a platform wallpaper.

        Args:
            wallpaper: The wallpaper to compute a path for.

        Returns:
            The target :class:`~pathlib.Path` (file may not exist yet).
        """
        safe = _sanitise_name(wallpaper.title) or wallpaper.id
        ext = _extension_for_type(wallpaper.type)
        return self._download_dir / (safe + ext)

    def register(self, wallpaper_id: str, local_path: Path) -> None:
        """Record that *wallpaper_id* has been downloaded to *local_path*.

        Args:
            wallpaper_id: Platform UUID.
            local_path: Local file path.
        """
        self._index[wallpaper_id] = str(local_path)
        self._save_index()
        logger.debug("Cached %s → %s", wallpaper_id, local_path)

    def remove(self, wallpaper_id: str, delete_file: bool = True) -> bool:
        """Remove a cached wallpaper from the index.

        Args:
            wallpaper_id: Platform UUID.
            delete_file: If ``True``, also delete the local file.

        Returns:
            ``True`` if the entry existed and was removed.
        """
        local_path_str = self._index.pop(wallpaper_id, None)
        if not local_path_str:
            return False
        if delete_file:
            path = Path(local_path_str)
            if path.exists():
                try:
                    if path.is_dir():
                        shutil.rmtree(path)
                    else:
                        path.unlink()
                except OSError as exc:
                    logger.warning("Could not delete %s: %s", path, exc)
        self._save_index()
        return True

    def all_cached(self) -> dict[str, Path]:
        """Return all cached wallpapers as ``{wallpaper_id: local_path}``.

        Only entries whose files still exist on disk are returned.
        Stale index entries are cleaned up automatically.
        """
        result: dict[str, Path] = {}
        stale: list[str] = []
        for wid, path_str in self._index.items():
            p = Path(path_str)
            if p.exists():
                result[wid] = p
            else:
                stale.append(wid)
        if stale:
            for wid in stale:
                del self._index[wid]
            self._save_index()
            logger.debug("Removed %d stale cache entries", len(stale))
        return result

    def cache_size_bytes(self) -> int:
        """Return the total size of all cached files in bytes."""
        total = 0
        for path in self.all_cached().values():
            try:
                if path.is_file():
                    total += path.stat().st_size
                elif path.is_dir():
                    total += sum(f.stat().st_size for f in path.rglob("*") if f.is_file())
            except OSError:
                pass
        return total

    def clear(self, delete_files: bool = True) -> int:
        """Clear all cached wallpapers.

        Args:
            delete_files: If ``True``, delete the local files as well.

        Returns:
            Number of entries removed.
        """
        count = len(self._index)
        if delete_files:
            for path_str in self._index.values():
                p = Path(path_str)
                try:
                    if p.is_dir():
                        shutil.rmtree(p)
                    elif p.exists():
                        p.unlink()
                except OSError as exc:
                    logger.warning("Could not delete %s: %s", p, exc)
        self._index.clear()
        self._save_index()
        logger.info("Cache cleared (%d entries)", count)
        return count


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _sanitise_name(name: str) -> str:
    """Return a filesystem-safe version of *name*."""
    return "".join(c if c.isalnum() or c in " ._-" else "_" for c in name).strip()[:80]


def _extension_for_type(wp_type: str) -> str:
    return {
        "video": ".mp4",
        "scene": "",     # scene wallpapers are directories
        "web":   "",
        "image": ".jpg",
    }.get(wp_type.lower(), ".bin")
