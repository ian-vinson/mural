# mural/utils/hyprland.py
# GPL v3 — see LICENSE

"""Hyprland IPC socket interface for wallpaper color synchronization."""

from __future__ import annotations

import os
import socket
from pathlib import Path


def is_hyprland() -> bool:
    """Return True if running inside a Hyprland compositor session."""
    return bool(os.environ.get("HYPRLAND_INSTANCE_SIGNATURE"))


def _socket_path() -> Path | None:
    sig = os.environ.get("HYPRLAND_INSTANCE_SIGNATURE")
    if not sig:
        return None
    runtime = os.environ.get("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")
    return Path(runtime) / "hypr" / sig / ".socket.sock"


def send_command(cmd: str) -> str | None:
    """Send *cmd* to the Hyprland IPC socket and return the response, or None on failure."""
    path = _socket_path()
    if not path or not path.exists():
        return None
    try:
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(3.0)
        sock.connect(str(path))
        sock.sendall(cmd.encode())
        response = b""
        while True:
            chunk = sock.recv(4096)
            if not chunk:
                break
            response += chunk
        sock.close()
        return response.decode(errors="replace")
    except OSError:
        return None


def set_border_color(hex_color: str) -> bool:
    """Set the active border color via Hyprland IPC.

    Converts ``#rrggbb`` to ``rgba(rrggbbff)`` (fully opaque).
    """
    h = hex_color.lstrip("#")
    rgba = f"rgba({h}ff)"
    result = send_command(f"keyword general:col.active_border {rgba}")
    return result is not None


def set_inactive_border_color(hex_color: str) -> bool:
    """Set the inactive border color (50 % opacity) via Hyprland IPC."""
    h = hex_color.lstrip("#")
    rgba = f"rgba({h}88)"
    result = send_command(f"keyword general:col.inactive_border_color {rgba}")
    return result is not None


def get_hyprland_version() -> str | None:
    """Return the Hyprland version string, or None if not on Hyprland."""
    return send_command("version")
