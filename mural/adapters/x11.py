# mural/adapters/x11.py
#
# Mural — Animated Wallpaper Platform for Linux
# Copyright (C) 2024  Mural Contributors
# GPL v3 — see LICENSE

"""X11 root window adapter (XFCE, plain X11 sessions).

Monitor detection uses ``xrandr``.  Wallpaper apply/stop is routed
through the Core Service D-Bus interface.

Note from DEVGUIDE: X11 root window mode does NOT work if a compositor
is drawing the background.  XFCE with Xfwm4 compositor may need the
compositor disabled for lwe to render correctly.
"""

from __future__ import annotations

import logging
import re
import subprocess
from typing import Any

from mural.adapters.base import BaseAdapter
from mural.core.monitor_manager import Monitor, normalize_monitor_name

logger = logging.getLogger(__name__)


class X11Adapter(BaseAdapter):
    """Adapter for X11 sessions (XFCE, plain X11, Sway on X11).

    Args:
        core_proxy: dasbus proxy for ``com.mural.Core``.
    """

    name = "x11"

    def __init__(self, core_proxy: Any | None = None) -> None:
        self._core = core_proxy

    def detect_monitors(self) -> list[Monitor]:
        """Detect monitors via ``xrandr --listmonitors``."""
        monitors = self._detect_listmonitors()
        if not monitors:
            monitors = self._detect_xrandr_full()
        return monitors

    def apply_wallpaper(self, monitor: str, wallpaper_path: str) -> bool:
        """Route apply request to Core Service."""
        monitor = normalize_monitor_name(monitor)
        if not self._core:
            logger.error("[x11] apply_wallpaper: Core Service proxy not set")
            return False
        try:
            return bool(self._core.SetWallpaper(monitor, wallpaper_path))
        except Exception as exc:
            logger.error("[x11] SetWallpaper failed: %s", exc)
            return False

    def stop_wallpaper(self, monitor: str) -> bool:
        return self.apply_wallpaper(monitor, "")

    def capabilities(self) -> dict[str, bool]:
        return {
            "fullscreen_detection": False,
            "hotplug": False,
            "session_events": False,
            "multi_monitor": True,
        }

    # ------------------------------------------------------------------
    # Detection helpers
    # ------------------------------------------------------------------

    def _detect_listmonitors(self) -> list[Monitor]:
        try:
            result = subprocess.run(
                ["xrandr", "--listmonitors"],
                capture_output=True, text=True, timeout=5,
            )
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return []

        monitors: list[Monitor] = []
        pattern = re.compile(
            r"\s*\d+:\s+\+?\*?(\S+)\s+(\d+)/\d+x(\d+)/\d+\+(-?\d+)\+(-?\d+)\s+(\S+)"
        )
        for line in result.stdout.splitlines():
            m = pattern.match(line)
            if m:
                is_primary = "*" in line
                raw_name = m.group(6)
                monitors.append(Monitor(
                    name=normalize_monitor_name(raw_name),
                    width=int(m.group(2)), height=int(m.group(3)),
                    x=int(m.group(4)), y=int(m.group(5)),
                    is_primary=is_primary, connected=True,
                ))
        return monitors

    def _detect_xrandr_full(self) -> list[Monitor]:
        try:
            result = subprocess.run(
                ["xrandr"], capture_output=True, text=True, timeout=5
            )
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return []

        monitors: list[Monitor] = []
        pattern = re.compile(
            r"^(\S+)\s+connected\s*(primary)?\s*(\d+)x(\d+)\+(-?\d+)\+(-?\d+)"
        )
        for line in result.stdout.splitlines():
            m = pattern.match(line)
            if m:
                monitors.append(Monitor(
                    name=normalize_monitor_name(m.group(1)),
                    width=int(m.group(3)), height=int(m.group(4)),
                    x=int(m.group(5)), y=int(m.group(6)),
                    is_primary=bool(m.group(2)), connected=True,
                ))
        return monitors
