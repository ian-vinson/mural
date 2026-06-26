# mural/backend/runner.py
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

"""Subprocess wrapper for linux-wallpaperengine.

Manages the lifetime of the ``linux-wallpaperengine`` (lwe) process:
start, stop, restart on crash, and orphan cleanup on service restart.

A single lwe process handles all monitors in one invocation:

    linux-wallpaperengine \\
        --screen-root DP-3   --bg /path/to/wallpaper1 \\
        --screen-root HDMI-1 --bg /path/to/wallpaper2

``BackendRunner`` owns that process and exposes a simple interface for
the Core Service to drive it.
"""

from __future__ import annotations

import logging
import os
import re
import signal
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import psutil

from mural.utils.properties import load_overrides

logger = logging.getLogger(__name__)

_LWE_PROCESS_NAME = "linux-wallpaperengine"
_SIGTERM_TIMEOUT = 3.0  # seconds to wait after SIGTERM before SIGKILL


def _kill_process(process: "subprocess.Popen[bytes]") -> None:
    """Terminate *process*'s process group; escalate to SIGKILL after timeout."""
    if process.poll() is not None:
        return
    pid = process.pid
    try:
        pgid = os.getpgid(pid)
        os.killpg(pgid, signal.SIGTERM)
    except ProcessLookupError:
        return
    try:
        process.wait(timeout=_SIGTERM_TIMEOUT)
    except subprocess.TimeoutExpired:
        try:
            pgid = os.getpgid(pid)
            os.killpg(pgid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.wait()


@dataclass
class WallpaperAssignment:
    """A single monitor-to-wallpaper mapping for lwe.

    Attributes:
        monitor: Output name as reported by the display server,
            e.g. ``"DP-3"`` or ``"HDMI-A-2"``.
        wallpaper: Absolute path to a wallpaper directory or file,
            or a Wallpaper Engine workshop ID string.
    """

    monitor: str
    wallpaper: str
    scaling: str = "default"


class BackendRunner:
    """Manages the linux-wallpaperengine subprocess lifecycle.

    One ``BackendRunner`` instance corresponds to one lwe process that
    may serve multiple monitors simultaneously.  The runner spawns a
    background monitor thread that detects unexpected process exits and
    triggers an optional callback so the Core Service can decide whether
    to restart.

    Args:
        binary_path: Absolute path to the ``linux-wallpaperengine`` binary.
        assets_path: Path to the Wallpaper Engine assets directory
            (``steamapps/common/wallpaper_engine``).  May be ``None``
            when only non-scene wallpapers are used.
        on_unexpected_exit: Optional callback invoked from the monitor
            thread when lwe exits without being asked to.  Receives the
            process return code as its sole argument.
        auto_restart: If ``True``, automatically restart lwe when it
            exits unexpectedly.  The callback (if any) is still called.
        max_restarts: Maximum consecutive automatic restarts before
            giving up.  Resets to zero after a successful run of at
            least ``restart_grace_seconds``.
        restart_grace_seconds: Seconds a process must run before its
            restart counter is considered reset.
    """

    def __init__(
        self,
        binary_path: Path | str,
        assets_path: Path | str | None = None,
        on_unexpected_exit: Callable[[int], None] | None = None,
        auto_restart: bool = True,
        max_restarts: int = 5,
        restart_grace_seconds: float = 10.0,
        fps_limit: int = 30,
        mute_audio: bool = False,
        volume: int = 80,
        no_automute: bool = False,
        no_audio_processing: bool = False,
        fullscreen_pause: bool = True,
        fullscreen_pause_only_active: bool = False,
        fullscreen_ignore_appids: list[str] | None = None,
        disable_mouse: bool = False,
        disable_parallax: bool = False,
        disable_particles: bool = False,
        screen_span: bool = False,
        clamping: str = "clamp",
        render_debug: bool = False,
        render_debug_type: str = "full",
    ) -> None:
        self._binary = Path(binary_path)
        self._assets = Path(assets_path) if assets_path else None
        self._on_unexpected_exit = on_unexpected_exit
        self._auto_restart = auto_restart
        self._max_restarts = max_restarts
        self._restart_grace = restart_grace_seconds
        self._fps_limit = fps_limit
        self._mute_audio = mute_audio
        self._volume = volume
        self._no_automute = no_automute
        self._no_audio_processing = no_audio_processing
        self._fullscreen_pause = fullscreen_pause
        self._fullscreen_pause_only_active = fullscreen_pause_only_active
        self._fullscreen_ignore_appids: list[str] = fullscreen_ignore_appids or []
        self._disable_mouse = disable_mouse
        self._disable_parallax = disable_parallax
        self._disable_particles = disable_particles
        self._screen_span = screen_span
        self._clamping = clamping
        self._render_debug = render_debug
        self._render_debug_type = render_debug_type

        self._process: subprocess.Popen[bytes] | None = None
        self._assignments: list[WallpaperAssignment] = []
        self._monitor_thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._restart_count = 0
        self._start_time: float = 0.0
        self._lock = threading.Lock()
        self._intentional_stop: bool = False

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def start(self, assignments: list[WallpaperAssignment]) -> None:
        """Start lwe with the given monitor-wallpaper assignments.

        If a process is already running it is stopped first.

        Args:
            assignments: One or more :class:`WallpaperAssignment` items.

        Raises:
            FileNotFoundError: If the lwe binary does not exist.
            ValueError: If ``assignments`` is empty.
        """
        if not assignments:
            raise ValueError("At least one WallpaperAssignment is required")
        if not self._binary.exists():
            raise FileNotFoundError(f"lwe binary not found: {self._binary}")

        with self._lock:
            old_process = (
                self._process
                if self._process and self._process.poll() is None
                else None
            )
            self._assignments = list(assignments)
            self._stop_event.clear()
            self._intentional_stop = False

            if old_process is not None:
                # Overlap transition: spawn new lwe without stopping the old
                # one first so there is no rendering gap. Pre-spawn guard
                # skips old_pid to keep it alive during the handoff.
                self._start_process(exclude_pid=old_process.pid)
            else:
                # First launch: clean up leftover orphans, then spawn.
                self.kill_orphans()
                self._start_process()

        if old_process is not None:
            # Sleep outside the lock so other callers aren't blocked.
            # 400 ms gives the new lwe time to initialize and start rendering.
            time.sleep(0.4)
            _kill_process(old_process)
            logger.debug("Overlap transition: retired old lwe (pid=%d)", old_process.pid)

    def stop(self) -> None:
        """Stop the lwe process gracefully and join the monitor thread."""
        with self._lock:
            self._intentional_stop = True
            self._stop_event.set()
            self._stop_process()
            self._restart_count = 0

        if self._monitor_thread and self._monitor_thread.is_alive():
            self._monitor_thread.join(timeout=5.0)

    def restart(self) -> None:
        """Restart lwe with the current assignments."""
        with self._lock:
            self._intentional_stop = True
            self._stop_process()
            self._stop_event.clear()
            self._intentional_stop = False
            self._start_process()

    def update_playback(
        self,
        fps_limit: int,
        mute_audio: bool,
        fullscreen_pause: bool,
        disable_mouse: bool = False,
        disable_parallax: bool = False,
        volume: int = 80,
        no_automute: bool = False,
        no_audio_processing: bool = False,
        fullscreen_pause_only_active: bool = False,
        fullscreen_ignore_appids: list[str] | None = None,
        disable_particles: bool = False,
        screen_span: bool = False,
        clamping: str = "clamp",
        render_debug: bool = False,
        render_debug_type: str = "full",
    ) -> None:
        """Update playback settings and restart lwe if it is running."""
        self._fps_limit = fps_limit
        self._mute_audio = mute_audio
        self._volume = volume
        self._no_automute = no_automute
        self._no_audio_processing = no_audio_processing
        self._fullscreen_pause = fullscreen_pause
        self._fullscreen_pause_only_active = fullscreen_pause_only_active
        self._fullscreen_ignore_appids = fullscreen_ignore_appids or []
        self._disable_mouse = disable_mouse
        self._disable_parallax = disable_parallax
        self._disable_particles = disable_particles
        self._screen_span = screen_span
        self._clamping = clamping
        self._render_debug = render_debug
        self._render_debug_type = render_debug_type
        if self.is_running():
            self.restart()

    def is_running(self) -> bool:
        """Return ``True`` if lwe is currently running."""
        with self._lock:
            return self._process is not None and self._process.poll() is None

    @property
    def pid(self) -> int | None:
        """PID of the running lwe process, or ``None``."""
        if self._process:
            return self._process.pid
        return None

    # ------------------------------------------------------------------
    # Process lifecycle (called with _lock held)
    # ------------------------------------------------------------------

    def _build_command(self, assignments: list[WallpaperAssignment]) -> list[str]:
        """Build the lwe CLI command for the given assignments."""
        cmd: list[str] = [str(self._binary)]

        if self._assets:
            cmd += ["--assets-dir", str(self._assets)]

        if self._fps_limit > 0:
            cmd += ["--fps", str(self._fps_limit)]

        # Audio
        if self._mute_audio:
            cmd.append("--silent")
        else:
            if self._volume != 100:
                cmd += ["--volume", str(self._volume)]
        if self._no_automute:
            cmd.append("--noautomute")
        if self._no_audio_processing:
            cmd.append("--no-audio-processing")

        # Fullscreen pause
        if self._fullscreen_pause:
            if self._fullscreen_pause_only_active:
                cmd.append("--fullscreen-pause-only-active")
            for appid in self._fullscreen_ignore_appids:
                if appid.strip():
                    cmd += ["--fullscreen-pause-ignore-appid", appid.strip()]
        else:
            cmd.append("--no-fullscreen-pause")

        if self._disable_mouse:
            cmd.append("--disable-mouse")
        if self._disable_parallax:
            cmd.append("--disable-parallax")
        if self._disable_particles:
            cmd.append("--disable-particles")
        if self._clamping and self._clamping != "clamp":
            cmd += ["--clamping", self._clamping]
        if self._render_debug:
            cmd += ["--render-debug", self._render_debug_type]

        # Screen assignments — span mode vs per-monitor mode
        if self._screen_span and len(assignments) > 1:
            monitors_str = ",".join(a.monitor for a in assignments)
            if assignments[0].scaling and assignments[0].scaling != "default":
                cmd += ["--scaling", assignments[0].scaling]
            cmd += ["--screen-span", monitors_str, "--bg", assignments[0].wallpaper]
        else:
            for assignment in assignments:
                if assignment.scaling and assignment.scaling != "default":
                    cmd += ["--scaling", assignment.scaling]
                cmd += ["--screen-root", assignment.monitor, "--bg", assignment.wallpaper]

        for assignment in assignments:
            for key, value in load_overrides(assignment.wallpaper).items():
                cmd += ["--set-property", f"{key}={value}"]

        return cmd

    def _start_process(self, exclude_pid: int | None = None) -> None:
        """Spawn the lwe subprocess in its own process group."""
        cmd = self._build_command(self._assignments)
        logger.info("Starting lwe: %s", " ".join(cmd))

        # Explicitly forward display-server variables into the child env.
        # mural-core may be started as a systemd user service before the
        # session manager has run `import-environment`, so these vars may
        # not be present in os.environ even though they exist in the
        # graphical session.  Copying os.environ and re-asserting the
        # specific vars makes the intent clear and ensures lwe can reach
        # the display server regardless of how mural-core was launched.
        env = os.environ.copy()
        for var in (
            "WAYLAND_DISPLAY",
            "DISPLAY",
            "XDG_RUNTIME_DIR",
            "DBUS_SESSION_BUS_ADDRESS",
            "XDG_SESSION_TYPE",
        ):
            if var in os.environ:
                env[var] = os.environ[var]
            else:
                logger.debug("lwe env: %s is not set", var)

        # lwe requires XDG_SESSION_TYPE to select its display backend.
        # logind/PAM sets it to "unspecified" for user sessions that
        # aren't full graphical logins, which lwe rejects.  Override it
        # with the actual session type inferred from display variables.
        if env.get("XDG_SESSION_TYPE") not in ("wayland", "x11", "mir"):
            if env.get("WAYLAND_DISPLAY"):
                env["XDG_SESSION_TYPE"] = "wayland"
                logger.debug("lwe env: inferred XDG_SESSION_TYPE=wayland from WAYLAND_DISPLAY")
            elif env.get("DISPLAY"):
                env["XDG_SESSION_TYPE"] = "x11"
                logger.debug("lwe env: inferred XDG_SESSION_TYPE=x11 from DISPLAY")

        # Hard guard: kill any lwe process owned by this user before spawning.
        # Catches processes that slipped past stop() or the orphan cleanup,
        # preventing multiple simultaneous lwe instances.
        current_uid = os.getuid()
        for proc in psutil.process_iter(["pid", "name", "uids", "cmdline"]):
            try:
                name = proc.info.get("name") or ""
                cmdline = proc.info.get("cmdline") or []
                uids = proc.info.get("uids")
                is_lwe = name == _LWE_PROCESS_NAME or any(
                    _LWE_PROCESS_NAME in str(arg) for arg in cmdline
                )
                if is_lwe and uids and uids.real == current_uid:
                    if exclude_pid is not None and proc.pid == exclude_pid:
                        continue  # keep old process alive during overlap transition
                    logger.warning("Pre-spawn guard: killing lwe (pid=%d)", proc.pid)
                    try:
                        pgid = os.getpgid(proc.pid)
                        os.killpg(pgid, signal.SIGKILL)
                    except (ProcessLookupError, PermissionError):
                        try:
                            proc.kill()
                        except (psutil.NoSuchProcess, psutil.AccessDenied):
                            pass
                    try:
                        proc.wait(timeout=2.0)
                    except psutil.TimeoutExpired:
                        pass
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass

        self._process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            preexec_fn=os.setsid,  # new process group → clean kill
            env=env,
        )
        self._start_time = time.monotonic()
        logger.info("lwe started (pid=%d)", self._process.pid)

        self._monitor_thread = threading.Thread(
            target=self._monitor_process,
            name="lwe-monitor",
            daemon=True,
        )
        self._monitor_thread.start()

    def _stop_process(self) -> None:
        """Send SIGTERM to the process group; escalate to SIGKILL after timeout."""
        if not self._process:
            return
        if self._process.poll() is not None:
            self._process = None
            return

        pid = self._process.pid
        try:
            pgid = os.getpgid(pid)
            logger.debug("Sending SIGTERM to process group %d", pgid)
            os.killpg(pgid, signal.SIGTERM)
        except ProcessLookupError:
            pass

        try:
            self._process.wait(timeout=_SIGTERM_TIMEOUT)
            logger.debug("lwe exited cleanly after SIGTERM")
        except subprocess.TimeoutExpired:
            logger.warning("lwe did not exit after SIGTERM; sending SIGKILL")
            try:
                pgid = os.getpgid(pid)
                os.killpg(pgid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            self._process.wait()

        self._process = None

    # ------------------------------------------------------------------
    # Background monitor thread
    # ------------------------------------------------------------------

    def _monitor_process(self) -> None:
        """Watch the lwe process and handle unexpected exits.

        Runs in a daemon thread.  Exits when the stop event is set or
        the process exits after ``stop()`` was called.
        """
        process = self._process
        if not process:
            return

        returncode = process.wait()

        # Log whatever lwe wrote to stderr so failures are diagnosable.
        if process.stderr:
            try:
                stderr_out = process.stderr.read().decode("utf-8", errors="replace").strip()
                if stderr_out:
                    for line in stderr_out.splitlines():
                        logger.warning("lwe stderr: %s", line)
            except Exception:
                pass

        # If self._process has been replaced (overlap transition), this
        # monitor was watching the retired process — exit without restarting.
        if self._process is not process:
            return

        # Intentional stop (stop()/start()/restart() killed lwe) or clean
        # exit (lwe exits 0 on SIGTERM) — no auto-restart in either case.
        if self._intentional_stop or returncode == 0:
            self._intentional_stop = False
            return

        if self._stop_event.is_set():
            return

        logger.warning("lwe exited unexpectedly (returncode=%d)", returncode)

        if self._on_unexpected_exit:
            try:
                self._on_unexpected_exit(returncode)
            except Exception:
                logger.exception("on_unexpected_exit callback raised")

        uptime = time.monotonic() - self._start_time
        if uptime >= self._restart_grace:
            self._restart_count = 0

        if self._auto_restart and self._restart_count < self._max_restarts:
            self._restart_count += 1
            backoff = min(2 ** (self._restart_count - 1), 30)
            logger.info(
                "Auto-restarting lwe in %.0fs (attempt %d/%d)",
                backoff,
                self._restart_count,
                self._max_restarts,
            )
            # Interruptible sleep: stop()/start() sets _stop_event to wake us early.
            self._stop_event.wait(timeout=backoff)
            with self._lock:
                if not self._stop_event.is_set():
                    self._start_process()
        else:
            logger.error(
                "lwe exited and will not be restarted (max_restarts=%d reached or auto_restart=False)",
                self._max_restarts,
            )

    # ------------------------------------------------------------------
    # Orphan cleanup
    # ------------------------------------------------------------------

    @staticmethod
    def kill_orphans(exclude_pid: int | None = None) -> int:
        """Kill any lwe processes not owned by this runner.

        Scans ``/proc`` via psutil for processes named
        ``linux-wallpaperengine`` and terminates them.  Called at service
        startup to clean up after a previous crash.

        Args:
            exclude_pid: PID to skip (used when an old process is being
                retired via overlap transition and must stay alive briefly).

        Returns:
            Number of orphaned processes killed.
        """
        killed = 0
        for proc in psutil.process_iter(["pid", "name", "cmdline"]):
            try:
                name = proc.info.get("name") or ""
                cmdline = proc.info.get("cmdline") or []
                is_lwe = name == _LWE_PROCESS_NAME or any(
                    _LWE_PROCESS_NAME in str(arg) for arg in cmdline
                )
                if is_lwe:
                    if exclude_pid is not None and proc.pid == exclude_pid:
                        continue
                    logger.warning(
                        "Killing orphaned lwe process (pid=%d)", proc.pid
                    )
                    proc.terminate()
                    try:
                        proc.wait(timeout=3.0)
                    except psutil.TimeoutExpired:
                        proc.kill()
                    killed += 1
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass

        if killed:
            logger.info("Killed %d orphaned lwe process(es)", killed)
        return killed

    # ------------------------------------------------------------------
    # Context manager support
    # ------------------------------------------------------------------

    def __enter__(self) -> "BackendRunner":
        return self

    def __exit__(self, *_: object) -> None:
        self.stop()

    def __repr__(self) -> str:
        state = "running" if self.is_running() else "stopped"
        return f"<BackendRunner binary={self._binary.name!r} state={state} pid={self.pid}>"
