# mural/core/autostart.py
#
# Mural — Animated Wallpaper Platform for Linux
# Copyright (C) 2024  Mural Contributors
# GPL v3 — see LICENSE

"""systemd user service management for Mural Core autostart.

Provides helpers to enable, disable, start, stop, and query the status
of ``mural-core.service`` without requiring the caller to shell out to
``systemctl`` directly.
"""

from __future__ import annotations

import logging
import shutil
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)

_UNIT_NAME = "mural-core.service"
_UNIT_INSTALL_DIR = Path("~/.config/systemd/user").expanduser()
_UNIT_SOURCE = Path(__file__).parent.parent.parent / "systemd" / _UNIT_NAME


def _systemctl(*args: str, check: bool = False) -> subprocess.CompletedProcess:
    """Run ``systemctl --user <args>`` and return the result."""
    return subprocess.run(
        ["systemctl", "--user", *args],
        capture_output=True,
        text=True,
        timeout=15,
        check=check,
    )


# ---------------------------------------------------------------------------
# Status queries
# ---------------------------------------------------------------------------

def is_enabled() -> bool:
    """Return ``True`` if mural-core.service is enabled for autostart."""
    try:
        result = _systemctl("is-enabled", "--quiet", _UNIT_NAME)
        return result.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


def is_running() -> bool:
    """Return ``True`` if mural-core.service is currently active."""
    try:
        result = _systemctl("is-active", "--quiet", _UNIT_NAME)
        return result.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


def status() -> dict[str, str]:
    """Return a dict with ``enabled`` and ``active`` string values."""
    return {
        "enabled": "yes" if is_enabled() else "no",
        "active":  "yes" if is_running() else "no",
    }


# ---------------------------------------------------------------------------
# Unit file management
# ---------------------------------------------------------------------------

def install_unit() -> bool:
    """Copy the bundled ``mural-core.service`` unit file to the user systemd dir.

    Returns:
        ``True`` if the unit was installed successfully.
    """
    if not _UNIT_SOURCE.exists():
        logger.error("Unit source not found: %s", _UNIT_SOURCE)
        return False

    _UNIT_INSTALL_DIR.mkdir(parents=True, exist_ok=True)
    dest = _UNIT_INSTALL_DIR / _UNIT_NAME

    try:
        shutil.copy2(_UNIT_SOURCE, dest)
        logger.info("Installed %s to %s", _UNIT_NAME, dest)
    except OSError as exc:
        logger.error("Failed to install unit file: %s", exc)
        return False

    return _daemon_reload()


def uninstall_unit() -> bool:
    """Remove the installed unit file.

    Returns:
        ``True`` if removed (or it was not installed).
    """
    dest = _UNIT_INSTALL_DIR / _UNIT_NAME
    if not dest.exists():
        return True
    try:
        dest.unlink()
        logger.info("Removed %s", dest)
        _daemon_reload()
        return True
    except OSError as exc:
        logger.error("Failed to remove unit file: %s", exc)
        return False


def _daemon_reload() -> bool:
    """Run ``systemctl --user daemon-reload``."""
    try:
        _systemctl("daemon-reload")
        return True
    except Exception as exc:
        logger.warning("daemon-reload failed: %s", exc)
        return False


# ---------------------------------------------------------------------------
# Service lifecycle
# ---------------------------------------------------------------------------

def enable(start_now: bool = True) -> bool:
    """Enable mural-core.service for autostart, optionally starting it now.

    Args:
        start_now: If ``True``, also start the service immediately.

    Returns:
        ``True`` on success.
    """
    try:
        args = ["enable"]
        if start_now:
            args.append("--now")
        args.append(_UNIT_NAME)
        result = _systemctl(*args)
        if result.returncode != 0:
            logger.error("enable failed: %s", result.stderr.strip())
            return False
        logger.info("mural-core.service enabled (start_now=%s)", start_now)
        return True
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        logger.error("systemctl not available: %s", exc)
        return False


def disable(stop_now: bool = True) -> bool:
    """Disable mural-core.service, optionally stopping it now.

    Args:
        stop_now: If ``True``, also stop the service immediately.

    Returns:
        ``True`` on success.
    """
    try:
        args = ["disable"]
        if stop_now:
            args.append("--now")
        args.append(_UNIT_NAME)
        result = _systemctl(*args)
        if result.returncode != 0:
            logger.error("disable failed: %s", result.stderr.strip())
            return False
        logger.info("mural-core.service disabled (stop_now=%s)", stop_now)
        return True
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        logger.error("systemctl not available: %s", exc)
        return False


def start() -> bool:
    """Start mural-core.service immediately (without enabling autostart)."""
    try:
        result = _systemctl("start", _UNIT_NAME)
        return result.returncode == 0
    except Exception:
        return False


def stop() -> bool:
    """Stop mural-core.service immediately."""
    try:
        result = _systemctl("stop", _UNIT_NAME)
        return result.returncode == 0
    except Exception:
        return False


def restart() -> bool:
    """Restart mural-core.service."""
    try:
        result = _systemctl("restart", _UNIT_NAME)
        return result.returncode == 0
    except Exception:
        return False
