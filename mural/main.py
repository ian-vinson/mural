# mural/main.py
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

"""Mural GUI application entry point.

Launches the PySide6 interface and connects it to the Mural Core session
service via D-Bus.  If the Core Service is not running it is started
automatically as a background subprocess.

Usage::

    python -m mural.main          # normal launch
    python -m mural.main --debug  # verbose logging
    python -m mural.main --minimized  # start minimized to tray
"""

from __future__ import annotations

import argparse
import logging
import subprocess
import sys
import time
from typing import Any

from PySide6.QtGui import QAction, QIcon
from PySide6.QtWidgets import (
    QApplication,
    QLabel,
    QMainWindow,
    QMenu,
    QMessageBox,
    QSystemTrayIcon,
    QVBoxLayout,
    QWidget,
)

logger = logging.getLogger(__name__)

APP_NAME = "Mural"
APP_VERSION = "0.2.0-alpha"
DBUS_SERVICE_NAME = "com.mural.Core"
DBUS_OBJECT_PATH = "/com/mural/Core"

# How long to wait for the Core Service to appear on the bus after launch.
_SERVICE_WAIT_TIMEOUT = 10.0   # seconds
_SERVICE_WAIT_INTERVAL = 0.25  # seconds


# ---------------------------------------------------------------------------
# Core Service connection
# ---------------------------------------------------------------------------

def _connect_to_service() -> Any | None:
    """Return a dasbus proxy for the Core Service, or ``None`` on failure.

    Does not block; the caller is responsible for checking availability
    via :func:`_wait_for_service` first.
    """
    try:
        from dasbus.connection import SessionMessageBus  # noqa: PLC0415
        bus = SessionMessageBus()
        proxy = bus.get_proxy(DBUS_SERVICE_NAME, DBUS_OBJECT_PATH)
        # Force a lightweight call to confirm the service is alive.
        _ = proxy.GetMonitors()
        return proxy
    except Exception as exc:
        logger.debug("Core Service not reachable: %s", exc)
        return None


def _service_is_running() -> bool:
    """Return ``True`` if the Core Service name is registered on the session bus."""
    try:
        from dasbus.connection import SessionMessageBus  # noqa: PLC0415
        bus = SessionMessageBus()
        proxy = bus.get_proxy(
            "org.freedesktop.DBus",
            "/org/freedesktop/DBus",
        )
        names: list[str] = proxy.ListNames()
        return DBUS_SERVICE_NAME in names
    except Exception:
        return False


def _start_service_subprocess() -> subprocess.Popen | None:
    """Launch ``mural-core`` as a detached background subprocess.

    Tries ``mural-core`` (installed entry point) first, then falls back
    to ``python -m mural.core.service`` for development environments.

    Returns:
        The :class:`subprocess.Popen` handle, or ``None`` if launch failed.
    """
    for cmd in (
        ["mural-core"],
        [sys.executable, "-m", "mural.core.service"],
    ):
        try:
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
            logger.info("Started Core Service: %s (pid=%d)", " ".join(cmd), proc.pid)
            return proc
        except FileNotFoundError:
            continue
        except OSError as exc:
            logger.warning("Failed to start Core Service with %s: %s", cmd, exc)

    return None


def _wait_for_service(timeout: float = _SERVICE_WAIT_TIMEOUT) -> bool:
    """Block until the Core Service appears on the bus, or *timeout* expires.

    Returns:
        ``True`` if the service became available within *timeout* seconds.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if _service_is_running():
            return True
        time.sleep(_SERVICE_WAIT_INTERVAL)
    return False


def _ensure_service_running(parent_widget: QWidget | None = None) -> Any | None:
    """Ensure the Core Service is running and return a D-Bus proxy.

    If the service is not running, starts it automatically and waits for
    it to register on the bus.  Shows an error dialog if startup fails.

    Args:
        parent_widget: Qt parent for error dialogs.

    Returns:
        A dasbus proxy object, or ``None`` if the service could not be started.
    """
    if _service_is_running():
        logger.debug("Core Service already running")
        return _connect_to_service()

    logger.info("Core Service not running — starting it")
    proc = _start_service_subprocess()
    if proc is None:
        msg = (
            "Could not start the Mural Core Service.\n\n"
            "Make sure mural-core is installed, or run:\n"
            "  python -m mural.core.service\n"
            "in a separate terminal."
        )
        QMessageBox.critical(parent_widget, "Mural — Service Error", msg)
        return None

    if not _wait_for_service():
        msg = (
            "The Mural Core Service started but did not appear on the D-Bus "
            f"within {_SERVICE_WAIT_TIMEOUT:.0f} seconds.\n\n"
            "Check the service logs:\n"
            "  journalctl --user -u mural-core.service -f\n"
            "or:\n"
            "  python -m mural.core.service --debug"
        )
        QMessageBox.critical(parent_widget, "Mural — Service Timeout", msg)
        return None

    return _connect_to_service()


# ---------------------------------------------------------------------------
# Placeholder main window (replaced by gui/mainwindow.py in Phase 3)
# ---------------------------------------------------------------------------

class _PlaceholderWindow(QMainWindow):
    """Minimal window shown until the full GUI is implemented in Phase 3."""

    def __init__(self, core_proxy: Any | None) -> None:
        super().__init__()
        self._core = core_proxy
        self.setWindowTitle(f"Mural {APP_VERSION}")
        self.setMinimumSize(640, 480)
        self._build_ui()

    def _build_ui(self) -> None:
        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)

        if self._core is not None:
            try:
                status = self._core.GetStatus()
                desktop = str(status.get("desktop", "unknown"))
                session = str(status.get("session", "unknown"))
                running = bool(status.get("running", False))
                monitors: list[str] = self._core.GetMonitors()
                info = (
                    f"Core Service: connected\n"
                    f"Desktop: {desktop}   Session: {session}\n"
                    f"lwe running: {running}\n"
                    f"Monitors: {', '.join(monitors) or '(none detected)'}\n\n"
                    "Full GUI coming in Phase 3.\n"
                    "Use SetWallpaper via D-Bus to test rendering:\n\n"
                    "  from dasbus.connection import SessionMessageBus\n"
                    "  bus = SessionMessageBus()\n"
                    "  core = bus.get_proxy('com.mural.Core', '/com/mural/Core')\n"
                    "  core.SetWallpaper('DP-3', '/path/to/wallpaper')"
                )
            except Exception as exc:
                info = f"Core Service connected but status call failed:\n{exc}"
        else:
            info = (
                "Core Service: NOT connected\n\n"
                "Wallpapers cannot be applied without the Core Service.\n"
                "Start it with:  python -m mural.core.service --debug"
            )

        label = QLabel(info)
        label.setWordWrap(True)
        label.setMargin(24)
        layout.addWidget(label)


# ---------------------------------------------------------------------------
# System tray
# ---------------------------------------------------------------------------

def _build_tray(app: QApplication, window: QMainWindow) -> QSystemTrayIcon:
    """Create and return the system tray icon with its context menu.

    Args:
        app: The running :class:`QApplication` (used for quit action).
        window: The main window to show/hide from the tray.

    Returns:
        A configured (but not yet shown) :class:`QSystemTrayIcon`.
    """
    tray = QSystemTrayIcon(app)

    # Fallback to a named theme icon; a custom icon is added in Phase 3.
    icon = QIcon.fromTheme("video-display", QIcon.fromTheme("preferences-desktop-wallpaper"))
    tray.setIcon(icon)
    tray.setToolTip(f"Mural {APP_VERSION}")

    menu = QMenu()

    open_action = QAction("Open Mural", menu)
    open_action.triggered.connect(window.show)
    open_action.triggered.connect(window.raise_)
    menu.addAction(open_action)

    menu.addSeparator()

    quit_action = QAction("Quit", menu)
    quit_action.triggered.connect(app.quit)
    menu.addAction(quit_action)

    tray.setContextMenu(menu)

    # Double-click restores the window.
    def _on_tray_activated(reason: QSystemTrayIcon.ActivationReason) -> None:
        if reason == QSystemTrayIcon.ActivationReason.DoubleClick:
            window.show()
            window.raise_()

    tray.activated.connect(_on_tray_activated)

    return tray


# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------

def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="mural",
        description="Mural animated wallpaper platform",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Enable debug-level logging",
    )
    parser.add_argument(
        "--minimized",
        action="store_true",
        help="Start minimized to the system tray",
    )
    parser.add_argument(
        "--no-tray",
        action="store_true",
        dest="no_tray",
        help="Do not create a system tray icon",
    )
    parser.add_argument(
        "--screensaver",
        action="store_true",
        help="Launch current wallpaper as a fullscreen screensaver window",
    )
    return parser.parse_args(argv)


# ---------------------------------------------------------------------------
# Screensaver mode
# ---------------------------------------------------------------------------

def _run_screensaver(argv: list[str] | None = None) -> int:
    """Launch the current wallpaper in a fullscreen window for screensaver use."""
    from mural.backend.discovery import find_lwe_binary

    binary = find_lwe_binary()
    if not binary:
        print("mural: lwe binary not found — cannot run screensaver", file=sys.stderr)
        return 1

    # Try to get the current wallpaper from the running service.
    wallpaper = ""
    core = _connect_to_service()
    if core:
        try:
            monitors = list(core.GetMonitors())
            if monitors:
                wallpaper = core.GetCurrentWallpaper(monitors[0]) or ""
        except Exception:
            pass

    if not wallpaper:
        print("mural: no wallpaper is currently active", file=sys.stderr)
        return 1

    # Determine screen resolution via Qt (minimal QApplication).
    app = QApplication(sys.argv if argv is None else [sys.argv[0]] + list(argv))
    screen = app.primaryScreen()
    if screen:
        geo = screen.geometry()
        w, h = geo.width(), geo.height()
    else:
        w, h = 1920, 1080

    proc = subprocess.Popen([
        str(binary),
        "--window", f"0x0x{w}x{h}",
        wallpaper,
    ])
    try:
        proc.wait()
    except KeyboardInterrupt:
        proc.terminate()
        try:
            proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            proc.kill()
    return proc.returncode if proc.returncode is not None else 0


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    """Launch the Mural GUI application.

    Args:
        argv: Command-line arguments (defaults to ``sys.argv[1:]``).

    Returns:
        Process exit code.
    """
    args = _parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.debug else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    logger.info("Mural %s starting", APP_VERSION)

    if args.screensaver:
        return _run_screensaver(argv)

    app = QApplication(sys.argv if argv is None else [sys.argv[0]] + list(argv))
    app.setApplicationName(APP_NAME)
    app.setApplicationVersion(APP_VERSION)
    app.setOrganizationName("Mural")
    # Keep the process alive when the main window is closed (tray keeps it running).
    app.setQuitOnLastWindowClosed(False)

    # Connect to (or start) the Core Service.
    core_proxy = _ensure_service_running()
    if core_proxy is None:
        logger.warning("Running in degraded mode: Core Service unavailable")

    # Build the main window.
    # Phase 3 will replace _PlaceholderWindow with the real MainWindow.
    try:
        from mural.gui.mainwindow import MainWindow  # noqa: PLC0415
        window: QMainWindow = MainWindow(core_proxy=core_proxy)
    except (ImportError, Exception) as exc:
        logger.debug("MainWindow not yet implemented (%s) — using placeholder", exc)
        window = _PlaceholderWindow(core_proxy=core_proxy)

    # System tray.
    tray: QSystemTrayIcon | None = None
    if not args.no_tray and QSystemTrayIcon.isSystemTrayAvailable():
        tray = _build_tray(app, window)
        tray.show()
    else:
        # No tray: closing the window should quit.
        app.setQuitOnLastWindowClosed(True)

    if not args.minimized:
        window.show()

    logger.info("Mural GUI ready")
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
