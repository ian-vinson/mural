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
from mural.core.profiles import ProfileStore
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

    def SetWallpaper(self, monitor: Str, path: Str, scaling: Str) -> Bool:
        """Apply a wallpaper to a named monitor with optional scaling mode."""
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

    def GetNowPlaying(self) -> Str:
        """Return current MPRIS media info as JSON string, or empty string if nothing is playing."""
        ...

    def GetActivities(self) -> Str:
        """Return JSON array [{id, name}] of KDE activities, or '[]' on non-Plasma desktops."""
        ...

    def CaptureSddmScreenshot(self) -> Bool:
        """Capture the current wallpaper as a JPEG and push it to the SDDM background path.

        Returns ``True`` on success, ``False`` if lwe is not available or the capture failed.
        """
        ...

    # ------------------------------------------------------------------
    # Monitor profiles
    # ------------------------------------------------------------------

    def GetProfiles(self) -> Str:
        """Return JSON array of all saved monitor profiles."""
        ...

    def SaveProfile(self, name: Str) -> Str:
        """Snapshot current monitor assignments as a named profile; returns profile id."""
        ...

    def LoadProfile(self, profile_id: Str) -> Bool:
        """Restore wallpaper assignments from a saved profile; returns True on success."""
        ...

    def DeleteProfile(self, profile_id: Str) -> Bool:
        """Delete a saved profile by id; returns True if it existed."""
        ...

    def RenameProfile(self, profile_id: Str, new_name: Str) -> Bool:
        """Rename an existing profile; returns True on success."""
        ...


# ---------------------------------------------------------------------------
# Service implementation
# ---------------------------------------------------------------------------

class MuralCoreService(IMuralCore):
    """Implementation of the Mural Core D-Bus service."""

    VERSION = "0.2.0-alpha"

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

        # Profile state
        self._profile_store = ProfileStore()
        self._profile_store.load()
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

        # MPRIS now-playing state
        self._mpris_timer_id: int | None = None
        self._current_media_json: str = ""
        self._last_media_key: str = ""

        # D-Bus signal subscriptions (ScreenSaver + ActivityManager)
        self._gio_conn = None
        self._screensaver_sub_id: int | None = None
        self._activity_sub_id: int | None = None

        _cfg.load()
        if discovery.binary_found:
            self._runner = BackendRunner(
                binary_path=discovery.binary,          # type: ignore[arg-type]
                assets_path=discovery.assets_path,
                on_unexpected_exit=self._on_lwe_exit,
                auto_restart=True,
                fps_limit=int(_cfg.get("fps_limit", 30)),
                mute_audio=bool(_cfg.get("mute_audio", False)),
                volume=int(_cfg.get("volume", 80)),
                no_automute=bool(_cfg.get("no_automute", False)),
                no_audio_processing=bool(_cfg.get("no_audio_processing", False)),
                fullscreen_pause=bool(_cfg.get("fullscreen_pause", True)),
                fullscreen_pause_only_active=bool(_cfg.get("fullscreen_pause_only_active", False)),
                fullscreen_ignore_appids=list(_cfg.get("fullscreen_ignore_appids", [])),
                disable_mouse=bool(_cfg.get("disable_mouse", False)),
                disable_parallax=bool(_cfg.get("disable_parallax", False)),
                disable_particles=bool(_cfg.get("disable_particles", False)),
                screen_span=bool(_cfg.get("screen_span", False)),
                clamping=str(_cfg.get("clamping", "clamp")),
                render_debug=bool(_cfg.get("render_debug", False)),
                render_debug_type=str(_cfg.get("render_debug_type", "full")),
                fade_transition=bool(_cfg.get("fade_transition", True)),
                fade_duration_ms=int(_cfg.get("fade_duration_ms", 400)),
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
        self._mpris_timer_id = GLib.timeout_add_seconds(5, self._on_mpris_tick)
        self._subscribe_system_signals()

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
        if self._mpris_timer_id is not None:
            GLib.source_remove(self._mpris_timer_id)
            self._mpris_timer_id = None
        self._unsubscribe_system_signals()
        if self._runner and self._runner.is_running():
            logger.info("Stopping lwe subprocess")
            self._runner.stop()

    # ------------------------------------------------------------------
    # Core D-Bus methods
    # ------------------------------------------------------------------

    def SetWallpaper(self, monitor: str, path: str, scaling: str = "default") -> bool:  # type: ignore[override]
        logger.info("SetWallpaper(%r, %r, scaling=%r)", monitor, path, scaling)

        if not self._runner:
            logger.error("SetWallpaper: lwe binary not found")
            return False

        if not Path(path).exists():
            logger.error("SetWallpaper: path does not exist: %s", path)
            return False

        self._monitor_manager.assign_wallpaper(monitor, path)
        self._monitor_manager.assign_scaling(monitor, scaling)
        result = self._apply_all()
        if result:
            pywal_source = str(_cfg.get("pywal_source", "disabled"))
            if pywal_source == "last":
                self._run_pywal_async(path)
            elif pywal_source == "primary":
                primary = self._monitor_manager.primary_monitor()
                if primary is None or monitor == primary.name:
                    self._run_pywal_async(path)
            if bool(_cfg.get("openrgb_sync", False)):
                self._run_openrgb_async(path)
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
        volume = int(_cfg.get("volume", 80))
        no_automute = bool(_cfg.get("no_automute", False))
        no_audio_processing = bool(_cfg.get("no_audio_processing", False))
        fullscreen_pause = bool(_cfg.get("fullscreen_pause", True))
        fullscreen_pause_only_active = bool(_cfg.get("fullscreen_pause_only_active", False))
        fullscreen_ignore_appids = list(_cfg.get("fullscreen_ignore_appids", []))
        disable_mouse = bool(_cfg.get("disable_mouse", False))
        disable_parallax = bool(_cfg.get("disable_parallax", False))
        disable_particles = bool(_cfg.get("disable_particles", False))
        screen_span = bool(_cfg.get("screen_span", False))
        clamping = str(_cfg.get("clamping", "clamp"))
        render_debug = bool(_cfg.get("render_debug", False))
        render_debug_type = str(_cfg.get("render_debug_type", "full"))
        logger.info(
            "ApplySettings: fps=%d mute=%s vol=%d fullscreen_pause=%s disable_mouse=%s"
            " disable_parallax=%s disable_particles=%s screen_span=%s clamping=%s"
            " render_debug=%s",
            fps_limit, mute_audio, volume, fullscreen_pause, disable_mouse,
            disable_parallax, disable_particles, screen_span, clamping, render_debug,
        )
        if self._runner:
            self._runner.update_playback(
                fps_limit, mute_audio, fullscreen_pause,
                disable_mouse=disable_mouse,
                disable_parallax=disable_parallax,
                volume=volume,
                no_automute=no_automute,
                no_audio_processing=no_audio_processing,
                fullscreen_pause_only_active=fullscreen_pause_only_active,
                fullscreen_ignore_appids=fullscreen_ignore_appids,
                disable_particles=disable_particles,
                screen_span=screen_span,
                clamping=clamping,
                render_debug=render_debug,
                render_debug_type=render_debug_type,
                fade_transition=bool(_cfg.get("fade_transition", True)),
                fade_duration_ms=int(_cfg.get("fade_duration_ms", 400)),
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
            WallpaperAssignment(monitor=a.monitor_name, wallpaper=a.wallpaper, scaling=a.scaling)
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

    def _run_openrgb_async(self, wallpaper_path: str) -> None:
        """Sync RGB lighting to the wallpaper palette in a background thread."""
        color_source = str(_cfg.get("openrgb_color_source", "dominant"))

        def _worker() -> None:
            try:
                from mural.utils.openrgb import is_available, set_color_from_hex
                from mural.utils.palette import extract_palette
                if not is_available():
                    return
                palette = extract_palette(wallpaper_path)
                if not palette:
                    return
                source_idx = {"dominant": 0, "secondary": 1, "tertiary": 2}.get(
                    color_source, 0
                )
                if color_source == "average":
                    parts = [c.lstrip("#") for c in palette if len(c.lstrip("#")) == 6]
                    if not parts:
                        return
                    r = int(sum(int(h[0:2], 16) for h in parts) / len(parts))
                    g = int(sum(int(h[2:4], 16) for h in parts) / len(parts))
                    b = int(sum(int(h[4:6], 16) for h in parts) / len(parts))
                    hex_color = f"#{r:02x}{g:02x}{b:02x}"
                else:
                    hex_color = palette[min(source_idx, len(palette) - 1)]
                set_color_from_hex(hex_color)
                logger.debug("OpenRGB: set color %s from %s", hex_color, color_source)
            except Exception as exc:
                logger.debug("OpenRGB worker failed: %s", exc)

        threading.Thread(target=_worker, daemon=True, name="openrgb-sync").start()

    def _on_mpris_tick(self) -> bool:
        """Poll MPRIS every 5 seconds and update cached media info."""
        try:
            from mural.utils.mpris import get_current_media
            media = get_current_media()

            if media and media.playing:
                import json as _json
                new_json = _json.dumps({
                    "title": media.title,
                    "artist": media.artist,
                    "album": media.album,
                    "art_url": media.art_url,
                })
                new_key = f"{media.title}|{media.artist}"
            else:
                new_json = ""
                new_key = ""

            if new_key != self._last_media_key:
                old_key = self._last_media_key
                self._current_media_json = new_json
                self._last_media_key = new_key
                if new_key:
                    logger.info("MPRIS: now playing %r by %r", media.title, media.artist)  # type: ignore[union-attr]
                elif old_key:
                    logger.info("MPRIS: playback stopped")
                if media and media.playing and bool(_cfg.get("mpris_to_wallpaper", False)):
                    props = {
                        "mediametadata_title": media.title,
                        "mediametadata_artist": media.artist,
                        "mediametadata_album": media.album,
                    }
                    if self._runner:
                        self._runner.set_extra_props(props)
        except Exception as exc:
            logger.debug("MPRIS tick error: %s", exc)
        return True

    def GetNowPlaying(self) -> str:  # type: ignore[override]
        return self._current_media_json

    def GetActivities(self) -> str:  # type: ignore[override]
        """Return JSON array [{id, name}] of KDE activities, or '[]'."""
        if self._detection.desktop != "plasma":
            return "[]"
        try:
            import gi
            gi.require_version("Gio", "2.0")
            from gi.repository import Gio, GLib as _GLib  # noqa: PLC0415

            conn = Gio.bus_get_sync(Gio.BusType.SESSION, None)
            res = conn.call_sync(
                "org.kde.ActivityManager",
                "/ActivityManager/Activities",
                "org.kde.ActivityManager.Activities",
                "ListActivities",
                None,
                _GLib.VariantType.new("(as)"),
                Gio.DBusCallFlags.NONE,
                2000,
                None,
            )
            ids: list[str] = list(res.get_child_value(0).unpack())
            activities: list[dict] = []
            for act_id in ids:
                try:
                    name_res = conn.call_sync(
                        "org.kde.ActivityManager",
                        "/ActivityManager/Activities",
                        "org.kde.ActivityManager.Activities",
                        "ActivityName",
                        _GLib.Variant("(s)", (act_id,)),
                        _GLib.VariantType.new("(s)"),
                        Gio.DBusCallFlags.NONE,
                        2000,
                        None,
                    )
                    name = str(name_res.get_child_value(0).unpack())
                except Exception:
                    name = act_id
                activities.append({"id": act_id, "name": name})
            return json.dumps(activities)
        except Exception as exc:
            logger.debug("GetActivities error: %s", exc)
            return "[]"

    # ------------------------------------------------------------------
    # D-Bus system signal subscriptions (ScreenSaver + ActivityManager)
    # ------------------------------------------------------------------

    def _subscribe_system_signals(self) -> None:
        """Subscribe to org.freedesktop.ScreenSaver::ActiveChanged and
        (on Plasma) org.kde.ActivityManager.Activities::CurrentActivityChanged."""
        try:
            import gi
            gi.require_version("Gio", "2.0")
            from gi.repository import Gio  # noqa: PLC0415

            self._gio_conn = Gio.bus_get_sync(Gio.BusType.SESSION, None)

            self._screensaver_sub_id = self._gio_conn.signal_subscribe(
                "org.freedesktop.ScreenSaver",
                "org.freedesktop.ScreenSaver",
                "ActiveChanged",
                "/org/freedesktop/ScreenSaver",
                None,
                Gio.DBusSignalFlags.NONE,
                self._on_screensaver_active_changed,
                None,
            )
            logger.info("Subscribed to org.freedesktop.ScreenSaver::ActiveChanged")

            if self._detection.desktop == "plasma":
                self._activity_sub_id = self._gio_conn.signal_subscribe(
                    None,
                    "org.kde.ActivityManager.Activities",
                    "CurrentActivityChanged",
                    None,
                    None,
                    Gio.DBusSignalFlags.NONE,
                    self._on_activity_changed,
                    None,
                )
                logger.info("Subscribed to ActivityManager::CurrentActivityChanged")
        except Exception as exc:
            logger.debug("_subscribe_system_signals failed: %s", exc)

    def _unsubscribe_system_signals(self) -> None:
        if self._gio_conn is None:
            return
        try:
            if self._screensaver_sub_id is not None:
                self._gio_conn.signal_unsubscribe(self._screensaver_sub_id)
                self._screensaver_sub_id = None
            if self._activity_sub_id is not None:
                self._gio_conn.signal_unsubscribe(self._activity_sub_id)
                self._activity_sub_id = None
        except Exception as exc:
            logger.debug("_unsubscribe_system_signals: %s", exc)

    def _on_screensaver_active_changed(
        self, connection, sender, object_path, interface_name, signal_name, parameters, user_data
    ) -> None:
        try:
            active = bool(parameters.get_child_value(0).unpack())
        except Exception:
            return
        if active and bool(_cfg.get("auto_sddm_update", False)):
            logger.info("Screen locked — triggering SDDM screenshot")
            threading.Thread(
                target=self._capture_sddm_screenshot, daemon=True, name="sddm-lock-shot"
            ).start()

    def _capture_sddm_screenshot(self) -> bool:
        """Capture the current wallpaper frame and push it to the SDDM background path.

        Returns True on success.  Safe to call from any thread.
        """
        import subprocess as _sp

        binary = self._discovery.binary if self._discovery.binary_found else None
        if not binary:
            logger.debug("sddm-screenshot: lwe binary not found")
            return False

        assets = self._discovery.assets_path
        monitors = self.GetMonitors()
        if not monitors:
            logger.debug("sddm-screenshot: no monitors")
            return False
        assignment = self._monitor_manager.get_assignment(monitors[0])
        wallpaper = assignment.wallpaper if assignment else ""
        if not wallpaper:
            logger.debug("sddm-screenshot: no wallpaper active")
            return False

        out_path = Path("~/.local/share/mural/sddm_lock.jpg").expanduser()
        out_path.parent.mkdir(parents=True, exist_ok=True)

        cmd = [str(binary)]
        if assets:
            cmd += ["--assets-dir", str(assets)]
        cmd += [
            "--screenshot", str(out_path),
            "--screenshot-delay", "1",
            "--bg", wallpaper,
        ]
        try:
            result = _sp.run(cmd, timeout=10, capture_output=True)
            if result.returncode != 0 or not out_path.exists():
                logger.warning("sddm-screenshot: lwe exited with rc=%d", result.returncode)
                return False
            logger.info("sddm-screenshot: saved %s", out_path)
        except Exception as exc:
            logger.debug("sddm-screenshot: error: %s", exc)
            return False

        sddm_bg = _detect_sddm_background_path()
        if sddm_bg is None:
            logger.info("sddm-screenshot: could not detect background path")
            return True  # screenshot succeeded, just no place to copy it

        try:
            import subprocess as _sp2
            _sp2.Popen(["pkexec", "cp", str(out_path), str(sddm_bg)])
            logger.info("sddm-screenshot: pkexec copy → %s", sddm_bg)
        except Exception as exc:
            logger.debug("sddm-screenshot: pkexec failed: %s", exc)
        return True

    def CaptureSddmScreenshot(self) -> bool:  # type: ignore[override]
        """D-Bus method: capture wallpaper screenshot and update SDDM background."""
        return self._capture_sddm_screenshot()

    def _on_activity_changed(
        self, connection, sender, object_path, interface_name, signal_name, parameters, user_data
    ) -> None:
        try:
            new_id = str(parameters.get_child_value(0).unpack())
        except Exception:
            return
        if not bool(_cfg.get("activity_sync_enabled", False)):
            return
        logger.info("KDE activity changed → %s", new_id)
        self._apply_activity_wallpaper(new_id)

    def _apply_activity_wallpaper(self, activity_id: str) -> None:
        """Switch wallpaper to the one assigned to *activity_id* in config."""
        _cfg.load()
        wallpapers: dict = dict(_cfg.get("activity_wallpapers", {}))
        path = wallpapers.get(activity_id, "")
        if not path or not Path(path).exists():
            logger.debug("Activity %s: no wallpaper configured or file missing", activity_id)
            return
        monitors = self.GetMonitors()
        for monitor in monitors:
            self._monitor_manager.assign_wallpaper(monitor, path)
        if monitors:
            self._apply_all()
            logger.info("Activity %s: applied %s", activity_id, Path(path).name)

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

    # ------------------------------------------------------------------
    # Monitor profile D-Bus methods
    # ------------------------------------------------------------------

    def GetProfiles(self) -> str:  # type: ignore[override]
        return json.dumps([
            {
                "id": p.id,
                "name": p.name,
                "assignments": p.assignments,
                "scaling": p.scaling,
                "created_at": p.created_at,
            }
            for p in self._profile_store.all()
        ])

    def SaveProfile(self, name: str) -> str:  # type: ignore[override]
        assignments = {
            a.monitor_name: a.wallpaper
            for a in self._monitor_manager.active_assignments()
        }
        scaling = {
            a.monitor_name: a.scaling
            for a in self._monitor_manager.active_assignments()
        }
        profile = self._profile_store.create(name, assignments, scaling)
        logger.info("SaveProfile: %r → %s", name, profile.id)
        return profile.id

    def LoadProfile(self, profile_id: str) -> bool:  # type: ignore[override]
        profile = self._profile_store.get(profile_id)
        if not profile:
            logger.warning("LoadProfile: profile %s not found", profile_id[:8])
            return False
        for monitor, path in profile.assignments.items():
            if Path(path).exists():
                self._monitor_manager.assign_wallpaper(monitor, path)
                scaling = profile.scaling.get(monitor, "default")
                self._monitor_manager.assign_scaling(monitor, scaling)
        self._apply_all()
        logger.info("LoadProfile: loaded %r (%s)", profile.name, profile_id[:8])
        return True

    def DeleteProfile(self, profile_id: str) -> bool:  # type: ignore[override]
        ok = self._profile_store.delete(profile_id)
        if ok:
            logger.info("DeleteProfile: %s", profile_id[:8])
        return ok

    def RenameProfile(self, profile_id: str, new_name: str) -> bool:  # type: ignore[override]
        profile = self._profile_store.get(profile_id)
        if not profile:
            return False
        profile.name = new_name
        self._profile_store.save(self._profile_store.all())
        logger.info("RenameProfile: %s → %r", profile_id[:8], new_name)
        return True

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

def _detect_sddm_background_path() -> "Path | None":
    """Return the SDDM background file path to overwrite, or None if undetectable.

    Reads ``Background=`` from ``[Theme]`` in ``/etc/sddm.conf`` and
    ``/etc/sddm.conf.d/*.conf``.  Falls back to the Breeze theme path.
    """
    import configparser
    import glob

    for conf_path in ["/etc/sddm.conf"] + sorted(glob.glob("/etc/sddm.conf.d/*.conf")):
        try:
            cfg = configparser.ConfigParser()
            cfg.read(conf_path)
            bg = cfg.get("Theme", "Background", fallback=None)
            if bg:
                return Path(bg)
        except Exception:
            pass
    return Path("/usr/share/sddm/themes/breeze/background.jpg")


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
