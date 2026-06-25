# mural/adapters/gnome.py
#
# Mural — Animated Wallpaper Platform for Linux
# Copyright (C) 2024  Mural Contributors
# GPL v3 — see LICENSE

"""GNOME Shell adapter — stub / planned implementation.

GNOME has no public API for animated wallpapers (see DEVGUIDE — PHASE 5).
This adapter logs a clear explanation and falls back to NullAdapter
behaviour until a GNOME Shell extension approach is implemented.

Implementation options (future):
  1. GNOME Shell extension that creates a background Clutter actor.
  2. Hack: render lwe output to an off-screen surface, capture frames,
     set as static wallpaper via gsettings (low quality, not viable).

Do not implement until Plasma and wlroots adapters are stable.
Refer to https://extensions.gnome.org for extension development docs.
"""

from __future__ import annotations

import logging
from typing import Any

from mural.adapters.base import BaseAdapter, NullAdapter
from mural.core.monitor_manager import Monitor

logger = logging.getLogger(__name__)

_NOT_IMPLEMENTED_MSG = (
    "GNOME Shell does not provide a public API for animated wallpapers. "
    "Mural's GNOME adapter is planned but not yet implemented. "
    "See DEVGUIDE — PHASE 5 for the planned approach."
)


class GnomeAdapter(BaseAdapter):
    """Stub adapter for GNOME Shell — logs a warning on every operation.

    When GNOME support is eventually implemented this class will be
    replaced with a real adapter that drives a companion GNOME Shell
    extension.

    Args:
        core_proxy: Ignored in this stub implementation.
    """

    name = "gnome"

    def __init__(self, core_proxy: Any | None = None) -> None:
        logger.warning("[gnome] %s", _NOT_IMPLEMENTED_MSG)
        self._null = NullAdapter()

    def detect_monitors(self) -> list[Monitor]:
        """Return an empty list — GNOME adapter not yet implemented."""
        logger.warning("[gnome] detect_monitors: %s", _NOT_IMPLEMENTED_MSG)
        return []

    def apply_wallpaper(self, monitor: str, wallpaper_path: str) -> bool:
        """Return ``False`` — GNOME adapter not yet implemented."""
        logger.warning(
            "[gnome] apply_wallpaper(%r, %r): %s", monitor, wallpaper_path, _NOT_IMPLEMENTED_MSG
        )
        return False

    def stop_wallpaper(self, monitor: str) -> bool:
        """Return ``False`` — GNOME adapter not yet implemented."""
        logger.warning("[gnome] stop_wallpaper(%r): %s", monitor, _NOT_IMPLEMENTED_MSG)
        return False

    def capabilities(self) -> dict[str, bool]:
        return {
            "fullscreen_detection": False,
            "hotplug": False,
            "session_events": False,
            "multi_monitor": False,
        }
