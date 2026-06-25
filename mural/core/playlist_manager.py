# mural/core/playlist_manager.py
#
# Mural — Animated Wallpaper Platform for Linux
# Copyright (C) 2024  Mural Contributors
# GPL v3 — see LICENSE

"""Playlist and wallpaper rotation logic.

:class:`PlaylistManager` maintains an ordered (or shuffled) list of
wallpapers and advances through it on a configurable timer.  It delegates
each wallpaper switch to a :class:`~mural.core.wallpaper_manager.WallpaperManager`.

The timer runs on the GLib main loop (via a GLib.timeout_add call) so it
integrates with the Core Service event loop without a separate thread.
"""

from __future__ import annotations

import logging
import random
from pathlib import Path
from typing import Callable

logger = logging.getLogger(__name__)


class PlaylistManager:
    """Manages timed wallpaper rotation across one or more monitors.

    Args:
        apply_fn: Callable ``(monitor, path) -> bool`` — typically
            :meth:`~mural.core.wallpaper_manager.WallpaperManager.apply`.
        interval_minutes: Rotation interval in minutes.  ``0`` disables
            the playlist.
        shuffle: If ``True``, pick wallpapers in random order.
    """

    def __init__(
        self,
        apply_fn: Callable[[str, str], bool],
        interval_minutes: int = 0,
        shuffle: bool = False,
    ) -> None:
        self._apply = apply_fn
        self._interval_minutes = interval_minutes
        self._shuffle = shuffle

        self._wallpapers: list[Path] = []
        self._monitor: str = ""
        self._index: int = 0
        self._timer_id: int | None = None

    # ------------------------------------------------------------------
    # Configuration
    # ------------------------------------------------------------------

    def set_wallpapers(self, paths: list[str | Path], monitor: str) -> None:
        """Set the playlist for *monitor*.

        Args:
            paths: Ordered list of wallpaper paths.
            monitor: Output name to rotate wallpapers on.
        """
        self._wallpapers = [Path(p) for p in paths if Path(p).exists()]
        self._monitor = monitor
        self._index = 0
        if self._shuffle:
            random.shuffle(self._wallpapers)
        logger.info(
            "Playlist set: %d wallpapers for %s (interval=%dmin shuffle=%s)",
            len(self._wallpapers),
            monitor,
            self._interval_minutes,
            self._shuffle,
        )

    def set_interval(self, minutes: int) -> None:
        """Update the rotation interval.  Restarts the timer if running.

        Args:
            minutes: New interval in minutes.  ``0`` stops rotation.
        """
        self._interval_minutes = minutes
        if self._timer_id is not None:
            self.stop()
            if minutes > 0:
                self.start()

    def set_shuffle(self, shuffle: bool) -> None:
        """Enable or disable shuffle mode.

        Args:
            shuffle: ``True`` to randomise order.
        """
        self._shuffle = shuffle
        if shuffle and self._wallpapers:
            random.shuffle(self._wallpapers)
            self._index = 0

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Start the rotation timer using the GLib main loop.

        Does nothing if the interval is 0 or the playlist is empty.
        """
        if self._interval_minutes <= 0 or not self._wallpapers:
            return

        try:
            import gi  # noqa: PLC0415
            gi.require_version("GLib", "2.0")
            from gi.repository import GLib  # noqa: PLC0415
        except ImportError:
            logger.warning("GLib not available — playlist timer disabled")
            return

        interval_ms = self._interval_minutes * 60 * 1000
        self._timer_id = GLib.timeout_add(interval_ms, self._on_tick)
        logger.info("Playlist timer started (%d minutes)", self._interval_minutes)

    def stop(self) -> None:
        """Cancel the rotation timer."""
        if self._timer_id is not None:
            try:
                import gi  # noqa: PLC0415
                gi.require_version("GLib", "2.0")
                from gi.repository import GLib  # noqa: PLC0415
                GLib.source_remove(self._timer_id)
            except Exception:
                pass
            self._timer_id = None
            logger.info("Playlist timer stopped")

    # ------------------------------------------------------------------
    # Navigation
    # ------------------------------------------------------------------

    def advance(self) -> bool:
        """Move to the next wallpaper and apply it immediately.

        Returns:
            ``True`` if a wallpaper was applied.
        """
        return self._on_tick()

    def previous(self) -> bool:
        """Move to the previous wallpaper and apply it.

        Returns:
            ``True`` if a wallpaper was applied.
        """
        if not self._wallpapers or not self._monitor:
            return False
        self._index = (self._index - 2) % len(self._wallpapers)
        return self._on_tick()

    def current(self) -> Path | None:
        """Return the current playlist entry without advancing."""
        if not self._wallpapers:
            return None
        return self._wallpapers[self._index % len(self._wallpapers)]

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _on_tick(self) -> bool:
        """Timer callback — apply next wallpaper and return True to continue."""
        if not self._wallpapers or not self._monitor:
            return False

        path = self._wallpapers[self._index % len(self._wallpapers)]
        self._index = (self._index + 1) % len(self._wallpapers)

        if self._shuffle and self._index == 0:
            random.shuffle(self._wallpapers)

        logger.info("Playlist: applying %s to %s", path.name, self._monitor)
        self._apply(self._monitor, str(path))
        return True  # GLib: True keeps the timer running
