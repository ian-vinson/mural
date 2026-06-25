# mural/adapters/wlr.py
#
# Mural — Animated Wallpaper Platform for Linux
# Copyright (C) 2024  Mural Contributors
# GPL v3 — see LICENSE

"""wlroots / Hyprland compositor adapter.

Uses the Hyprland IPC socket to query monitor information, and relies on
linux-wallpaperengine's native wlr-layer-shell Wayland backend for
rendering (no extra integration needed — lwe handles the protocol).

References
----------
* wlr-layer-shell-unstable-v1: https://wayland.app/protocols/wlr-layer-shell-unstable-v1
* Hyprland IPC: https://wiki.hyprland.org/IPC/
"""

from __future__ import annotations

import json
import logging
import os
import socket
import subprocess
from typing import Any

from mural.adapters.base import BaseAdapter
from mural.core.monitor_manager import Monitor, normalize_monitor_name

logger = logging.getLogger(__name__)


def _hyprland_socket_path() -> str | None:
    """Return the path to the Hyprland IPC socket, or ``None``."""
    sig = os.environ.get("HYPRLAND_INSTANCE_SIGNATURE", "")
    if not sig:
        return None
    return f"/tmp/hypr/{sig}/.socket.sock"


def _hyprctl_monitors() -> list[dict[str, Any]]:
    """Query Hyprland for monitor info via ``hyprctl monitors -j``."""
    try:
        result = subprocess.run(
            ["hyprctl", "monitors", "-j"],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode == 0:
            return json.loads(result.stdout)
    except (FileNotFoundError, subprocess.TimeoutExpired, json.JSONDecodeError):
        pass
    return []


def _socket_monitors() -> list[dict[str, Any]]:
    """Query monitor info via Hyprland's Unix IPC socket."""
    sock_path = _hyprland_socket_path()
    if not sock_path:
        return []
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
            s.settimeout(3)
            s.connect(sock_path)
            s.sendall(b"j/monitors")
            data = b""
            while True:
                chunk = s.recv(4096)
                if not chunk:
                    break
                data += chunk
        return json.loads(data.decode())
    except Exception as exc:
        logger.debug("Hyprland socket query failed: %s", exc)
        return []


class WlrAdapter(BaseAdapter):
    """Adapter for wlroots-based compositors (Hyprland, Sway).

    Monitor detection uses ``hyprctl monitors -j`` (Hyprland) or
    ``wlr-randr`` (other wlroots compositors) as fallbacks.

    Wallpaper apply/stop is routed through the Core Service D-Bus interface
    exactly as in :class:`~mural.adapters.plasma.PlasmaAdapter`.

    Args:
        session: Always ``"wayland"`` for wlroots compositors.
        core_proxy: dasbus proxy for ``com.mural.Core``.
    """

    name = "wlr"

    def __init__(self, session: str = "wayland", core_proxy: Any | None = None) -> None:
        self._session = session
        self._core = core_proxy

    def detect_monitors(self) -> list[Monitor]:
        """Detect monitors via Hyprland IPC or wlr-randr."""
        raw = _hyprctl_monitors() or _socket_monitors()
        if raw:
            return self._parse_hyprland_monitors(raw)
        return self._detect_via_wlr_randr()

    def apply_wallpaper(self, monitor: str, wallpaper_path: str) -> bool:
        """Route apply request to Core Service."""
        monitor = normalize_monitor_name(monitor)
        if not self._core:
            logger.error("[wlr] apply_wallpaper: Core Service proxy not set")
            return False
        try:
            return bool(self._core.SetWallpaper(monitor, wallpaper_path))
        except Exception as exc:
            logger.error("[wlr] SetWallpaper failed: %s", exc)
            return False

    def stop_wallpaper(self, monitor: str) -> bool:
        """Route stop request to Core Service."""
        return self.apply_wallpaper(monitor, "")

    def on_session_lock(self) -> None:
        if self._core:
            try:
                self._core.SetEnabled(False)
            except Exception:
                pass

    def on_session_unlock(self) -> None:
        if self._core:
            try:
                self._core.SetEnabled(True)
            except Exception:
                pass

    def capabilities(self) -> dict[str, bool]:
        return {
            "fullscreen_detection": False,
            "hotplug": True,
            "session_events": True,
            "multi_monitor": True,
        }

    # ------------------------------------------------------------------
    # Parsing helpers
    # ------------------------------------------------------------------

    def _parse_hyprland_monitors(self, raw: list[dict]) -> list[Monitor]:
        monitors: list[Monitor] = []
        for m in raw:
            name = normalize_monitor_name(m.get("name") or "")
            if not name:
                continue
            monitors.append(Monitor(
                name=name,
                width=m.get("width") or 0,
                height=m.get("height") or 0,
                x=m.get("x") or 0,
                y=m.get("y") or 0,
                is_primary=bool(m.get("focused")),
                connected=True,
            ))
        return monitors

    def _detect_via_wlr_randr(self) -> list[Monitor]:
        """Fallback: parse ``wlr-randr`` output."""
        try:
            result = subprocess.run(
                ["wlr-randr"], capture_output=True, text=True, timeout=5
            )
        except (FileNotFoundError, subprocess.TimeoutExpired):
            logger.warning("[wlr] Neither hyprctl nor wlr-randr is available")
            return []

        monitors: list[Monitor] = []
        current_name = ""
        for line in result.stdout.splitlines():
            if line and not line.startswith(" "):
                current_name = normalize_monitor_name(line.split()[0])
            if "current" in line and current_name:
                import re  # noqa: PLC0415
                m = re.search(r"(\d+)x(\d+)\+(-?\d+)\+(-?\d+)", line)
                if m:
                    monitors.append(Monitor(
                        name=current_name,
                        width=int(m.group(1)), height=int(m.group(2)),
                        x=int(m.group(3)), y=int(m.group(4)),
                        connected=True,
                    ))
        return monitors
