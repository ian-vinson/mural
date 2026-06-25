# mural/detection.py
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

"""Desktop environment and compositor auto-detection.

Reads standard XDG environment variables at startup to determine which
DE adapter to instantiate.  Never raises — always returns a usable result
even on unknown environments.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from mural.adapters.base import BaseAdapter

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class DetectionResult:
    """Snapshot of the detected desktop environment at startup.

    Attributes:
        desktop: Normalised DE identifier — one of ``"plasma"``,
            ``"hyprland"``, ``"gnome"``, ``"xfce"``, ``"unknown"``.
        session: Display server protocol — ``"wayland"`` or ``"x11"``.
        adapter_class: The adapter class that should be instantiated for
            this environment.  Falls back to :class:`NullAdapter` when
            the DE is not supported yet.
    """

    desktop: str
    session: str
    adapter_class: type["BaseAdapter"]
    raw_env: dict[str, str] = field(default_factory=dict, repr=False)


def _read_env() -> dict[str, str]:
    """Collect the relevant environment variables into a plain dict."""
    keys = (
        "XDG_CURRENT_DESKTOP",
        "XDG_SESSION_TYPE",
        "WAYLAND_DISPLAY",
        "DISPLAY",
        "PLASMA_SESSION",
        "KDE_FULL_SESSION",
        "HYPRLAND_INSTANCE_SIGNATURE",
        "GNOME_DESKTOP_SESSION_ID",
    )
    return {k: os.environ.get(k, "") for k in keys}


def _detect_session(env: dict[str, str]) -> str:
    """Return ``"wayland"`` or ``"x11"`` from environment variables."""
    session_type = env["XDG_SESSION_TYPE"].lower()
    if session_type in ("wayland", "x11"):
        return session_type
    if env["WAYLAND_DISPLAY"]:
        return "wayland"
    if env["DISPLAY"]:
        return "x11"
    return "x11"  # safest default


def _detect_desktop(env: dict[str, str]) -> str:
    """Return a normalised desktop identifier from environment variables."""
    xdg = env["XDG_CURRENT_DESKTOP"].upper()

    if "KDE" in xdg or "PLASMA" in xdg or env["PLASMA_SESSION"] or env["KDE_FULL_SESSION"]:
        return "plasma"
    if "HYPRLAND" in xdg or env["HYPRLAND_INSTANCE_SIGNATURE"]:
        return "hyprland"
    if "GNOME" in xdg or env["GNOME_DESKTOP_SESSION_ID"]:
        return "gnome"
    if "XFCE" in xdg:
        return "xfce"
    if "SWAY" in xdg:
        return "sway"
    if xdg:
        logger.warning("Unknown XDG_CURRENT_DESKTOP value: %r — using null adapter", xdg)
    else:
        logger.warning("XDG_CURRENT_DESKTOP is not set — using null adapter")
    return "unknown"


def _adapter_class_for(desktop: str, session: str, env: dict[str, str]) -> "type[BaseAdapter]":
    """Return the appropriate adapter class without instantiating it.

    Imports are deferred so that optional adapter dependencies don't need
    to be installed when a DE is not in use.
    """
    if desktop == "plasma":
        from mural.adapters.plasma import PlasmaAdapter  # noqa: PLC0415
        return PlasmaAdapter
    if desktop == "hyprland":
        from mural.adapters.wlr import WlrAdapter  # noqa: PLC0415
        return WlrAdapter
    if desktop == "gnome":
        from mural.adapters.gnome import GnomeAdapter  # noqa: PLC0415
        return GnomeAdapter
    if desktop in ("xfce", "sway") or (session == "x11" and env.get("DISPLAY")):
        from mural.adapters.x11 import X11Adapter  # noqa: PLC0415
        return X11Adapter

    from mural.adapters.base import NullAdapter  # noqa: PLC0415
    return NullAdapter


def detect() -> DetectionResult:
    """Detect the current desktop environment and return a :class:`DetectionResult`.

    This is the primary entry point for environment detection.  Call once
    at startup; the result is stable for the lifetime of the session.

    Returns:
        A frozen :class:`DetectionResult` describing the detected environment.
    """
    env = _read_env()
    session = _detect_session(env)
    desktop = _detect_desktop(env)
    adapter_cls = _adapter_class_for(desktop, session, env)

    result = DetectionResult(
        desktop=desktop,
        session=session,
        adapter_class=adapter_cls,
        raw_env=env,
    )
    logger.info(
        "Detected environment: desktop=%r session=%r adapter=%s",
        result.desktop,
        result.session,
        result.adapter_class.__name__,
    )
    return result
