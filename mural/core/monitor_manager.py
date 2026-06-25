# mural/core/monitor_manager.py
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

"""Monitor detection and per-monitor wallpaper assignment management.

Detects connected monitors and their output names (needed for the
``--screen-root`` argument passed to linux-wallpaperengine).  Persists
per-monitor wallpaper assignments to ``~/.config/mural/monitors.json``.

Detection strategy
------------------
* **Wayland / KDE Plasma** — ``kscreen-doctor -o`` (subprocess).  Falls back
  to ``QGuiApplication.screens()`` when a Qt application is already running.
* **X11** — ``xrandr --listmonitors`` (subprocess).

Monitor names are normalised to KDE/kscreen format (e.g. ``"HDMI-A-2"``)
so they can be passed directly to lwe's ``--screen-root``.
"""

from __future__ import annotations

import json
import logging
import re
import subprocess
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Sequence

logger = logging.getLogger(__name__)

_CONFIG_DIR = Path("~/.config/mural").expanduser()
_MONITORS_JSON = _CONFIG_DIR / "monitors.json"


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class Monitor:
    """A single connected display output.

    Attributes:
        name: Output name in KDE/lwe format, e.g. ``"DP-3"`` or ``"HDMI-A-2"``.
        width: Horizontal resolution in pixels.
        height: Vertical resolution in pixels.
        x: Horizontal offset of the top-left corner in the compositor layout.
        y: Vertical offset of the top-left corner in the compositor layout.
        is_primary: ``True`` if this is the primary/priority output.
        connected: ``True`` if a display is physically connected.
    """

    name: str
    width: int = 0
    height: int = 0
    x: int = 0
    y: int = 0
    is_primary: bool = False
    connected: bool = True

    @property
    def resolution(self) -> str:
        """Return a ``"WxH"`` resolution string."""
        return f"{self.width}x{self.height}"

    def __str__(self) -> str:
        return f"{self.name} ({self.resolution}+{self.x}+{self.y})"


@dataclass
class MonitorAssignment:
    """Wallpaper assignment for a single monitor.

    Attributes:
        monitor_name: Output name (matches :attr:`Monitor.name`).
        wallpaper: Absolute path to the wallpaper, or ``""`` if unset.
        enabled: Whether Mural should manage this monitor's wallpaper.
    """

    monitor_name: str
    wallpaper: str = ""
    enabled: bool = True
    scaling: str = "default"


# ---------------------------------------------------------------------------
# Name normalisation
# ---------------------------------------------------------------------------

def normalize_monitor_name(raw: str) -> str:
    """Normalise a raw output name to KDE/lwe format.

    Handles the most common xrandr → KDE discrepancies:

    * ``"HDMI1"``        → ``"HDMI-A-1"``
    * ``"HDMI-1"``       → ``"HDMI-A-1"``
    * ``"DisplayPort-1"``→ ``"DP-1"``
    * ``"DP1"``          → ``"DP-1"``
    * ``"eDP1"``         → ``"eDP-1"``
    * ``"eDP-1"``        → ``"eDP-1"``  (already correct)
    * ``"DP-3"``         → ``"DP-3"``   (already correct)

    Args:
        raw: The monitor name as returned by xrandr or kscreen-doctor.

    Returns:
        Normalised output name.
    """
    name = raw.strip()

    # Expand DisplayPort abbreviation
    name = re.sub(r"(?i)^displayport[-_]?", "DP-", name)

    # HDMI without sub-connector letter: HDMI1, HDMI-1 → HDMI-A-1
    name = re.sub(r"(?i)^HDMI-?(\d+)$", lambda m: f"HDMI-A-{m.group(1)}", name)

    # Remove any double dash that may have been introduced
    name = re.sub(r"--+", "-", name)

    # Ensure a dash between alpha prefix and numeric suffix when missing
    # e.g. "DP1" → "DP-1", "eDP1" → "eDP-1"
    name = re.sub(r"([a-zA-Z])(\d+)$", r"\1-\2", name)

    return name


# ---------------------------------------------------------------------------
# Detection backends
# ---------------------------------------------------------------------------

def _detect_via_kscreen() -> list[Monitor]:
    """Parse ``kscreen-doctor -o`` output into a list of :class:`Monitor` objects.

    Returns an empty list if kscreen-doctor is not available or fails.
    """
    try:
        result = subprocess.run(
            ["kscreen-doctor", "-o"],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        logger.debug("kscreen-doctor unavailable: %s", exc)
        return []

    monitors: list[Monitor] = []
    current: dict[str, str | int | bool] = {}

    # Example kscreen-doctor output lines:
    # Output: 1 eDP-1 enabled connected priority 1
    #   Geometry: 0,0,2560x1600
    # Output: 2 DP-3 enabled connected priority 0
    #   Geometry: 2560,0,3440x1440

    for line in result.stdout.splitlines():
        output_match = re.match(
            r"Output:\s+\d+\s+(\S+)\s+(enabled|disabled)\s+(connected|disconnected)(.*)$",
            line.strip(),
        )
        if output_match:
            if current:
                monitors.append(_build_monitor(current))
            name = normalize_monitor_name(output_match.group(1))
            current = {
                "name": name,
                "connected": output_match.group(3) == "connected",
                "is_primary": "priority 1" in output_match.group(4),
            }
            continue

        geo_match = re.match(r"Geometry:\s*(-?\d+),(-?\d+),(\d+)x(\d+)", line.strip())
        if geo_match and current:
            current["x"] = int(geo_match.group(1))
            current["y"] = int(geo_match.group(2))
            current["width"] = int(geo_match.group(3))
            current["height"] = int(geo_match.group(4))

    if current:
        monitors.append(_build_monitor(current))

    return monitors


def _detect_via_xrandr() -> list[Monitor]:
    """Parse ``xrandr`` output into a list of :class:`Monitor` objects.

    Uses ``xrandr --listmonitors`` for a compact, machine-friendly format
    and falls back to full ``xrandr`` output if needed.

    Returns an empty list if xrandr is not available or fails.
    """
    try:
        result = subprocess.run(
            ["xrandr", "--listmonitors"],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        logger.debug("xrandr unavailable: %s", exc)
        return []

    monitors: list[Monitor] = []

    # Format: " 0: +*eDP-1 2560/340x1600/210+0+0  eDP-1"
    # The name at the end of the line is the canonical output name.
    for line in result.stdout.splitlines():
        m = re.match(
            r"\s*\d+:\s+\+?\*?(\S+)\s+(\d+)/\d+x(\d+)/\d+\+(-?\d+)\+(-?\d+)\s+(\S+)",
            line,
        )
        if m:
            is_primary = "*" in line
            raw_name = m.group(6)  # trailing name is most reliable
            monitors.append(Monitor(
                name=normalize_monitor_name(raw_name),
                width=int(m.group(2)),
                height=int(m.group(3)),
                x=int(m.group(4)),
                y=int(m.group(5)),
                is_primary=is_primary,
                connected=True,
            ))

    if monitors:
        return monitors

    # Fallback: full xrandr output
    return _detect_via_xrandr_full()


def _detect_via_xrandr_full() -> list[Monitor]:
    """Parse full ``xrandr`` output as a fallback."""
    try:
        result = subprocess.run(
            ["xrandr"],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return []

    monitors: list[Monitor] = []
    # Line format: "DP-3 connected 3440x1440+2560+0 ..."
    # or:          "eDP-1 connected primary 2560x1600+0+0 ..."
    pattern = re.compile(
        r"^(\S+)\s+connected\s*(primary)?\s*(\d+)x(\d+)\+(-?\d+)\+(-?\d+)"
    )
    for line in result.stdout.splitlines():
        m = pattern.match(line)
        if m:
            monitors.append(Monitor(
                name=normalize_monitor_name(m.group(1)),
                width=int(m.group(3)),
                height=int(m.group(4)),
                x=int(m.group(5)),
                y=int(m.group(6)),
                is_primary=bool(m.group(2)),
                connected=True,
            ))
    return monitors


def _detect_via_qscreen() -> list[Monitor]:
    """Use PySide6's QGuiApplication.screens() when a Qt app is running.

    This is the cleanest approach but requires an existing QGuiApplication
    instance.  Returns an empty list when Qt is not available or no
    application exists.
    """
    try:
        from PySide6.QtGui import QGuiApplication  # noqa: PLC0415
    except ImportError:
        return []

    app = QGuiApplication.instance()
    if app is None:
        return []

    monitors: list[Monitor] = []
    primary = app.primaryScreen()
    for screen in app.screens():
        geo = screen.geometry()
        monitors.append(Monitor(
            name=normalize_monitor_name(screen.name()),
            width=geo.width(),
            height=geo.height(),
            x=geo.x(),
            y=geo.y(),
            is_primary=(screen is primary),
            connected=True,
        ))
    return monitors


def _build_monitor(data: dict) -> Monitor:
    return Monitor(
        name=str(data.get("name", "unknown")),
        width=int(data.get("width", 0)),
        height=int(data.get("height", 0)),
        x=int(data.get("x", 0)),
        y=int(data.get("y", 0)),
        is_primary=bool(data.get("is_primary", False)),
        connected=bool(data.get("connected", True)),
    )


# ---------------------------------------------------------------------------
# MonitorManager
# ---------------------------------------------------------------------------

class MonitorManager:
    """Detects connected monitors and manages per-monitor wallpaper assignments.

    Args:
        session: Display server session type — ``"wayland"`` or ``"x11"``.
        desktop: Normalised desktop identifier (e.g. ``"plasma"``).
        config_path: Override the default ``~/.config/mural/monitors.json``
            path (useful for testing).
    """

    def __init__(
        self,
        session: str = "wayland",
        desktop: str = "plasma",
        config_path: Path | None = None,
    ) -> None:
        self._session = session
        self._desktop = desktop
        self._config_path = config_path or _MONITORS_JSON
        self._monitors: list[Monitor] = []
        self._assignments: dict[str, MonitorAssignment] = {}

    # ------------------------------------------------------------------
    # Detection
    # ------------------------------------------------------------------

    def detect(self) -> list[Monitor]:
        """Detect connected monitors and cache the result.

        Detection is attempted in this order:

        1. PySide6 QScreen (if a QGuiApplication is running).
        2. ``kscreen-doctor`` (KDE Plasma Wayland/X11).
        3. ``xrandr`` (X11 fallback).

        Returns:
            List of :class:`Monitor` objects for connected outputs.
        """
        monitors = _detect_via_qscreen()
        if not monitors:
            if self._session == "wayland" or self._desktop == "plasma":
                monitors = _detect_via_kscreen()
            if not monitors:
                monitors = _detect_via_xrandr()

        self._monitors = [m for m in monitors if m.connected]

        if not self._monitors:
            logger.warning(
                "No connected monitors detected. "
                "Ensure kscreen-doctor or xrandr is available."
            )
        else:
            logger.info(
                "Detected %d monitor(s): %s",
                len(self._monitors),
                ", ".join(str(m) for m in self._monitors),
            )

        return self._monitors

    @property
    def monitors(self) -> list[Monitor]:
        """Cached list of connected monitors (call :meth:`detect` first)."""
        return list(self._monitors)

    def primary_monitor(self) -> Monitor | None:
        """Return the primary monitor, or the first one if none is flagged primary."""
        primaries = [m for m in self._monitors if m.is_primary]
        if primaries:
            return primaries[0]
        return self._monitors[0] if self._monitors else None

    # ------------------------------------------------------------------
    # Assignments
    # ------------------------------------------------------------------

    def load_assignments(self) -> dict[str, MonitorAssignment]:
        """Load per-monitor wallpaper assignments from disk.

        Returns:
            Dict mapping monitor name → :class:`MonitorAssignment`.
            Returns an empty dict if the file does not exist.
        """
        if not self._config_path.exists():
            logger.debug("No monitors.json found at %s", self._config_path)
            return {}

        try:
            raw: dict = json.loads(self._config_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            logger.error("Failed to load monitors.json: %s", exc)
            return {}

        self._assignments = {
            name: MonitorAssignment(
                monitor_name=name,
                wallpaper=entry.get("wallpaper", ""),
                enabled=entry.get("enabled", True),
                scaling=entry.get("scaling", "default"),
            )
            for name, entry in raw.items()
        }
        return dict(self._assignments)

    def save_assignments(self) -> None:
        """Persist current assignments to ``~/.config/mural/monitors.json``."""
        self._config_path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            name: {"wallpaper": a.wallpaper, "enabled": a.enabled, "scaling": a.scaling}
            for name, a in self._assignments.items()
        }
        try:
            self._config_path.write_text(
                json.dumps(data, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
            logger.debug("Saved monitor assignments to %s", self._config_path)
        except OSError as exc:
            logger.error("Failed to save monitors.json: %s", exc)

    def assign_wallpaper(self, monitor_name: str, wallpaper: str) -> None:
        """Set the wallpaper for a named monitor and persist immediately.

        Args:
            monitor_name: Output name (e.g. ``"DP-3"``).
            wallpaper: Absolute path to the wallpaper, or workshop ID string.
        """
        if monitor_name not in self._assignments:
            self._assignments[monitor_name] = MonitorAssignment(monitor_name=monitor_name)
        self._assignments[monitor_name].wallpaper = wallpaper
        self.save_assignments()

    def assign_scaling(self, monitor_name: str, scaling: str) -> None:
        """Set the scaling mode for a monitor and persist immediately."""
        if monitor_name not in self._assignments:
            self._assignments[monitor_name] = MonitorAssignment(monitor_name=monitor_name)
        self._assignments[monitor_name].scaling = scaling
        self.save_assignments()

    def set_enabled(self, monitor_name: str, enabled: bool) -> None:
        """Enable or disable Mural management for a monitor.

        Args:
            monitor_name: Output name.
            enabled: ``False`` to leave this monitor's wallpaper alone.
        """
        if monitor_name not in self._assignments:
            self._assignments[monitor_name] = MonitorAssignment(monitor_name=monitor_name)
        self._assignments[monitor_name].enabled = enabled
        self.save_assignments()

    def active_assignments(self) -> list[MonitorAssignment]:
        """Return enabled assignments that have a wallpaper set."""
        return [
            a for a in self._assignments.values()
            if a.enabled and a.wallpaper
        ]

    def get_assignment(self, monitor_name: str) -> MonitorAssignment | None:
        """Return the assignment for *monitor_name*, or ``None``."""
        return self._assignments.get(monitor_name)
