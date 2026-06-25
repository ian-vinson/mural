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
import random
import signal
import sys
import threading
import time
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
from mural.config import config as _cfg, DOWNLOAD_DIR
from mural.core.playlist import Playlist, PlaylistStore
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
        """Apply a wallpaper to a named monitor."""
        ...

    def GetCurrentWallpaper(self, monitor: Str) -> Str:
        """Return the active wallpaper path for *monitor*, or ``""``."""
        ...

    def GetMonitors(self) -> List[Str]:
        """Return the names of all connected monitors."""
        ...

    def SetEnabled(self, enabled: Bool) -> None:
        """Pause (``False``) or resume (``True``) wallpaper rendering."""
        ...

    def GetStatus(self) -> Dict[Str, Variant]:
        """Return a status snapshot dict (keys: running, pid, monitors, ...)."""
        ...

    def ApplySettings(self) -> None:
        """Re-read playback settings from disk and restart lwe."""
        ...

    # ------------------------------------------------------------------
    # Playlist management
    # ------------------------------------------------------------------

    def GetPlaylistStatus(self) -> Str:
        """Return JSON: {timer_running, global_interval_minutes, playlists:[...]}."""
        ...

    def GetPlaylists(self) -> Str:
        """Return JSON array of all playlists (full data)."""
        ...

    def CreatePlaylist(self, name: Str) -> Str:
        """Create a new named playlist; returns the new playlist id."""
        ...

    def DeletePlaylist(self, playlist_id: Str) -> Bool:
        """Delete playlist *playlist_id*; returns ``True`` on success."""
        ...

    def SetPlaylistName(self, playlist_id: Str, name: Str) -> Bool:
        """Rename a playlist."""
        ...

    def AddToPlaylist(self, playlist_id: Str, wallpaper_path: Str) -> Bool:
        """Append *wallpaper_path* to playlist *playlist_id*."""
        ...

    def RemoveFromPlaylist(self, playlist_id: Str, index: int) -> Bool:
        """Remove the item at *index* from playlist *playlist_id*."""
        ...

    def ReorderPlaylist(self, playlist_id: Str, from_index: int, to_index: int) -> Bool:
        """Move item from *from_index* to *to_index* in playlist *playlist_id*."""
        ...

    def AssignPlaylistToMonitor(self, playlist_id: Str, monitor: Str) -> Bool:
        """Assign *monitor* to playlist *playlist_id*, removing it from any other."""
        ...

    def UnassignPlaylistFromMonitor(self, playlist_id: Str, monitor: Str) -> Bool:
        """Remove *monitor* from playlist *playlist_id*'s assignments."""
        ...

    def SetPlaylistShuffle(self, playlist_id: Str, shuffle: Bool) -> Bool:
        """Set shuffle mode for playlist *playlist_id*."""
        ...

    def SetPlaylistInterval(self, playlist_id: Str, interval_minutes: int) -> Bool:
        """Set per-playlist rotation interval (0 = use global setting)."""
        ...

    def SetItemDuration(self, playlist_id: Str, index: int, minutes: int) -> Bool:
        """Set per-item duration override for item at *index* (0 = playlist default)."""
        ...

    # ------------------------------------------------------------------
    # Power and app-rule status
    # ------------------------------------------------------------------

    def GetPowerStatus(self) -> Str:
        """Return current power source: ``"ac"``, ``"battery"``, or ``"unknown"``."""
        ...

    def GetAppRuleStatus(self) -> Str:
        """Return ``"paused:<appname>"`` or ``"running"``."""
        ...

    def GetScheduleStatus(self) -> Str:
        """Return the name of the currently active time-of-day slot, or ``"none"``."""
        ...


# ---------------------------------------------------------------------------
# Service implementation
# ---------------------------------------------------------------------------

class MuralCoreService(IMuralCore):
    """Implementation of the Mural Core D-Bus service."""

    VERSION = "0.1.0-alpha"

    # Steam Workshop search roots for the library fallback.
    _STEAM_ROOTS = (
        "~/.steam/steam",
        "~/.local/share/Steam",
        "~/.var/app/com.valvesoftware.Steam/.local/share/Steam",
        "~/snap/steam/common/.local/share/Steam",
    )
    _WORKSHOP_ID = "431960"

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

        # Playlist state
        self._playlists = PlaylistStore()
        self._playlists.load()
        self._tick_timer_id: int | None = None
        self._playlist_last_tick: dict[str, float] = {}      # playlist_id → epoch
        self._playlist_next_interval: dict[str, int] = {}    # playlist_id → effective minutes

        # Pause state: multiple reasons can suspend lwe simultaneously.
        # lwe only runs when this set is empty.
        self._pause_reasons: set[str] = set()  # {"battery", "app"}
        self._battery_on: bool | None = None   # last known battery state
        self._app_pause_name: str = ""         # app name causing pause
        self._battery_timer_id: int | None = None
        self._app_timer_id: int | None = None

        # Time-of-day schedule state
        self._schedule_timer_id: int | None = None
        self._schedule_last_slot: str = ""

        _cfg.load()
        if discovery.binary_found:
            self._runner = BackendRunner(
                binary_path=discovery.binary,          # type: ignore[arg-type]
                assets_path=discovery.assets_path,
                on_unexpected_exit=self._on_lwe_exit,
                auto_restart=True,
                fps_limit=int(_cfg.get("fps_limit", 30)),
                mute_audio=bool(_cfg.get("mute_audio", False)),
                fullscreen_pause=bool(_cfg.get("fullscreen_pause", True)),
                disable_mouse=bool(_cfg.get("disable_mouse", False)),
                disable_parallax=bool(_cfg.get("disable_parallax", False)),
            )

    # ------------------------------------------------------------------
    # Startup / shutdown
    # ------------------------------------------------------------------

    def initialise(self) -> None:
        """Kill orphans, detect monitors, apply saved wallpapers, start timers."""
        if self._runner:
            BackendRunner.kill_orphans()

        self._monitor_manager.detect()
        self._monitor_manager.load_assignments()

        # Prime pause conditions before starting lwe so we don't start
        # then immediately stop when on battery or a ruled app is running.
        self._check_battery_pause(initial=True)
        self._check_app_rule_pause(initial=True)

        self._apply_all()
        self._update_tick_timer()

        self._battery_timer_id = GLib.timeout_add_seconds(60, self._on_battery_tick)
        self._app_timer_id = GLib.timeout_add_seconds(10, self._on_app_rule_tick)
        self._start_schedule_timer()

    def shutdown(self) -> None:
        """Stop all timers and the lwe subprocess."""
        self._stop_tick_timer()
        if self._battery_timer_id is not None:
            GLib.source_remove(self._battery_timer_id)
            self._battery_timer_id = None
        if self._app_timer_id is not None:
            GLib.source_remove(self._app_timer_id)
            self._app_timer_id = None
        self._stop_schedule_timer()
        if self._runner and self._runner.is_running():
            logger.info("Stopping lwe subprocess")
            self._runner.stop()

    # ------------------------------------------------------------------
    # Core D-Bus methods
    # ------------------------------------------------------------------

    def SetWallpaper(self, monitor: str, path: str) -> bool:  # type: ignore[override]
        logger.info("SetWallpaper(%r, %r)", monitor, path)

        if not self._runner:
            logger.error("SetWallpaper: lwe binary not found")
            return False

        if not Path(path).exists():
            logger.error("SetWallpaper: path does not exist: %s", path)
            return False

        self._monitor_manager.assign_wallpaper(monitor, path)
        result = self._apply_all()
        if result and bool(_cfg.get("pywal_on_change", False)):
            self._run_pywal_async(path)
        return result

    def GetCurrentWallpaper(self, monitor: str) -> str:  # type: ignore[override]
        assignment = self._monitor_manager.get_assignment(monitor)
        return assignment.wallpaper if assignment else ""

    def GetMonitors(self) -> list[str]:  # type: ignore[override]
        if not self._monitor_manager.monitors:
            self._monitor_manager.detect()
        return [m.name for m in self._monitor_manager.monitors]

    def SetEnabled(self, enabled: bool) -> None:  # type: ignore[override]
        logger.info("SetEnabled(%r)", enabled)
        self._enabled = enabled
        if not self._runner:
            return
        if enabled:
            self._apply_all()  # _apply_all checks _pause_reasons internally
        else:
            self._runner.stop()

    def GetStatus(self) -> dict[str, Any]:  # type: ignore[override]
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

    def ApplySettings(self) -> None:  # type: ignore[override]
        _cfg.load()
        fps_limit = int(_cfg.get("fps_limit", 30))
        mute_audio = bool(_cfg.get("mute_audio", False))
        fullscreen_pause = bool(_cfg.get("fullscreen_pause", True))
        disable_mouse = bool(_cfg.get("disable_mouse", False))
        disable_parallax = bool(_cfg.get("disable_parallax", False))
        logger.info(
            "ApplySettings: fps=%d mute=%s fullscreen_pause=%s disable_mouse=%s disable_parallax=%s",
            fps_limit, mute_audio, fullscreen_pause, disable_mouse, disable_parallax,
        )
        if self._runner:
            self._runner.update_playback(
                fps_limit, mute_audio, fullscreen_pause, disable_mouse, disable_parallax
            )
        self._update_tick_timer()
        # Re-evaluate pause conditions and schedule with updated config.
        self._check_battery_pause()
        self._check_app_rule_pause()
        self._restart_schedule_timer()

    # ------------------------------------------------------------------
    # Playlist D-Bus — read
    # ------------------------------------------------------------------

    def GetPlaylistStatus(self) -> str:  # type: ignore[override]
        global_interval = int(_cfg.get("playlist_interval_minutes", 0))
        return json.dumps({
            "timer_running": self._tick_timer_id is not None,
            "global_interval_minutes": global_interval,
            "playlists": [pl.status_dict() for pl in self._playlists.all()],
        })

    def GetPlaylists(self) -> str:  # type: ignore[override]
        return json.dumps([pl.to_dict() for pl in self._playlists.all()])

    # ------------------------------------------------------------------
    # Playlist D-Bus — create / delete / rename
    # ------------------------------------------------------------------

    def CreatePlaylist(self, name: str) -> str:  # type: ignore[override]
        pl = self._playlists.create(name)
        logger.info("CreatePlaylist: %r → %s", name, pl.id)
        self._update_tick_timer()
        return pl.id

    def DeletePlaylist(self, playlist_id: str) -> bool:  # type: ignore[override]
        ok = self._playlists.delete(playlist_id)
        if ok:
            self._playlist_last_tick.pop(playlist_id, None)
            self._update_tick_timer()
            logger.info("DeletePlaylist: %s", playlist_id)
        return ok

    def SetPlaylistName(self, playlist_id: str, name: str) -> bool:  # type: ignore[override]
        pl = self._playlists.get(playlist_id)
        if not pl:
            return False
        pl.name = name
        self._playlists.save()
        return True

    # ------------------------------------------------------------------
    # Playlist D-Bus — wallpaper list mutations
    # ------------------------------------------------------------------

    def AddToPlaylist(self, playlist_id: str, wallpaper_path: str) -> bool:  # type: ignore[override]
        pl = self._playlists.get(playlist_id)
        if not pl:
            return False
        if wallpaper_path not in pl.wallpaper_paths:
            pl.wallpaper_paths.append(wallpaper_path)
            pl.item_durations.append(0)  # default: use playlist interval
            self._playlists.save()
            logger.info("AddToPlaylist %s: %s", pl.name, Path(wallpaper_path).name)
        self._update_tick_timer()
        return True

    def RemoveFromPlaylist(self, playlist_id: str, index: int) -> bool:  # type: ignore[override]
        pl = self._playlists.get(playlist_id)
        if not pl or not (0 <= index < len(pl.wallpaper_paths)):
            return False
        removed = pl.wallpaper_paths.pop(index)
        pl._sync_durations()
        if index < len(pl.item_durations):
            pl.item_durations.pop(index)
        pl.current_index = max(0, min(pl.current_index, len(pl.wallpaper_paths) - 1))
        self._playlists.save()
        logger.info("RemoveFromPlaylist %s: index %d (%s)", pl.name, index, Path(removed).name)
        return True

    def ReorderPlaylist(self, playlist_id: str, from_index: int, to_index: int) -> bool:  # type: ignore[override]
        pl = self._playlists.get(playlist_id)
        if not pl:
            return False
        n = len(pl.wallpaper_paths)
        if not (0 <= from_index < n and 0 <= to_index < n):
            return False
        item = pl.wallpaper_paths.pop(from_index)
        pl.wallpaper_paths.insert(to_index, item)
        pl._sync_durations()
        dur = pl.item_durations.pop(from_index)
        pl.item_durations.insert(to_index, dur)
        pl.current_index = 0
        self._playlists.save()
        return True

    # ------------------------------------------------------------------
    # Playlist D-Bus — monitor assignments
    # ------------------------------------------------------------------

    def AssignPlaylistToMonitor(self, playlist_id: str, monitor: str) -> bool:  # type: ignore[override]
        pl = self._playlists.get(playlist_id)
        if not pl:
            return False
        # Remove this monitor from any other playlist first.
        for other in self._playlists.all():
            if other.id != playlist_id and monitor in other.monitor_assignments:
                other.monitor_assignments.remove(monitor)
        if monitor not in pl.monitor_assignments:
            pl.monitor_assignments.append(monitor)
        self._playlists.save()
        self._update_tick_timer()
        logger.info("AssignPlaylistToMonitor: %s → %s", monitor, pl.name)
        return True

    def UnassignPlaylistFromMonitor(self, playlist_id: str, monitor: str) -> bool:  # type: ignore[override]
        pl = self._playlists.get(playlist_id)
        if not pl or monitor not in pl.monitor_assignments:
            return False
        pl.monitor_assignments.remove(monitor)
        self._playlists.save()
        self._update_tick_timer()
        logger.info("UnassignPlaylistFromMonitor: %s ← %s", monitor, pl.name)
        return True

    # ------------------------------------------------------------------
    # Playlist D-Bus — settings
    # ------------------------------------------------------------------

    def SetPlaylistShuffle(self, playlist_id: str, shuffle: bool) -> bool:  # type: ignore[override]
        pl = self._playlists.get(playlist_id)
        if not pl:
            return False
        pl.shuffle = shuffle
        self._playlists.save()
        return True

    def SetPlaylistInterval(self, playlist_id: str, interval_minutes: int) -> bool:  # type: ignore[override]
        pl = self._playlists.get(playlist_id)
        if not pl:
            return False
        pl.interval_minutes = max(0, interval_minutes)
        self._playlists.save()
        self._update_tick_timer()
        return True

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _apply_all(self) -> bool:
        """Build current assignments and (re)start lwe."""
        if not self._runner:
            return False
        if not self._enabled:
            return False
        if self._pause_reasons:
            return False  # battery or app rule has suspended playback

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
        logger.warning("lwe exited unexpectedly (returncode=%d); auto-restart handled by runner",
                       returncode)

    def _run_pywal_async(self, wallpaper_path: str) -> None:
        """Apply pywal in a daemon thread so wallpaper switching is not blocked."""
        from mural.utils.palette import apply_pywal

        def _worker() -> None:
            preview = _find_preview_image(wallpaper_path)
            if not preview:
                logger.debug("pywal: no preview image found in %s", wallpaper_path)
                return
            ok = apply_pywal(preview)
            if ok:
                logger.info("pywal: applied theme from %s", preview)
            else:
                logger.debug("pywal: not available or returned non-zero")

        threading.Thread(target=_worker, daemon=True, name="pywal-worker").start()

    # ------------------------------------------------------------------
    # Power / app-rule pause management
    # ------------------------------------------------------------------

    def _check_battery_pause(self, initial: bool = False) -> None:
        """Detect battery state and add/remove "battery" from _pause_reasons."""
        import psutil
        battery = psutil.sensors_battery()
        on_battery = battery is not None and not battery.power_plugged
        if on_battery == self._battery_on:
            return  # no transition
        self._battery_on = on_battery
        if on_battery and bool(_cfg.get("pause_on_battery", True)):
            self._pause_reasons.add("battery")
            if not initial:
                logger.info("Battery: pausing lwe")
                if self._runner and self._runner.is_running():
                    self._runner.stop()
        else:
            self._pause_reasons.discard("battery")
            if not initial:
                logger.info("Battery: resuming lwe")
                self._apply_all()

    def _check_app_rule_pause(self, initial: bool = False) -> None:
        """Check running processes against pause_app_list; update _pause_reasons."""
        import psutil
        app_list = [
            a.lower().strip()
            for a in _cfg.get("pause_app_list", [])
            if a.strip()
        ]
        if not app_list:
            was = "app" in self._pause_reasons
            self._app_pause_name = ""
            self._pause_reasons.discard("app")
            if was and not initial:
                logger.info("AppRule: resuming (no rules configured)")
                self._apply_all()
            return

        found: str | None = None
        for proc in psutil.process_iter(["name"]):
            try:
                name = (proc.info.get("name") or "").lower()
                if name in app_list:
                    found = proc.info.get("name") or name
                    break
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass

        if found:
            if "app" not in self._pause_reasons:
                self._app_pause_name = found
                self._pause_reasons.add("app")
                if not initial:
                    logger.info("AppRule: pausing for %s", found)
                    if self._runner and self._runner.is_running():
                        self._runner.stop()
        else:
            was = "app" in self._pause_reasons
            self._app_pause_name = ""
            self._pause_reasons.discard("app")
            if was and not initial:
                logger.info("AppRule: resuming")
                self._apply_all()

    def _on_battery_tick(self) -> bool:
        self._check_battery_pause()
        return True

    def _on_app_rule_tick(self) -> bool:
        self._check_app_rule_pause()
        return True

    # ------------------------------------------------------------------
    # New D-Bus methods — power / app-rule / per-item duration
    # ------------------------------------------------------------------

    def GetPowerStatus(self) -> str:  # type: ignore[override]
        import psutil
        battery = psutil.sensors_battery()
        if battery is None:
            return "unknown"
        return "battery" if not battery.power_plugged else "ac"

    def GetAppRuleStatus(self) -> str:  # type: ignore[override]
        if self._app_pause_name:
            return f"paused:{self._app_pause_name}"
        return "running"

    def SetItemDuration(self, playlist_id: str, index: int, minutes: int) -> bool:  # type: ignore[override]
        pl = self._playlists.get(playlist_id)
        if not pl or not (0 <= index < len(pl.wallpaper_paths)):
            return False
        pl._sync_durations()
        pl.item_durations[index] = max(0, minutes)
        self._playlists.save()
        return True

    def GetScheduleStatus(self) -> str:  # type: ignore[override]
        return self._schedule_last_slot or "none"

    # ------------------------------------------------------------------
    # Time-of-day schedule
    # ------------------------------------------------------------------

    def _start_schedule_timer(self) -> None:
        """Start the 60-second schedule timer if scheduling is configured and enabled."""
        if not bool(_cfg.get("time_schedule_enabled", False)):
            return
        schedule = _cfg.get("time_schedule", [])
        if not any(e.get("path") for e in schedule if isinstance(e, dict)):
            return
        # Immediately apply whichever slot should be active right now.
        self._check_schedule_now(initial=True)
        if self._schedule_timer_id is None:
            self._schedule_timer_id = GLib.timeout_add_seconds(60, self._on_schedule_tick)
            logger.info("Schedule timer started")

    def _stop_schedule_timer(self) -> None:
        if self._schedule_timer_id is not None:
            GLib.source_remove(self._schedule_timer_id)
            self._schedule_timer_id = None
            logger.info("Schedule timer stopped")

    def _restart_schedule_timer(self) -> None:
        self._stop_schedule_timer()
        self._schedule_last_slot = ""  # reset so the slot re-fires on next start
        self._start_schedule_timer()

    def _on_schedule_tick(self) -> bool:
        self._check_schedule_now(initial=False)
        return True  # keep timer alive

    def _check_schedule_now(self, initial: bool = False) -> None:
        """Determine the currently active time slot and apply it if it changed."""
        import datetime
        if not bool(_cfg.get("time_schedule_enabled", False)):
            return
        schedule = _cfg.get("time_schedule", [])
        if not schedule:
            return

        now = datetime.datetime.now()
        current_minutes = now.hour * 60 + now.minute

        # Find the slot with the latest start time that has already passed today.
        active_entry: dict | None = None
        best_minutes = -1
        for entry in schedule:
            if not isinstance(entry, dict) or not entry.get("path"):
                continue
            try:
                h, m = map(int, entry.get("time", "00:00").split(":"))
            except (ValueError, AttributeError):
                continue
            slot_minutes = h * 60 + m
            if slot_minutes <= current_minutes and slot_minutes > best_minutes:
                best_minutes = slot_minutes
                active_entry = entry

        # Day wraparound: no slot has fired yet today — use the latest slot from "yesterday".
        if active_entry is None:
            best_minutes = -1
            for entry in schedule:
                if not isinstance(entry, dict) or not entry.get("path"):
                    continue
                try:
                    h, m = map(int, entry.get("time", "00:00").split(":"))
                except (ValueError, AttributeError):
                    continue
                slot_minutes = h * 60 + m
                if slot_minutes > best_minutes:
                    best_minutes = slot_minutes
                    active_entry = entry

        if active_entry is None:
            return

        slot_name = active_entry.get("slot", "")
        path = active_entry.get("path", "")

        # Don't re-fire the same slot every tick.
        if slot_name == self._schedule_last_slot and not initial:
            return

        self._schedule_last_slot = slot_name
        if path and Path(path).exists():
            monitors = self.GetMonitors()
            for monitor in monitors:
                self._monitor_manager.assign_wallpaper(monitor, path)
                logger.info(
                    "Schedule slot %r → %s on %s",
                    slot_name, Path(path).name, monitor,
                )
            if monitors:
                self._apply_all()

    # ------------------------------------------------------------------
    # Playlist tick timer
    # ------------------------------------------------------------------

    def _timer_needed(self) -> bool:
        """Return True if the tick timer should be running."""
        global_interval = int(_cfg.get("playlist_interval_minutes", 0))
        if global_interval > 0:
            return True
        for pl in self._playlists.all():
            if pl.interval_minutes > 0 and pl.monitor_assignments and pl.wallpaper_paths:
                return True
        return False

    def _update_tick_timer(self) -> None:
        """Start or stop the 60-second tick timer based on current state."""
        needed = self._timer_needed()
        if needed and self._tick_timer_id is None:
            self._tick_timer_id = GLib.timeout_add_seconds(60, self._on_playlist_tick)
            logger.info("Playlist tick timer started (60s granularity)")
        elif not needed and self._tick_timer_id is not None:
            self._stop_tick_timer()

    def _stop_tick_timer(self) -> None:
        if self._tick_timer_id is not None:
            GLib.source_remove(self._tick_timer_id)
            self._tick_timer_id = None
            logger.info("Playlist tick timer stopped")

    def _on_playlist_tick(self) -> bool:
        """60-second callback: advance playlists whose interval has elapsed."""
        now = time.time()
        global_interval = int(_cfg.get("playlist_interval_minutes", 0))
        applied_any = False

        for pl in self._playlists.all():
            if not pl.monitor_assignments or not pl.wallpaper_paths:
                continue

            effective_minutes = pl.interval_minutes or global_interval
            if effective_minutes <= 0:
                continue

            last = self._playlist_last_tick.get(pl.id, 0.0)
            next_dur = self._playlist_next_interval.get(pl.id, effective_minutes)
            if now - last < next_dur * 60:
                continue

            # Time to advance this playlist.
            wp_path, item_dur = pl.next_item()
            if wp_path is None:
                logger.warning("Playlist %r: no valid wallpapers on disk", pl.name)
                continue

            self._playlist_last_tick[pl.id] = now
            # Record effective duration for next tick interval.
            eff_next = item_dur if item_dur > 0 else effective_minutes
            self._playlist_next_interval[pl.id] = eff_next
            self._playlists.save()

            for monitor in pl.monitor_assignments:
                self._monitor_manager.assign_wallpaper(monitor, wp_path)
                logger.info("Playlist %r → %s: %s", pl.name, monitor, Path(wp_path).name)

            applied_any = True

        if applied_any:
            self._apply_all()

        return True  # keep the GLib timer alive

    def _discover_library_wallpapers(self) -> list[Path]:
        """Scan Steam Workshop, downloads, and extra dirs for wallpaper dirs."""
        dirs: list[Path] = []
        for root_str in self._STEAM_ROOTS:
            wp = Path(root_str).expanduser() / "steamapps" / "workshop" / "content" / self._WORKSHOP_ID
            if wp.is_dir():
                dirs.extend(p for p in wp.iterdir() if p.is_dir())
        if DOWNLOAD_DIR.is_dir():
            dirs.extend(p for p in DOWNLOAD_DIR.iterdir() if p.is_dir())
        for extra in _cfg.get("extra_library_dirs", []):
            p = Path(extra).expanduser()
            if p.is_dir():
                dirs.extend(c for c in p.iterdir() if c.is_dir())
        return dirs


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------

_PREVIEW_NAMES = (
    "preview.jpg", "preview.png", "preview.gif",
    "thumbnail.jpg", "thumbnail.png",
)


def _find_preview_image(wallpaper_dir: str) -> str | None:
    """Return the path to the best preview image for *wallpaper_dir*, or None.

    Checks ``project.json`` first, then falls back to common filenames.
    """
    p = Path(wallpaper_dir)
    if not p.is_dir():
        return None
    proj = p / "project.json"
    if proj.exists():
        try:
            data = json.loads(proj.read_text(encoding="utf-8"))
            preview = data.get("preview", "")
            if preview:
                candidate = p / preview
                if candidate.exists():
                    return str(candidate)
        except Exception:
            pass
    for name in _PREVIEW_NAMES:
        candidate = p / name
        if candidate.exists():
            return str(candidate)
    return None


# ---------------------------------------------------------------------------
# Bus registration and main loop
# ---------------------------------------------------------------------------

def _register_signal_handlers(loop: GLib.MainLoop, service: MuralCoreService) -> None:
    def _handle_signal(sig: int, _frame: Any) -> None:
        logger.info("Received signal %d — shutting down", sig)
        service.shutdown()
        loop.quit()

    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)


def run_service(debug: bool = False) -> int:
    logging.basicConfig(
        level=logging.DEBUG if debug else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    logger.info("Mural Core Service starting (debug=%s)", debug)

    detection = detect()
    discovery = discover()

    if not discovery.binary_found:
        logger.error("linux-wallpaperengine is not installed — service will start but cannot render wallpapers.")

    loop = GLib.MainLoop()
    service = MuralCoreService(detection=detection, discovery=discovery, loop=loop)

    try:
        bus = SessionMessageBus()
        bus.publish_object(DBUS_OBJECT_PATH, service)
        bus.register_service(DBUS_SERVICE_NAME)
        logger.info("D-Bus service registered: name=%s path=%s", DBUS_SERVICE_NAME, DBUS_OBJECT_PATH)
    except DBusError as exc:
        logger.error("Failed to register D-Bus service: %s", exc)
        return 1

    _register_signal_handlers(loop, service)
    service.initialise()

    logger.info("Mural Core Service ready — entering main loop")
    try:
        loop.run()
    except KeyboardInterrupt:
        service.shutdown()

    logger.info("Mural Core Service stopped")
    return 0


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="mural-core",
        description="Mural Core animated wallpaper session service",
    )
    parser.add_argument("--debug", action="store_true", help="Enable debug-level logging")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = _parse_args(argv)
    sys.exit(run_service(debug=args.debug))


if __name__ == "__main__":
    main()
