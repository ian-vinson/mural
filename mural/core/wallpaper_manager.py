# mural/core/wallpaper_manager.py
#
# Mural — Animated Wallpaper Platform for Linux
# Copyright (C) 2024  Mural Contributors
# GPL v3 — see LICENSE

"""Wallpaper apply / stop / switch logic for the Core Service.

:class:`WallpaperManager` sits between the D-Bus interface and the
:class:`~mural.backend.runner.BackendRunner`.  It owns the current
assignments, validates paths, builds :class:`WallpaperAssignment` lists,
and delegates subprocess control to the runner.
"""

from __future__ import annotations

import logging
from pathlib import Path

from mural.backend.formats import detect_type, WallpaperType
from mural.backend.runner import BackendRunner, WallpaperAssignment
from mural.core.monitor_manager import MonitorAssignment, MonitorManager

logger = logging.getLogger(__name__)


class WallpaperManager:
    """Coordinates wallpaper apply/stop/switch operations.

    Args:
        runner: The :class:`~mural.backend.runner.BackendRunner` instance
            owned by the Core Service.
        monitor_manager: The :class:`~mural.core.monitor_manager.MonitorManager`
            that tracks assignments and connected outputs.
    """

    def __init__(self, runner: BackendRunner, monitor_manager: MonitorManager) -> None:
        self._runner = runner
        self._monitors = monitor_manager

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def apply(self, monitor: str, wallpaper_path: str) -> bool:
        """Set *wallpaper_path* on *monitor* and restart lwe.

        Validates that the path exists and is a supported format before
        writing the assignment.

        Args:
            monitor: Output name in KDE/lwe format.
            wallpaper_path: Absolute path to a wallpaper file or directory.
                Pass an empty string to clear the assignment for this monitor.

        Returns:
            ``True`` on success.
        """
        if wallpaper_path and not Path(wallpaper_path).exists():
            logger.error("apply: path does not exist: %s", wallpaper_path)
            return False

        if wallpaper_path:
            wp_type = detect_type(wallpaper_path)
            if wp_type == WallpaperType.UNKNOWN:
                logger.error("apply: unsupported wallpaper format: %s", wallpaper_path)
                return False
            logger.info(
                "Applying %s wallpaper to %s: %s", wp_type.value, monitor, wallpaper_path
            )

        self._monitors.assign_wallpaper(monitor, wallpaper_path)
        return self._restart_runner()

    def stop(self, monitor: str) -> bool:
        """Stop the wallpaper on *monitor* (clears its assignment).

        Args:
            monitor: Output name.

        Returns:
            ``True`` on success.
        """
        logger.info("Stopping wallpaper on %s", monitor)
        self._monitors.assign_wallpaper(monitor, "")
        active = self._monitors.active_assignments()
        if not active:
            self._runner.stop()
            return True
        return self._restart_runner()

    def stop_all(self) -> None:
        """Stop lwe and clear all monitor assignments."""
        logger.info("Stopping all wallpapers")
        self._runner.stop()

    def switch(self, monitor: str, wallpaper_path: str) -> bool:
        """Alias for :meth:`apply` — switch the wallpaper on *monitor*.

        Args:
            monitor: Output name.
            wallpaper_path: New wallpaper path.

        Returns:
            ``True`` on success.
        """
        return self.apply(monitor, wallpaper_path)

    def apply_all_saved(self) -> bool:
        """Apply all saved assignments from the monitor config.

        Called at service startup to restore the previous wallpaper state.

        Returns:
            ``True`` if lwe was started successfully.
        """
        active = self._monitors.active_assignments()
        if not active:
            logger.info("apply_all_saved: no active assignments — nothing to start")
            return True
        return self._restart_runner()

    def get_current(self, monitor: str) -> str:
        """Return the current wallpaper path for *monitor*, or ``""``."""
        assignment = self._monitors.get_assignment(monitor)
        return assignment.wallpaper if assignment else ""

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _restart_runner(self) -> bool:
        """Build the assignment list and (re)start the runner."""
        active: list[MonitorAssignment] = self._monitors.active_assignments()
        if not active:
            logger.info("No active assignments — lwe not started")
            return True

        assignments = [
            WallpaperAssignment(monitor=a.monitor_name, wallpaper=a.wallpaper)
            for a in active
        ]
        try:
            self._runner.start(assignments)
            return True
        except Exception as exc:
            logger.error("Failed to start lwe: %s", exc)
            return False
