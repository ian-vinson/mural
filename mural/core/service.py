# mural/core/service.py
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

"""Mural Core session service.

Runs as a systemd user service (``mural-core.service``).  Owns the
linux-wallpaperengine subprocess lifetime, exposes a D-Bus interface for
the GUI to drive, and persists wallpaper assignments across GUI restarts.

D-Bus service name:  ``com.mural.Core``
D-Bus object path:   ``/com/mural/Core``

Start without systemd (for development):

    python -m mural.core.service [--debug]
"""

import argparse
import json
import logging
import os
import signal
import sys
from pathlib import Path
from typing import Any

# GLib must be imported before dasbus touches the event loop.
import gi
gi.require_version("GLib", "2.0")
from gi.repository import GLib  # noqa: E402

from dasbus.connection import SessionMessageBus
from dasbus.error import DBusError
from dasbus.server.interface import dbus_interface
from dasbus.typing import Str, Bool, List, Dict, Variant

from mural.backend.discovery import discover, DiscoveryResult
from mural.backend.runner import BackendRunner, WallpaperAssignment
from mural.core.monitor_manager import MonitorManager
from mural.detection import detect, DetectionResult

logger = logging.getLogger(__name__)

DBUS_SERVICE_NAME = "com.mural.Core"
DBUS_OBJECT_PATH = "/com/mural/Core"


# ---------------------------------------------------------------------------
# D-Bus interface specification
# ---------------------------------------------------------------------------

@dbus_interface(DBUS_SERVICE_NAME)
class IMuralCore:
    """D-Bus interface contract for the Mural Core service.

    This class is the authoritative specification used by dasbus to
    generate introspection XML and client proxies.  The implementation
    lives in :class:`MuralCoreService`.
    """

    def SetWallpaper(self, monitor: Str, path: Str) -> Bool:
        """Apply a wallpaper to a named monitor.

        Args:
            monitor: Output name, e.g. ``"DP-3"``.
            path: Absolute path to wallpaper directory or file.

        Returns:
            ``True`` on success, ``False`` on error.
        """
        ...

    def GetCurrentWallpaper(self, monitor: Str) -> Str:
        """Return the active wallpaper path for *monitor*.

        Returns:
            Absolute path string, or an empty string if none is set.
        """
        ...

    def GetMonitors(self) -> List[Str]:
        """Return the names of all connected monitors.

        Returns:
            List of output names in KDE/lwe format.
        """
        ...

    def SetEnabled(self, enabled: Bool) -> None:
        """Pause or resume wallpaper rendering on all monitors.

        Args:
            enabled: ``False`` stops lwe; ``True`` restarts it.
        """
        ...

    def GetStatus(self) -> Dict[Str, Variant]:
        """Return a status snapshot.

        Keys:
            ``running``   (bool)  — whether lwe is currently running.
            ``pid``       (int)   — lwe PID, or ``0`` if not running.
            ``monitors``  (list)  — connected monitor names.
            ``desktop``   (str)   — detected desktop environment.
            ``session``   (str)   — display server session type.
            ``version``   (str)   — Mural version string.
        """
        ...


# ---------------------------------------------------------------------------
# Service implementation
# ---------------------------------------------------------------------------

class MuralCoreService(IMuralCore):
    """Implementation of the Mural Core D-Bus service.

    This object is registered on the session bus and drives the full
    wallpaper lifecycle: detection, assignment loading, subprocess
    management, and responding to GUI commands.

    Args:
        detection: Pre-computed :class:`~mural.detection.DetectionResult`.
        discovery: Pre-computed :class:`~mural.backend.discovery.DiscoveryResult`.
        loop: GLib main loop; stored so ``Quit()`` can stop it.
    """

    VERSION = "0.1.0-alpha"

    def __init__(
        self,
        detection: DetectionResult,
        discovery: DiscoveryResult,
        loop: GLib.MainLoop,
    ) -> None:
        self._detection = detection
        self._discovery = discovery
        self._loop = loop
        self._enabled = True

        self._monitor_manager = MonitorManager(
            session=detection.session,
            desktop=detection.desktop,
        )
        self._runner: BackendRunner | None = None

        if discovery.binary_found:
            self._runner = BackendRunner(
                binary_path=discovery.binary,          # type: ignore[arg-type]
                assets_path=discovery.assets_path,
                on_unexpected_exit=self._on_lwe_exit,
                auto_restart=True,
            )

    # ------------------------------------------------------------------
    # Startup / shutdown helpers (not exposed on D-Bus)
    # ------------------------------------------------------------------

    def initialise(self) -> None:
        """Run startup tasks: kill orphans, detect monitors, apply saved wallpapers.

        Called once before entering the main loop.
        """
        if self._runner:
            BackendRunner.kill_orphans()

        self._monitor_manager.detect()
        self._monitor_manager.load_assignments()
        self._apply_all()

    def shutdown(self) -> None:
        """Stop the lwe subprocess cleanly.  Called on service exit."""
        if self._runner and self._runner.is_running():
            logger.info("Stopping lwe subprocess")
            self._runner.stop()

    # ------------------------------------------------------------------
    # D-Bus methods
    # ------------------------------------------------------------------

    def SetWallpaper(self, monitor: str, path: str) -> bool:  # type: ignore[override]
        """Apply *path* as the wallpaper for *monitor*."""
        logger.info("SetWallpaper(%r, %r)", monitor, path)

        if not self._runner:
            logger.error("SetWallpaper: lwe binary not found — cannot apply wallpaper")
            return False

        wp_path = Path(path)
        if not wp_path.exists():
            logger.error("SetWallpaper: path does not exist: %s", path)
            return False

        self._monitor_manager.assign_wallpaper(monitor, path)
        return self._apply_all()

    def GetCurrentWallpaper(self, monitor: str) -> str:  # type: ignore[override]
        """Return the current wallpaper path for *monitor*."""
        assignment = self._monitor_manager.get_assignment(monitor)
        if assignment:
            return assignment.wallpaper
        return ""

    def GetMonitors(self) -> list[str]:  # type: ignore[override]
        """Return connected monitor names, re-detecting if the list is empty."""
        if not self._monitor_manager.monitors:
            self._monitor_manager.detect()
        return [m.name for m in self._monitor_manager.monitors]

    def SetEnabled(self, enabled: bool) -> None:  # type: ignore[override]
        """Pause (``False``) or resume (``True``) wallpaper rendering."""
        logger.info("SetEnabled(%r)", enabled)
        self._enabled = enabled

        if not self._runner:
            return

        if enabled:
            self._apply_all()
        else:
            self._runner.stop()

    def GetStatus(self) -> dict[str, Any]:  # type: ignore[override]
        """Return a dict status snapshot (serialised as D-Bus a{sv})."""
        running = bool(self._runner and self._runner.is_running())
        pid = (self._runner.pid or 0) if self._runner else 0

        return {
            "running": GLib.Variant("b", running),
            "pid": GLib.Variant("i", pid),
            "monitors": GLib.Variant("as", self.GetMonitors()),
            "desktop": GLib.Variant("s", self._detection.desktop),
            "session": GLib.Variant("s", self._detection.session),
            "version": GLib.Variant("s", self.VERSION),
        }

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _apply_all(self) -> bool:
        """Build assignments and (re)start lwe with all enabled monitors."""
        if not self._runner:
            return False
        if not self._enabled:
            logger.debug("_apply_all: rendering is disabled, skipping")
            return False

        active = self._monitor_manager.active_assignments()
        if not active:
            logger.info("No active wallpaper assignments — lwe not started")
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

    def _on_lwe_exit(self, returncode: int) -> None:
        """Callback invoked by BackendRunner when lwe exits unexpectedly."""
        logger.warning(
            "lwe exited unexpectedly (returncode=%d); auto-restart is handled by runner",
            returncode,
        )


# ---------------------------------------------------------------------------
# Bus registration and main loop
# ---------------------------------------------------------------------------

def _register_signal_handlers(loop: GLib.MainLoop, service: MuralCoreService) -> None:
    """Register SIGTERM / SIGINT handlers that stop the GLib loop cleanly."""

    def _handle_signal(sig: int, _frame: Any) -> None:
        logger.info("Received signal %d — shutting down", sig)
        service.shutdown()
        loop.quit()

    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)


def run_service(debug: bool = False) -> int:
    """Start the Mural Core service and block until shutdown.

    This is the main entry point called by ``python -m mural.core.service``
    and by the systemd unit's ``ExecStart``.

    Args:
        debug: Enable ``DEBUG``-level logging when ``True``.

    Returns:
        Exit code (0 on clean shutdown, 1 on startup failure).
    """
    logging.basicConfig(
        level=logging.DEBUG if debug else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    logger.info("Mural Core Service starting (debug=%s)", debug)

    # Detect environment.
    detection = detect()
    discovery = discover()

    if not discovery.binary_found:
        logger.error(
            "linux-wallpaperengine is not installed. "
            "Service will start but cannot render wallpapers."
        )

    loop = GLib.MainLoop()
    service = MuralCoreService(detection=detection, discovery=discovery, loop=loop)

    # Register on session D-Bus.
    try:
        bus = SessionMessageBus()
        bus.publish_object(DBUS_OBJECT_PATH, service)
        bus.register_service(DBUS_SERVICE_NAME)
        logger.info(
            "D-Bus service registered: name=%s path=%s",
            DBUS_SERVICE_NAME,
            DBUS_OBJECT_PATH,
        )
    except DBusError as exc:
        logger.error("Failed to register D-Bus service: %s", exc)
        logger.error(
            "Is another instance running? Check: "
            "dbus-send --session --print-reply "
            "--dest=org.freedesktop.DBus /org/freedesktop/DBus "
            "org.freedesktop.DBus.ListNames"
        )
        return 1

    _register_signal_handlers(loop, service)

    # Initialise (kill orphans, detect monitors, apply saved wallpapers).
    service.initialise()

    logger.info("Mural Core Service ready — entering main loop")
    try:
        loop.run()
    except KeyboardInterrupt:
        service.shutdown()

    logger.info("Mural Core Service stopped")
    return 0


# ---------------------------------------------------------------------------
# Module entry point
# ---------------------------------------------------------------------------

def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="mural-core",
        description="Mural Core animated wallpaper session service",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Enable debug-level logging",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    """CLI entry point: ``python -m mural.core.service``."""
    args = _parse_args(argv)
    sys.exit(run_service(debug=args.debug))


if __name__ == "__main__":
    main()
