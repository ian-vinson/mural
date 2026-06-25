# mural/adapters/base.py
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

"""Abstract base class and null implementation for DE adapters.

Each desktop environment adapter inherits from :class:`BaseAdapter` and
provides DE-specific implementations of monitor detection, wallpaper
application, and optional features such as fullscreen detection.

The adapter layer sits between the Core Service and the display server:

    Core Service  →  Adapter  →  linux-wallpaperengine / DE APIs

Adapters do NOT manage the lwe subprocess directly.  They translate
abstract requests (e.g. "apply wallpaper X to monitor Y") into the
correct DE-specific calls and hand subprocess control back to the Core
Service via the :class:`~mural.backend.runner.BackendRunner`.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from mural.core.monitor_manager import Monitor

logger = logging.getLogger(__name__)


class BaseAdapter(ABC):
    """Abstract interface that all DE adapters must implement.

    Subclasses are instantiated by the Core Service after
    :func:`~mural.detection.detect` resolves the correct class.
    """

    # Human-readable name used in log messages and status output.
    name: str = "base"

    # ------------------------------------------------------------------
    # Required — every adapter must implement these
    # ------------------------------------------------------------------

    @abstractmethod
    def detect_monitors(self) -> list["Monitor"]:
        """Return a list of currently connected monitors.

        Returns:
            List of :class:`~mural.core.monitor_manager.Monitor` objects.
            May return an empty list on failure; must never raise.
        """

    @abstractmethod
    def apply_wallpaper(self, monitor: str, wallpaper_path: str) -> bool:
        """Request that the Core Service apply *wallpaper_path* to *monitor*.

        Adapters do not call linux-wallpaperengine directly.  They build
        the correct argument set for the current DE (e.g. the right
        ``--screen-root`` name) and return it via the Core Service.

        Args:
            monitor: Output name in KDE/lwe format, e.g. ``"DP-3"``.
            wallpaper_path: Absolute path to a wallpaper directory or file.

        Returns:
            ``True`` on success, ``False`` if the adapter could not fulfil
            the request (e.g. monitor not found, display unavailable).
        """

    @abstractmethod
    def stop_wallpaper(self, monitor: str) -> bool:
        """Stop the wallpaper on *monitor* without touching other monitors.

        Args:
            monitor: Output name.

        Returns:
            ``True`` on success.
        """

    # ------------------------------------------------------------------
    # Optional — adapters override where the DE supports it
    # ------------------------------------------------------------------

    def set_fullscreen_detection(self, enabled: bool) -> None:
        """Enable or disable pausing on fullscreen windows.

        Default implementation is a no-op.  Override in adapters that can
        hook into compositor fullscreen events (e.g. KWin scripting).

        Args:
            enabled: ``True`` to pause wallpaper when a fullscreen window
                is detected; ``False`` to disable the feature.
        """

    def on_monitor_connected(self, monitor: "Monitor") -> None:
        """Called by the Core Service when a new monitor is plugged in.

        Default implementation logs and does nothing.

        Args:
            monitor: The newly connected :class:`~mural.core.monitor_manager.Monitor`.
        """
        logger.info("[%s] Monitor connected: %s", self.name, monitor)

    def on_monitor_disconnected(self, monitor_name: str) -> None:
        """Called by the Core Service when a monitor is unplugged.

        Default implementation logs and does nothing.

        Args:
            monitor_name: Output name of the disconnected monitor.
        """
        logger.info("[%s] Monitor disconnected: %s", self.name, monitor_name)

    def on_session_lock(self) -> None:
        """Called when the session is locked (screensaver / lock screen).

        Default implementation is a no-op.  Adapters may pause rendering.
        """

    def on_session_unlock(self) -> None:
        """Called when the session is unlocked.

        Default implementation is a no-op.  Adapters may resume rendering.
        """

    def on_suspend(self) -> None:
        """Called just before the system suspends.

        Default implementation is a no-op.
        """

    def on_resume(self) -> None:
        """Called after the system resumes from suspend.

        Default implementation is a no-op.
        """

    def capabilities(self) -> dict[str, bool]:
        """Return a dict describing which optional features this adapter supports.

        Keys:
            ``fullscreen_detection``  — can pause on fullscreen windows.
            ``hotplug``               — handles monitor connect/disconnect.
            ``session_events``        — responds to lock/unlock/suspend.
            ``multi_monitor``         — can drive more than one monitor.

        Returns:
            Dict of capability name → bool.
        """
        return {
            "fullscreen_detection": False,
            "hotplug": False,
            "session_events": False,
            "multi_monitor": False,
        }

    def __repr__(self) -> str:
        caps = [k for k, v in self.capabilities().items() if v]
        return f"<{self.__class__.__name__} caps={caps}>"


# ---------------------------------------------------------------------------
# Null adapter — used when the DE is not recognised or not yet supported
# ---------------------------------------------------------------------------

class NullAdapter(BaseAdapter):
    """No-op adapter for unknown or unsupported desktop environments.

    Every method logs a warning and returns a safe default.  This allows
    Mural to start and the GUI to function even when the DE is not
    supported, rather than crashing at startup.
    """

    name = "null"

    def detect_monitors(self) -> list["Monitor"]:
        """Return an empty list — monitor detection not supported."""
        logger.warning(
            "NullAdapter: monitor detection is not supported on this desktop environment. "
            "Set wallpapers manually via D-Bus."
        )
        return []

    def apply_wallpaper(self, monitor: str, wallpaper_path: str) -> bool:
        """Log a warning and return ``False`` — cannot apply wallpaper."""
        logger.warning(
            "NullAdapter: cannot apply wallpaper on unsupported desktop environment "
            "(monitor=%r, path=%r)",
            monitor,
            wallpaper_path,
        )
        return False

    def stop_wallpaper(self, monitor: str) -> bool:
        """Log a warning and return ``False``."""
        logger.warning(
            "NullAdapter: cannot stop wallpaper on unsupported desktop environment "
            "(monitor=%r)",
            monitor,
        )
        return False
