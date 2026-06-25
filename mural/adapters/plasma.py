# mural/adapters/plasma.py
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

"""KDE Plasma 6 desktop environment adapter.

Responsibilities
----------------
* Monitor detection via ``kscreen-doctor`` or PySide6 QScreen API.
* Routing wallpaper apply/stop requests to the Core Service D-Bus interface.
* Fullscreen detection via the KWin scripting D-Bus API.
* Responding to Plasma session events (lock, suspend, monitor hotplug).

Architecture note
-----------------
This adapter does NOT call linux-wallpaperengine directly.  It translates
Plasma-specific concerns (output naming, KWin events) into generic Core
Service D-Bus calls.  The Core Service owns the lwe subprocess.

References
----------
* KDE Plasma wallpaper plugin API: https://develop.kde.org/docs/plasma/
* KWin scripting API: https://develop.kde.org/docs/plasma/scripting/api/
* wallpaper-engine-kde-plugin (reference): https://github.com/catsout/wallpaper-engine-kde-plugin
"""

from __future__ import annotations

import logging
import subprocess
from typing import Any

from mural.adapters.base import BaseAdapter
from mural.core.monitor_manager import Monitor, MonitorManager, normalize_monitor_name

logger = logging.getLogger(__name__)

# KWin scripting D-Bus coordinates.
_KWIN_SERVICE = "org.kde.KWin"
_KWIN_SCRIPTING_PATH = "/Scripting"
_KWIN_SCRIPTING_IFACE = "org.kde.kwin.Scripting"

# KDE session manager D-Bus coordinates (for lock/suspend signals).
_KSCREENLOCKER_SERVICE = "org.freedesktop.ScreenSaver"
_KSCREENLOCKER_PATH = "/ScreenSaver"


class PlasmaAdapter(BaseAdapter):
    """Adapter for KDE Plasma 6 (Wayland and X11).

    Args:
        session: Display server type — ``"wayland"`` or ``"x11"``.
        core_proxy: dasbus proxy for ``com.mural.Core``.  When ``None``
            the adapter operates in detection-only mode (useful in tests
            and during early startup before the Core Service registers).
    """

    name = "plasma"

    def __init__(
        self,
        session: str = "wayland",
        core_proxy: Any | None = None,
    ) -> None:
        self._session = session
        self._core = core_proxy
        self._monitor_manager = MonitorManager(session=session, desktop="plasma")
        self._fullscreen_detection_enabled = False
        self._kwin_script_id: int | None = None

    # ------------------------------------------------------------------
    # BaseAdapter — required
    # ------------------------------------------------------------------

    def detect_monitors(self) -> list[Monitor]:
        """Detect connected monitors using kscreen-doctor or QScreen.

        Returns:
            List of connected :class:`~mural.core.monitor_manager.Monitor`
            objects in KDE output-name format.
        """
        return self._monitor_manager.detect()

    def apply_wallpaper(self, monitor: str, wallpaper_path: str) -> bool:
        """Route a wallpaper apply request through the Core Service.

        Args:
            monitor: Output name in KDE/lwe format, e.g. ``"DP-3"``.
            wallpaper_path: Absolute path to the wallpaper directory or file.

        Returns:
            ``True`` on success, ``False`` if the Core Service call failed
            or is unavailable.
        """
        monitor = normalize_monitor_name(monitor)
        logger.info("[plasma] apply_wallpaper(monitor=%r, path=%r)", monitor, wallpaper_path)

        if self._core is None:
            logger.error("[plasma] apply_wallpaper: Core Service proxy not set")
            return False

        try:
            return bool(self._core.SetWallpaper(monitor, wallpaper_path))
        except Exception as exc:
            logger.error("[plasma] SetWallpaper D-Bus call failed: %s", exc)
            return False

    def stop_wallpaper(self, monitor: str) -> bool:
        """Stop the wallpaper on *monitor* by setting an empty path.

        Passing an empty string to SetWallpaper signals the Core Service
        to remove the assignment and stop lwe for that monitor.

        Args:
            monitor: Output name.

        Returns:
            ``True`` on success.
        """
        monitor = normalize_monitor_name(monitor)
        logger.info("[plasma] stop_wallpaper(monitor=%r)", monitor)

        if self._core is None:
            logger.error("[plasma] stop_wallpaper: Core Service proxy not set")
            return False

        try:
            return bool(self._core.SetWallpaper(monitor, ""))
        except Exception as exc:
            logger.error("[plasma] stop_wallpaper D-Bus call failed: %s", exc)
            return False

    # ------------------------------------------------------------------
    # BaseAdapter — optional overrides
    # ------------------------------------------------------------------

    def set_fullscreen_detection(self, enabled: bool) -> None:
        """Enable or disable fullscreen detection via KWin scripting.

        When enabled, a small KWin script is loaded that emits a signal
        whenever a window enters or leaves fullscreen mode.  The Core
        Service responds by pausing or resuming lwe.

        Args:
            enabled: ``True`` to enable, ``False`` to disable and unload
                any active KWin script.
        """
        logger.info("[plasma] set_fullscreen_detection(%r)", enabled)
        self._fullscreen_detection_enabled = enabled

        if enabled:
            self._load_kwin_fullscreen_script()
        else:
            self._unload_kwin_fullscreen_script()

    def on_monitor_connected(self, monitor: Monitor) -> None:
        """Re-detect monitors and re-apply saved wallpapers on hotplug."""
        logger.info("[plasma] Monitor connected: %s", monitor)
        self._monitor_manager.detect()
        self._monitor_manager.load_assignments()
        if self._core:
            try:
                # Ask Core Service to re-apply all assignments so the new
                # monitor picks up its saved wallpaper if one exists.
                self._core.SetEnabled(True)
            except Exception as exc:
                logger.warning("[plasma] Could not re-enable after hotplug: %s", exc)

    def on_monitor_disconnected(self, monitor_name: str) -> None:
        """Update the internal monitor list when an output is removed."""
        logger.info("[plasma] Monitor disconnected: %s", monitor_name)
        self._monitor_manager.detect()

    def on_session_lock(self) -> None:
        """Pause rendering when the screen is locked."""
        logger.info("[plasma] Session locked — pausing lwe")
        if self._core:
            try:
                self._core.SetEnabled(False)
            except Exception as exc:
                logger.warning("[plasma] SetEnabled(False) on lock failed: %s", exc)

    def on_session_unlock(self) -> None:
        """Resume rendering when the screen is unlocked."""
        logger.info("[plasma] Session unlocked — resuming lwe")
        if self._core:
            try:
                self._core.SetEnabled(True)
            except Exception as exc:
                logger.warning("[plasma] SetEnabled(True) on unlock failed: %s", exc)

    def on_suspend(self) -> None:
        """Pause rendering before system suspend."""
        logger.info("[plasma] Suspending — pausing lwe")
        if self._core:
            try:
                self._core.SetEnabled(False)
            except Exception as exc:
                logger.warning("[plasma] SetEnabled(False) on suspend failed: %s", exc)

    def on_resume(self) -> None:
        """Resume rendering after system wakes from suspend."""
        logger.info("[plasma] Resumed — restarting lwe")
        if self._core:
            try:
                self._core.SetEnabled(True)
            except Exception as exc:
                logger.warning("[plasma] SetEnabled(True) on resume failed: %s", exc)

    def capabilities(self) -> dict[str, bool]:
        """Report Plasma adapter capabilities."""
        return {
            "fullscreen_detection": True,
            "hotplug": True,
            "session_events": True,
            "multi_monitor": True,
        }

    # ------------------------------------------------------------------
    # KWin scripting helpers
    # ------------------------------------------------------------------

    # Minimal KWin script that emits a D-Bus signal when any window
    # enters or leaves fullscreen.  Loaded dynamically at runtime so
    # Mural does not require a permanently installed KWin script.
    _FULLSCREEN_KWIN_SCRIPT = """
workspace.clientFullScreenChanged.connect(function(client, fullscreen, user) {
    callDBus(
        'com.mural.Core',
        '/com/mural/Core',
        'com.mural.Core',
        fullscreen ? 'SetEnabled' : 'SetEnabled',
        !fullscreen
    );
});
""".strip()

    def _load_kwin_fullscreen_script(self) -> None:
        """Load the fullscreen detection script into the KWin scripting engine."""
        try:
            from dasbus.connection import SessionMessageBus  # noqa: PLC0415
            bus = SessionMessageBus()
            kwin = bus.get_proxy(_KWIN_SERVICE, _KWIN_SCRIPTING_PATH)
            script_id: int = kwin.loadScript(
                self._FULLSCREEN_KWIN_SCRIPT,
                "mural-fullscreen-detect",
            )
            kwin.start()
            self._kwin_script_id = script_id
            logger.info("[plasma] KWin fullscreen script loaded (id=%d)", script_id)
        except Exception as exc:
            logger.warning(
                "[plasma] Could not load KWin fullscreen script: %s. "
                "Fullscreen detection will be unavailable.",
                exc,
            )

    def _unload_kwin_fullscreen_script(self) -> None:
        """Unload the previously loaded KWin fullscreen detection script."""
        if self._kwin_script_id is None:
            return
        try:
            from dasbus.connection import SessionMessageBus  # noqa: PLC0415
            bus = SessionMessageBus()
            kwin = bus.get_proxy(_KWIN_SERVICE, _KWIN_SCRIPTING_PATH)
            kwin.unloadScript(self._kwin_script_id)
            logger.info(
                "[plasma] KWin fullscreen script unloaded (id=%d)",
                self._kwin_script_id,
            )
        except Exception as exc:
            logger.warning("[plasma] Could not unload KWin script: %s", exc)
        finally:
            self._kwin_script_id = None

    # ------------------------------------------------------------------
    # Plasma plugin installation helper
    # ------------------------------------------------------------------

    @staticmethod
    def install_plasma_plugin(plugin_src_dir: str) -> bool:
        """Copy the Plasma wallpaper plugin to the user's local plugin directory.

        Args:
            plugin_src_dir: Path to the ``plasma-plugin/`` directory in
                the Mural source tree.

        Returns:
            ``True`` if the plugin was installed successfully.
        """
        import shutil  # noqa: PLC0415
        from pathlib import Path  # noqa: PLC0415

        src = Path(plugin_src_dir)
        dst = Path("~/.local/share/plasma/wallpapers/com.mural.wallpaper").expanduser()

        if not src.exists():
            logger.error("Plugin source directory not found: %s", src)
            return False

        try:
            if dst.exists():
                shutil.rmtree(dst)
            shutil.copytree(src, dst)
            logger.info("Plasma plugin installed to %s", dst)
        except OSError as exc:
            logger.error("Failed to install Plasma plugin: %s", exc)
            return False

        # Ask plasmashell to reload plugins without a full restart.
        try:
            subprocess.run(
                ["plasmashell", "--replace"],
                check=False,
                start_new_session=True,
                timeout=2,
            )
        except (FileNotFoundError, subprocess.TimeoutExpired):
            logger.debug("Could not signal plasmashell to reload — restart it manually")

        return True
