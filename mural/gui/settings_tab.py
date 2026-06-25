# mural/gui/settings_tab.py
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

"""Settings tab — monitor assignments, playback, performance, and autostart.

Sections
--------
Monitors    — per-monitor wallpaper enable/disable and current assignment.
Playback    — FPS cap, audio mute, battery pause, fullscreen detection.
Performance — quality profile that maps to lwe runtime flags.
Autostart   — systemd ``--user enable/disable`` for mural-core.service.

Settings are persisted to ``~/.config/mural/settings.json`` and applied
to the Core Service immediately via D-Bus when Save is clicked.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

import json

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSlider,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

_CONFIG_DIR = Path("~/.config/mural").expanduser()
_SETTINGS_JSON = _CONFIG_DIR / "settings.json"
_SYSTEMD_UNIT = "mural-core.service"

# Maps quality profile name → lwe CLI flags (appended to BackendRunner command in Phase 2+)
_QUALITY_PROFILES: dict[str, dict[str, Any]] = {
    "Low":    {"fps": 15, "noautomute": False, "quality": "low"},
    "Medium": {"fps": 30, "noautomute": False, "quality": "medium"},
    "High":   {"fps": 60, "noautomute": False, "quality": "high"},
    "Ultra":  {"fps": 0,  "noautomute": True,  "quality": "ultra"},
}

_DEFAULT_SETTINGS: dict[str, Any] = {
    "fps_limit": 30,
    "mute_audio": False,
    "pause_on_battery": True,
    "fullscreen_pause": True,
    "quality_profile": "Medium",
    "autostart": True,
    "playlist_interval_minutes": 0,   # 0 = disabled
    "monitor_assignments": {},
}


def _load_settings() -> dict[str, Any]:
    """Load settings from disk, falling back to defaults for missing keys."""
    if not _SETTINGS_JSON.exists():
        return dict(_DEFAULT_SETTINGS)
    try:
        data = json.loads(_SETTINGS_JSON.read_text(encoding="utf-8"))
        return {**_DEFAULT_SETTINGS, **data}
    except Exception:
        return dict(_DEFAULT_SETTINGS)


def _save_settings(settings: dict[str, Any]) -> None:
    """Persist *settings* to ``~/.config/mural/settings.json``."""
    _CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    _SETTINGS_JSON.write_text(
        json.dumps(settings, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


def _systemctl_user(*args: str) -> bool:
    """Run ``systemctl --user <args>`` and return ``True`` on success."""
    try:
        result = subprocess.run(
            ["systemctl", "--user", *args],
            capture_output=True,
            timeout=10,
        )
        return result.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


def _service_is_enabled() -> bool:
    """Return ``True`` if mural-core.service is enabled for autostart."""
    return _systemctl_user("is-enabled", "--quiet", _SYSTEMD_UNIT)


# ---------------------------------------------------------------------------
# Settings tab
# ---------------------------------------------------------------------------

class SettingsTab(QWidget):
    """Application settings panel.

    Args:
        core_proxy: dasbus proxy for ``com.mural.Core``.  May be ``None``
            when the Core Service is not running; the tab still loads but
            live monitor data is unavailable.
        parent: Optional Qt parent widget.
    """

    settings_saved: Signal = Signal(dict)   # emitted after a successful save

    def __init__(self, core_proxy: Any | None = None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._core = core_proxy
        self._settings = _load_settings()
        self._build_ui()
        self._populate_from_settings()
        self._refresh_monitors()

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        # Scrollable content area so the tab works at any window height.
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(scroll.Shape.NoFrame)
        outer.addWidget(scroll, 1)

        content = QWidget()
        scroll.setWidget(content)

        layout = QVBoxLayout(content)
        layout.setContentsMargins(16, 12, 16, 12)
        layout.setSpacing(14)

        layout.addWidget(self._build_monitors_section())
        layout.addWidget(self._build_playback_section())
        layout.addWidget(self._build_performance_section())
        layout.addWidget(self._build_playlist_section())
        layout.addWidget(self._build_autostart_section())
        layout.addStretch()

        # Save / Reset bar pinned at the bottom.
        btn_bar = QHBoxLayout()
        btn_bar.setContentsMargins(16, 6, 16, 10)

        self._status_label = QLabel()
        self._status_label.setStyleSheet("color: #888; font-size: 11px;")
        btn_bar.addWidget(self._status_label)
        btn_bar.addStretch()

        reset_btn = QPushButton("Reset to Defaults")
        reset_btn.clicked.connect(self._reset_to_defaults)
        btn_bar.addWidget(reset_btn)

        save_btn = QPushButton("Save")
        save_btn.setDefault(True)
        save_btn.clicked.connect(self._save)
        btn_bar.addWidget(save_btn)

        outer.addLayout(btn_bar)

    # ------------------------------------------------------------------
    # Monitors section
    # ------------------------------------------------------------------

    def _build_monitors_section(self) -> QGroupBox:
        box = QGroupBox("Monitors")
        layout = QVBoxLayout(box)

        self._monitor_table = QTableWidget(0, 4)
        self._monitor_table.setHorizontalHeaderLabels(["Output", "Current Wallpaper", "Enabled", "Playlist"])
        self._monitor_table.horizontalHeader().setStretchLastSection(False)
        self._monitor_table.horizontalHeader().setMinimumSectionSize(70)
        self._monitor_table.setColumnWidth(0, 110)
        self._monitor_table.setColumnWidth(1, 280)
        self._monitor_table.setColumnWidth(2, 70)
        self._monitor_table.setColumnWidth(3, 150)
        self._monitor_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._monitor_table.setSelectionMode(QTableWidget.SelectionMode.NoSelection)
        self._monitor_table.setAlternatingRowColors(True)
        self._monitor_table.setFixedHeight(130)
        layout.addWidget(self._monitor_table)

        detect_btn = QPushButton("Re-detect Monitors")
        detect_btn.setFixedWidth(160)
        detect_btn.clicked.connect(self._refresh_monitors)
        layout.addWidget(detect_btn, alignment=Qt.AlignmentFlag.AlignLeft)

        return box

    def _refresh_monitors(self) -> None:
        """Query the Core Service for monitor list and populate the table."""
        self._monitor_table.setRowCount(0)

        monitors: list[str] = []
        playlists: list[dict] = []
        if self._core:
            try:
                monitors = list(self._core.GetMonitors())
            except Exception:
                pass
            try:
                playlists = json.loads(self._core.GetPlaylists())
            except Exception:
                pass

        # Build monitor → playlist name map
        mon_to_playlist: dict[str, str] = {}
        for pl in playlists:
            for mon in pl.get("monitor_assignments", []):
                mon_to_playlist[mon] = pl.get("id", "")

        if not monitors:
            self._monitor_table.setRowCount(1)
            placeholder = QTableWidgetItem("No monitors detected — start Core Service first")
            placeholder.setForeground(Qt.GlobalColor.darkGray)
            self._monitor_table.setItem(0, 0, placeholder)
            self._monitor_table.setSpan(0, 0, 1, 4)
            return

        assignments: dict = self._settings.get("monitor_assignments", {})

        for row, name in enumerate(monitors):
            self._monitor_table.insertRow(row)

            # Output name
            self._monitor_table.setItem(row, 0, QTableWidgetItem(name))

            # Current wallpaper
            wallpaper = ""
            if self._core:
                try:
                    wallpaper = self._core.GetCurrentWallpaper(name) or ""
                except Exception:
                    pass
            wp_item = QTableWidgetItem(wallpaper or "(none)")
            wp_item.setToolTip(wallpaper)
            self._monitor_table.setItem(row, 1, wp_item)

            # Enabled checkbox
            enabled = assignments.get(name, {}).get("enabled", True)
            chk = QCheckBox()
            chk.setChecked(enabled)
            chk.setProperty("monitorName", name)
            chk_widget = QWidget()
            chk_layout = QHBoxLayout(chk_widget)
            chk_layout.addWidget(chk)
            chk_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
            chk_layout.setContentsMargins(0, 0, 0, 0)
            self._monitor_table.setCellWidget(row, 2, chk_widget)

            # Playlist dropdown
            combo = QComboBox()
            combo.addItem("— None —", "")
            for pl in playlists:
                combo.addItem(pl.get("name", "?"), pl.get("id", ""))
            current_pl_id = mon_to_playlist.get(name, "")
            for idx in range(combo.count()):
                if combo.itemData(idx) == current_pl_id:
                    combo.setCurrentIndex(idx)
                    break
            combo.setProperty("monitorName", name)
            combo.currentIndexChanged.connect(
                lambda _idx, m=name, cb=combo: self._on_monitor_playlist_changed(m, cb)
            )
            self._monitor_table.setCellWidget(row, 3, combo)

    def _on_monitor_playlist_changed(self, monitor: str, combo: QComboBox) -> None:
        """Called when the user changes a monitor's playlist dropdown."""
        if not self._core:
            return
        playlist_id: str = combo.currentData() or ""
        try:
            if playlist_id:
                self._core.AssignPlaylistToMonitor(playlist_id, monitor)
            else:
                # "None" selected — unassign from whichever playlist owns it.
                playlists = json.loads(self._core.GetPlaylists())
                for pl in playlists:
                    if monitor in pl.get("monitor_assignments", []):
                        self._core.UnassignPlaylistFromMonitor(pl["id"], monitor)
                        break
        except Exception:
            pass

    def _collect_monitor_assignments(self) -> dict[str, dict]:
        """Read enabled state from the monitor table into a dict."""
        assignments: dict[str, dict] = {}
        for row in range(self._monitor_table.rowCount()):
            name_item = self._monitor_table.item(row, 0)
            if not name_item or not name_item.text():
                continue
            name = name_item.text()
            chk_widget = self._monitor_table.cellWidget(row, 2)
            enabled = True
            if chk_widget:
                chk: QCheckBox | None = chk_widget.findChild(QCheckBox)
                if chk:
                    enabled = chk.isChecked()
            assignments[name] = {"enabled": enabled}
        return assignments

    # ------------------------------------------------------------------
    # Playback section
    # ------------------------------------------------------------------

    def _build_playback_section(self) -> QGroupBox:
        box = QGroupBox("Playback")
        form = QFormLayout(box)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        form.setHorizontalSpacing(16)
        form.setVerticalSpacing(10)

        # FPS limit
        fps_row = QHBoxLayout()
        self._fps_spin = QSpinBox()
        self._fps_spin.setRange(0, 240)
        self._fps_spin.setValue(30)
        self._fps_spin.setSuffix(" fps")
        self._fps_spin.setFixedWidth(90)
        fps_row.addWidget(self._fps_spin)
        fps_row.addWidget(QLabel("(0 = unlimited)"))
        fps_row.addStretch()
        form.addRow("FPS limit:", fps_row)

        self._mute_chk = QCheckBox("Mute wallpaper audio")
        form.addRow("Audio:", self._mute_chk)

        self._battery_chk = QCheckBox("Pause wallpaper when on battery")
        form.addRow("Battery:", self._battery_chk)

        self._fullscreen_chk = QCheckBox(
            "Pause wallpaper when a fullscreen window is detected"
        )
        form.addRow("Fullscreen:", self._fullscreen_chk)

        return box

    # ------------------------------------------------------------------
    # Performance section
    # ------------------------------------------------------------------

    def _build_performance_section(self) -> QGroupBox:
        box = QGroupBox("Performance")
        layout = QVBoxLayout(box)

        self._quality_combo = QComboBox()
        for name in _QUALITY_PROFILES:
            self._quality_combo.addItem(name)
        self._quality_combo.currentTextChanged.connect(self._on_quality_changed)

        row = QHBoxLayout()
        row.addWidget(QLabel("Quality profile:"))
        row.addWidget(self._quality_combo)
        row.addStretch()
        layout.addLayout(row)

        self._quality_note = QLabel()
        self._quality_note.setStyleSheet("color: #888; font-size: 11px;")
        self._quality_note.setWordWrap(True)
        layout.addWidget(self._quality_note)

        self._on_quality_changed(self._quality_combo.currentText())
        return box

    def _on_quality_changed(self, name: str) -> None:
        profile = _QUALITY_PROFILES.get(name, {})
        fps = profile.get("fps", 30)
        fps_str = "unlimited" if fps == 0 else f"{fps} fps"
        self._quality_note.setText(
            f"Renders at {fps_str}.  "
            "Changing the profile updates the FPS limit above and is applied on next save."
        )
        self._fps_spin.setValue(fps)

    # ------------------------------------------------------------------
    # Playlist section
    # ------------------------------------------------------------------

    def _build_playlist_section(self) -> QGroupBox:
        box = QGroupBox("Playlist / Auto-Rotate")
        form = QFormLayout(box)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        form.setHorizontalSpacing(16)
        form.setVerticalSpacing(10)

        interval_row = QHBoxLayout()
        self._playlist_spin = QSpinBox()
        self._playlist_spin.setRange(0, 1440)
        self._playlist_spin.setValue(0)
        self._playlist_spin.setSuffix(" min")
        self._playlist_spin.setFixedWidth(90)
        interval_row.addWidget(self._playlist_spin)
        interval_row.addWidget(QLabel("(0 = disabled)"))
        interval_row.addStretch()
        form.addRow("Rotate every:", interval_row)

        note = QLabel(
            "When enabled, Mural picks a random wallpaper from your library at each interval.\n"
            "Full playlist editor coming in a later release."
        )
        note.setWordWrap(True)
        note.setStyleSheet("color: #888; font-size: 11px;")
        form.addRow("", note)

        self._playlist_status_label = QLabel()
        self._playlist_status_label.setStyleSheet("font-size: 11px; color: #888;")
        form.addRow("Status:", self._playlist_status_label)
        self._refresh_playlist_status()

        return box

    def _refresh_playlist_status(self) -> None:
        """Query the Core Service for the current playlist status and update the label."""
        if not self._core:
            self._playlist_status_label.setText("Core Service not connected")
            return
        try:
            data = json.loads(self._core.GetPlaylistStatus())
        except Exception:
            self._playlist_status_label.setText("")
            return
        running = data.get("timer_running", False)
        global_interval = data.get("global_interval_minutes", 0)
        playlists = data.get("playlists", [])
        active = [p for p in playlists if p.get("monitors")]
        if running and active:
            self._playlist_status_label.setText(
                f"Auto-rotating · {len(active)} playlist(s) active · global interval: {global_interval} min"
            )
            self._playlist_status_label.setStyleSheet("font-size: 11px; color: #00C853;")
        elif global_interval > 0:
            self._playlist_status_label.setText(
                f"Timer active (every {global_interval} min) · no playlists assigned to monitors"
            )
            self._playlist_status_label.setStyleSheet("font-size: 11px; color: #FFA000;")
        else:
            self._playlist_status_label.setText("Auto-rotate disabled")
            self._playlist_status_label.setStyleSheet("font-size: 11px; color: #888;")

    # ------------------------------------------------------------------
    # Autostart section
    # ------------------------------------------------------------------

    def _build_autostart_section(self) -> QGroupBox:
        box = QGroupBox("Autostart")
        layout = QVBoxLayout(box)

        self._autostart_chk = QCheckBox(
            "Start Mural Core Service automatically at login\n"
            "(enables/disables the mural-core.service systemd user unit)"
        )
        layout.addWidget(self._autostart_chk)

        service_row = QHBoxLayout()
        self._service_status_label = QLabel()
        self._service_status_label.setStyleSheet("font-size: 11px;")
        service_row.addWidget(self._service_status_label)
        service_row.addStretch()

        restart_btn = QPushButton("Restart Core Service")
        restart_btn.setFixedHeight(28)
        restart_btn.clicked.connect(self._restart_service)
        service_row.addWidget(restart_btn)

        stop_btn = QPushButton("Stop Core Service")
        stop_btn.setFixedHeight(28)
        stop_btn.clicked.connect(self._stop_service)
        service_row.addWidget(stop_btn)

        layout.addLayout(service_row)
        self._refresh_service_status()
        return box

    def _refresh_service_status(self) -> None:
        """Update the service status label."""
        enabled = _service_is_enabled()
        running = False
        if self._core:
            try:
                status = self._core.GetStatus()
                running = bool(status.get("running", False))
            except Exception:
                pass

        status_parts = []
        status_parts.append("enabled" if enabled else "disabled")
        status_parts.append("running" if running else "stopped")
        self._service_status_label.setText(
            f"mural-core.service: {' · '.join(status_parts)}"
        )
        colour = "#00C853" if running else "#FF5252"
        self._service_status_label.setStyleSheet(f"font-size: 11px; color: {colour};")

    def _restart_service(self) -> None:
        ok = _systemctl_user("restart", _SYSTEMD_UNIT)
        self._status_label.setText(
            "Core Service restarted." if ok else "Failed to restart Core Service."
        )
        self._refresh_service_status()

    def _stop_service(self) -> None:
        ok = _systemctl_user("stop", _SYSTEMD_UNIT)
        self._status_label.setText(
            "Core Service stopped." if ok else "Failed to stop Core Service."
        )
        self._refresh_service_status()

    # ------------------------------------------------------------------
    # Populate / collect / save
    # ------------------------------------------------------------------

    def _populate_from_settings(self) -> None:
        """Fill all widgets from the current settings dict."""
        s = self._settings
        self._fps_spin.setValue(s.get("fps_limit", 30))
        self._mute_chk.setChecked(s.get("mute_audio", False))
        self._battery_chk.setChecked(s.get("pause_on_battery", True))
        self._fullscreen_chk.setChecked(s.get("fullscreen_pause", True))
        self._autostart_chk.setChecked(s.get("autostart", True))
        self._playlist_spin.setValue(s.get("playlist_interval_minutes", 0))

        profile = s.get("quality_profile", "Medium")
        idx = self._quality_combo.findText(profile)
        if idx >= 0:
            self._quality_combo.setCurrentIndex(idx)

    def _collect_settings(self) -> dict[str, Any]:
        """Read all widget values into a settings dict."""
        return {
            "fps_limit": self._fps_spin.value(),
            "mute_audio": self._mute_chk.isChecked(),
            "pause_on_battery": self._battery_chk.isChecked(),
            "fullscreen_pause": self._fullscreen_chk.isChecked(),
            "quality_profile": self._quality_combo.currentText(),
            "autostart": self._autostart_chk.isChecked(),
            "playlist_interval_minutes": self._playlist_spin.value(),
            "monitor_assignments": self._collect_monitor_assignments(),
        }

    def _save(self) -> None:
        """Write settings to disk and apply changes to the Core Service."""
        self._settings = self._collect_settings()
        _save_settings(self._settings)

        errors: list[str] = []

        # Restart lwe with new playback flags via Core Service.
        if self._core:
            try:
                self._core.ApplySettings()
            except Exception as exc:
                errors.append(f"D-Bus: {exc}")

        # Apply autostart via systemctl.
        want_autostart = self._settings["autostart"]
        current_enabled = _service_is_enabled()
        if want_autostart and not current_enabled:
            if not _systemctl_user("enable", _SYSTEMD_UNIT):
                errors.append("Could not enable mural-core.service")
        elif not want_autostart and current_enabled:
            if not _systemctl_user("disable", _SYSTEMD_UNIT):
                errors.append("Could not disable mural-core.service")

        self._refresh_service_status()
        self._refresh_playlist_status()

        if errors:
            self._status_label.setText("Saved with warnings: " + "; ".join(errors))
        else:
            self._status_label.setText("Settings saved.")

        self.settings_saved.emit(self._settings)

    def _reset_to_defaults(self) -> None:
        """Reset all widgets to default values (does not save until Save is clicked)."""
        self._settings = dict(_DEFAULT_SETTINGS)
        self._populate_from_settings()
        self._status_label.setText("Defaults loaded — click Save to apply.")

    # ------------------------------------------------------------------
    # Public helpers
    # ------------------------------------------------------------------

    def set_core_proxy(self, proxy: Any) -> None:
        """Update the Core Service proxy (called when service becomes available).

        Args:
            proxy: A dasbus proxy for ``com.mural.Core``.
        """
        self._core = proxy
        self._refresh_monitors()
        self._refresh_service_status()
        self._refresh_playlist_status()
