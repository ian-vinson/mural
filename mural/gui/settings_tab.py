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
import shutil
import subprocess
from pathlib import Path
from typing import Any

from PySide6.QtCore import Qt, QTime, Signal
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSlider,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QTimeEdit,
    QVBoxLayout,
    QWidget,
)

_POWER_PRESETS = {
    "Gaming":  {"fps_limit": 0,  "mute_audio": False, "fullscreen_pause": True, "quality_profile": "High"},
    "Work":    {"fps_limit": 30, "mute_audio": False, "fullscreen_pause": True, "quality_profile": "Medium"},
    "Battery": {"fps_limit": 15, "mute_audio": True,  "fullscreen_pause": True, "quality_profile": "Low"},
}

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

_SCHEDULE_SLOTS = [
    ("morning",   "Morning",   "06:00"),
    ("afternoon", "Afternoon", "12:00"),
    ("evening",   "Evening",   "18:00"),
    ("night",     "Night",     "22:00"),
]

_DEFAULT_SETTINGS: dict[str, Any] = {
    "fps_limit": 30,
    "mute_audio": False,
    "pause_on_battery": True,
    "fullscreen_pause": True,
    "disable_mouse": False,
    "disable_parallax": False,
    "quality_profile": "Medium",
    "autostart": True,
    "playlist_interval_minutes": 0,   # 0 = disabled
    "monitor_assignments": {},
    "pywal_source": "disabled",
    "pause_app_list": [],
    "time_schedule_enabled": False,
    "time_schedule": [
        {"slot": "morning",   "time": "06:00", "path": ""},
        {"slot": "afternoon", "time": "12:00", "path": ""},
        {"slot": "evening",   "time": "18:00", "path": ""},
        {"slot": "night",     "time": "22:00", "path": ""},
    ],
}


def _schedule_preview_image(wallpaper_path: str) -> str | None:
    """Return a path to a preview image for *wallpaper_path*, or None."""
    p = Path(wallpaper_path)
    if not p.is_dir():
        return None
    for name in ("preview.jpg", "preview.png", "preview.gif",
                 "preview.webp", "thumbnail.jpg", "thumbnail.png"):
        candidate = p / name
        if candidate.exists():
            return str(candidate)
    proj = p / "project.json"
    if proj.exists():
        try:
            data = json.loads(proj.read_text(encoding="utf-8"))
            preview = data.get("preview", "")
            if preview and (p / preview).exists():
                return str(p / preview)
        except Exception:
            pass
    return None


def _scan_library_dirs() -> list[Path]:
    """Return up to 200 wallpaper dirs from Steam Workshop + download dirs."""
    from mural.config import config as _mcfg, DOWNLOAD_DIR as _DDIR

    _STEAM_ROOTS = (
        "~/.steam/steam",
        "~/.local/share/Steam",
        "~/.var/app/com.valvesoftware.Steam/.local/share/Steam",
        "~/snap/steam/common/.local/share/Steam",
    )
    _WS_ID = "431960"

    dirs: list[Path] = []
    for root_str in _STEAM_ROOTS:
        wp = Path(root_str).expanduser() / "steamapps" / "workshop" / "content" / _WS_ID
        if wp.is_dir():
            dirs.extend(p for p in wp.iterdir() if p.is_dir())
    if _DDIR.is_dir():
        dirs.extend(p for p in _DDIR.iterdir() if p.is_dir())
    for extra in _mcfg.get("extra_library_dirs", []):
        ep = Path(extra).expanduser()
        if ep.is_dir():
            dirs.extend(c for c in ep.iterdir() if c.is_dir())
    return dirs[:200]


class _WallpaperPickerDialog(QDialog):
    """Minimal wallpaper picker that shows WallpaperCards from the local library."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        from mural.gui.wallpaper_card import WallpaperCard, WallpaperInfo

        self.setWindowTitle("Pick Wallpaper")
        self.resize(780, 520)
        self.setModal(True)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(8)

        self._selected_path: str | None = None

        # Scrollable card grid
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        content = QWidget()
        grid = QGridLayout(content)
        grid.setContentsMargins(4, 4, 4, 4)
        grid.setSpacing(8)

        dirs = _scan_library_dirs()
        COLS = 4
        for i, wp_dir in enumerate(dirs):
            name = wp_dir.name
            wp_type = "video"
            thumbnail = _schedule_preview_image(str(wp_dir))
            proj = wp_dir / "project.json"
            if proj.exists():
                try:
                    pdata = json.loads(proj.read_text(encoding="utf-8"))
                    name = pdata.get("title") or name
                    wp_type = pdata.get("type", "video").lower()
                except Exception:
                    pass
            info = WallpaperInfo(
                name=name,
                path=str(wp_dir),
                type=wp_type,
                thumbnail_path=thumbnail,
            )
            card = WallpaperCard(info)
            card.selected.connect(
                lambda inf, s=self: setattr(s, "_selected_path", inf.path)
            )
            card.apply_requested.connect(self._on_apply_requested)
            grid.addWidget(card, i // COLS, i % COLS)

        if not dirs:
            no_lbl = QLabel(
                "No wallpapers found.\n"
                "Add wallpapers to your library first."
            )
            no_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            no_lbl.setStyleSheet("color: #888; font-size: 13px;")
            grid.addWidget(no_lbl, 0, 0)

        content.setLayout(grid)
        scroll.setWidget(content)
        layout.addWidget(scroll, 1)

        # Buttons
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        select_btn = QPushButton("Select")
        select_btn.setDefault(True)
        select_btn.clicked.connect(self._on_select)
        btn_row.addWidget(select_btn)
        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(self.reject)
        btn_row.addWidget(cancel_btn)
        layout.addLayout(btn_row)

    def _on_apply_requested(self, info) -> None:
        self._selected_path = info.path
        self.accept()

    def _on_select(self) -> None:
        if self._selected_path:
            self.accept()

    def get_selected_path(self) -> str | None:
        return self._selected_path


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
        layout.addWidget(self._build_schedule_section())
        layout.addWidget(self._build_linux_integration_section())
        layout.addWidget(self._build_app_rules_section())
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

        battery_row = QHBoxLayout()
        self._battery_chk = QCheckBox("Pause when on battery")
        battery_row.addWidget(self._battery_chk)
        self._battery_status_label = QLabel()
        self._battery_status_label.setStyleSheet("font-size: 11px; color: #888;")
        battery_row.addWidget(self._battery_status_label)
        battery_row.addStretch()
        form.addRow("Battery:", battery_row)

        self._fullscreen_chk = QCheckBox(
            "Pause wallpaper when a fullscreen window is detected"
        )
        form.addRow("Fullscreen:", self._fullscreen_chk)

        self._disable_mouse_chk = QCheckBox("Disable mouse parallax effects")
        form.addRow("Mouse:", self._disable_mouse_chk)

        self._disable_parallax_chk = QCheckBox("Disable parallax depth effect")
        form.addRow("Parallax:", self._disable_parallax_chk)

        return box

    # ------------------------------------------------------------------
    # Performance section
    # ------------------------------------------------------------------

    def _build_performance_section(self) -> QGroupBox:
        box = QGroupBox("Performance")
        layout = QVBoxLayout(box)

        # Power-profile preset buttons
        preset_row = QHBoxLayout()
        preset_row.addWidget(QLabel("Presets:"))
        _btn_style = (
            "QPushButton { border: 1px solid #555; border-radius: 3px; "
            "padding: 2px 10px; font-size: 11px; background: transparent; } "
            "QPushButton:hover { border-color: #888; background: #2a2a2a; }"
        )
        for label, preset in _POWER_PRESETS.items():
            btn = QPushButton(label)
            btn.setFixedHeight(24)
            btn.setStyleSheet(_btn_style)
            btn.clicked.connect(lambda _checked=False, p=preset: self._apply_preset(p))
            preset_row.addWidget(btn)
        preset_row.addStretch()
        layout.addLayout(preset_row)

        self._quality_combo = QComboBox()
        for name in _QUALITY_PROFILES:
            self._quality_combo.addItem(name)
        self._quality_combo.currentTextChanged.connect(self._on_quality_changed)

        quality_row = QHBoxLayout()
        quality_row.addWidget(QLabel("Quality profile:"))
        quality_row.addWidget(self._quality_combo)
        quality_row.addStretch()
        layout.addLayout(quality_row)

        self._quality_note = QLabel()
        self._quality_note.setStyleSheet("color: #888; font-size: 11px;")
        self._quality_note.setWordWrap(True)
        layout.addWidget(self._quality_note)

        self._on_quality_changed(self._quality_combo.currentText())
        return box

    def _apply_preset(self, preset: dict) -> None:
        """Populate settings fields from a power-profile preset dict."""
        idx = self._quality_combo.findText(preset["quality_profile"])
        if idx >= 0:
            self._quality_combo.setCurrentIndex(idx)  # triggers _on_quality_changed
        # Override fps with the preset's actual value (profile fps ≠ preset fps for Gaming)
        self._fps_spin.setValue(preset["fps_limit"])
        self._mute_chk.setChecked(preset["mute_audio"])
        self._fullscreen_chk.setChecked(preset["fullscreen_pause"])

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
    # Time-of-day schedule section
    # ------------------------------------------------------------------

    def _build_schedule_section(self) -> QGroupBox:
        box = QGroupBox("Time of Day")
        outer = QVBoxLayout(box)
        outer.setSpacing(8)

        self._sched_enabled_chk = QCheckBox("Enable time of day scheduling")
        self._sched_enabled_chk.toggled.connect(self._on_sched_enabled_changed)
        outer.addWidget(self._sched_enabled_chk)

        # One row per slot
        self._sched_rows: list[dict] = []
        self._sched_slots_widget = QWidget()
        slots_layout = QVBoxLayout(self._sched_slots_widget)
        slots_layout.setContentsMargins(0, 0, 0, 0)
        slots_layout.setSpacing(6)

        for slot_key, slot_label, default_time in _SCHEDULE_SLOTS:
            row: dict = {"slot_key": slot_key, "path": ""}
            h = QHBoxLayout()
            h.setContentsMargins(0, 0, 0, 0)
            h.setSpacing(6)

            lbl = QLabel(slot_label)
            lbl.setFixedWidth(68)
            h.addWidget(lbl)

            te = QTimeEdit()
            te.setDisplayFormat("HH:mm")
            te.setFixedWidth(68)
            dh, dm = map(int, default_time.split(":"))
            te.setTime(QTime(dh, dm))
            row["time_edit"] = te
            h.addWidget(te)

            thumb = QLabel()
            thumb.setFixedSize(60, 40)
            thumb.setStyleSheet(
                "background: #1a1a2e; border: 1px solid #333; border-radius: 2px;"
            )
            thumb.setAlignment(Qt.AlignmentFlag.AlignCenter)
            row["thumb_label"] = thumb
            h.addWidget(thumb)

            path_lbl = QLabel("(not set)")
            path_lbl.setStyleSheet("color: #888; font-size: 11px;")
            path_lbl.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
            row["path_label"] = path_lbl
            h.addWidget(path_lbl, 1)

            pick_btn = QPushButton("Pick…")
            pick_btn.setFixedHeight(26)
            pick_btn.setFixedWidth(48)
            pick_btn.clicked.connect(
                lambda _c=False, r=row: self._pick_schedule_wallpaper(r)
            )
            h.addWidget(pick_btn)

            clear_btn = QPushButton("×")
            clear_btn.setFixedSize(26, 26)
            clear_btn.clicked.connect(
                lambda _c=False, r=row: self._set_schedule_path(r, "")
            )
            h.addWidget(clear_btn)

            slots_layout.addLayout(h)
            self._sched_rows.append(row)

        outer.addWidget(self._sched_slots_widget)

        self._sched_status_label = QLabel()
        self._sched_status_label.setStyleSheet("font-size: 11px; color: #888;")
        outer.addWidget(self._sched_status_label)

        return box

    def _on_sched_enabled_changed(self, checked: bool) -> None:
        self._sched_slots_widget.setEnabled(checked)

    def _pick_schedule_wallpaper(self, row: dict) -> None:
        dlg = _WallpaperPickerDialog(self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            path = dlg.get_selected_path()
            if path:
                self._set_schedule_path(row, path)

    def _set_schedule_path(self, row: dict, path: str) -> None:
        row["path"] = path
        if path:
            row["path_label"].setText(Path(path).name)
            row["path_label"].setStyleSheet("font-size: 11px; color: #e0e0e0;")
            preview = _schedule_preview_image(path)
            if preview:
                px = QPixmap(preview)
                if not px.isNull():
                    row["thumb_label"].setPixmap(
                        px.scaled(
                            60, 40,
                            Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                            Qt.TransformationMode.SmoothTransformation,
                        )
                    )
                    return
            row["thumb_label"].clear()
            row["thumb_label"].setText("?")
        else:
            row["path_label"].setText("(not set)")
            row["path_label"].setStyleSheet("color: #888; font-size: 11px;")
            row["thumb_label"].clear()

    def _refresh_schedule_status(self) -> None:
        if not self._core:
            self._sched_status_label.setText("")
            return
        try:
            active = self._core.GetScheduleStatus()
        except Exception:
            self._sched_status_label.setText("")
            return
        if active and active != "none":
            self._sched_status_label.setText(f"Active slot: {active.capitalize()}")
            self._sched_status_label.setStyleSheet("font-size: 11px; color: #00C853;")
        elif self._sched_enabled_chk.isChecked():
            self._sched_status_label.setText("Scheduling enabled — no slot active yet")
            self._sched_status_label.setStyleSheet("font-size: 11px; color: #888;")
        else:
            self._sched_status_label.setText("Time scheduling disabled")
            self._sched_status_label.setStyleSheet("font-size: 11px; color: #888;")

    # ------------------------------------------------------------------
    # Linux Integration section
    # ------------------------------------------------------------------

    def _build_linux_integration_section(self) -> QGroupBox:
        box = QGroupBox("Linux Integration")
        form = QFormLayout(box)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        form.setHorizontalSpacing(16)
        form.setVerticalSpacing(10)

        self._pywal_source_combo = QComboBox()
        self._pywal_source_combo.addItem("Disabled", userData="disabled")
        self._pywal_source_combo.addItem("Primary monitor only", userData="primary")
        self._pywal_source_combo.addItem("Any monitor change", userData="last")
        form.addRow("Pywal source:", self._pywal_source_combo)

        pywal_status = QLabel()
        if shutil.which("wal"):
            pywal_status.setText("pywal detected")
            pywal_status.setStyleSheet("font-size: 11px; color: #00C853;")
        else:
            pywal_status.setText("pywal not found — install python-pywal")
            pywal_status.setStyleSheet("font-size: 11px; color: #888;")
        form.addRow("", pywal_status)

        setup_toggle = QPushButton("▶ Setup guide")
        setup_toggle.setFlat(True)
        setup_toggle.setStyleSheet(
            "QPushButton { font-size: 11px; color: #aaa; text-align: left;"
            " padding: 0; border: none; background: transparent; }"
            "QPushButton:hover { color: #ddd; }"
        )
        setup_toggle.setCursor(Qt.CursorShape.PointingHandCursor)

        self._pywal_guide = QLabel(
            "1. Install pywal: <tt>pip install pywal</tt><br>"
            "2. Run once to generate a scheme: <tt>wal -i /path/to/image.jpg</tt><br>"
            "3. Add <tt>~/.config/wpg/formats/colors.sh</tt> to your shell profile.<br>"
            "4. Set Pywal source above and apply a wallpaper — Mural will call <tt>wal</tt> automatically."
        )
        self._pywal_guide.setWordWrap(True)
        self._pywal_guide.setStyleSheet("font-size: 11px; color: #888; padding-left: 8px;")
        self._pywal_guide.hide()

        def _toggle_guide() -> None:
            visible = not self._pywal_guide.isVisible()
            self._pywal_guide.setVisible(visible)
            setup_toggle.setText(("▼" if visible else "▶") + " Setup guide")

        setup_toggle.clicked.connect(_toggle_guide)
        form.addRow("", setup_toggle)
        form.addRow("", self._pywal_guide)

        return box

    # ------------------------------------------------------------------
    # App Rules section
    # ------------------------------------------------------------------

    def _build_app_rules_section(self) -> QGroupBox:
        box = QGroupBox("App Rules")
        form = QFormLayout(box)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        form.setHorizontalSpacing(16)
        form.setVerticalSpacing(10)

        self._app_list_edit = QPlainTextEdit()
        self._app_list_edit.setPlaceholderText(
            "one process name per line, e.g.\nsteam\nobs\nblender"
        )
        self._app_list_edit.setFixedHeight(72)
        form.addRow("Pause when these\napps are running:", self._app_list_edit)

        note = QLabel("Process names are matched case-insensitively. "
                       "Checked every 10 seconds.")
        note.setWordWrap(True)
        note.setStyleSheet("color: #888; font-size: 11px;")
        form.addRow("", note)

        self._app_rule_status_label = QLabel()
        self._app_rule_status_label.setStyleSheet("font-size: 11px; color: #888;")
        form.addRow("Status:", self._app_rule_status_label)

        return box

    def _refresh_battery_status(self) -> None:
        if not self._core:
            self._battery_status_label.setText("")
            return
        try:
            status = self._core.GetPowerStatus()
        except Exception:
            self._battery_status_label.setText("")
            return
        if status == "battery":
            self._battery_status_label.setText("On battery")
            self._battery_status_label.setStyleSheet("font-size: 11px; color: #FFA000;")
        elif status == "ac":
            self._battery_status_label.setText("On AC power")
            self._battery_status_label.setStyleSheet("font-size: 11px; color: #00C853;")
        else:
            self._battery_status_label.setText("Unknown power source")
            self._battery_status_label.setStyleSheet("font-size: 11px; color: #888;")

    def _refresh_app_rule_status(self) -> None:
        if not self._core:
            self._app_rule_status_label.setText("")
            return
        try:
            status = self._core.GetAppRuleStatus()
        except Exception:
            self._app_rule_status_label.setText("")
            return
        if status.startswith("paused:"):
            app_name = status.split(":", 1)[1]
            self._app_rule_status_label.setText(f"Paused — {app_name} is running")
            self._app_rule_status_label.setStyleSheet("font-size: 11px; color: #FFA000;")
        else:
            self._app_rule_status_label.setText("Running")
            self._app_rule_status_label.setStyleSheet("font-size: 11px; color: #00C853;")

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
        self._disable_mouse_chk.setChecked(s.get("disable_mouse", False))
        self._disable_parallax_chk.setChecked(s.get("disable_parallax", False))
        self._autostart_chk.setChecked(s.get("autostart", True))
        self._playlist_spin.setValue(s.get("playlist_interval_minutes", 0))
        pywal_source = s.get("pywal_source", "disabled")
        idx = self._pywal_source_combo.findData(pywal_source)
        self._pywal_source_combo.setCurrentIndex(max(idx, 0))
        self._app_list_edit.setPlainText(
            "\n".join(s.get("pause_app_list", []))
        )

        profile = s.get("quality_profile", "Medium")
        idx = self._quality_combo.findText(profile)
        if idx >= 0:
            self._quality_combo.setCurrentIndex(idx)

        # Time-of-day schedule
        enabled = bool(s.get("time_schedule_enabled", False))
        self._sched_enabled_chk.setChecked(enabled)
        self._on_sched_enabled_changed(enabled)
        schedule = s.get("time_schedule", [])
        slot_map = {
            e.get("slot", ""): e
            for e in schedule
            if isinstance(e, dict)
        }
        for row in self._sched_rows:
            entry = slot_map.get(row["slot_key"], {})
            time_str = entry.get("time", "00:00")
            try:
                th, tm = map(int, time_str.split(":"))
            except (ValueError, AttributeError):
                th, tm = 0, 0
            row["time_edit"].setTime(QTime(th, tm))
            self._set_schedule_path(row, entry.get("path", ""))

        self._refresh_battery_status()
        self._refresh_app_rule_status()
        self._refresh_schedule_status()

    def _collect_settings(self) -> dict[str, Any]:
        """Read all widget values into a settings dict."""
        raw_app_text = self._app_list_edit.toPlainText()
        pause_app_list = [
            line.strip() for line in raw_app_text.splitlines() if line.strip()
        ]
        schedule = [
            {
                "slot": row["slot_key"],
                "time": row["time_edit"].time().toString("HH:mm"),
                "path": row.get("path", ""),
            }
            for row in self._sched_rows
        ]
        return {
            "fps_limit": self._fps_spin.value(),
            "mute_audio": self._mute_chk.isChecked(),
            "pause_on_battery": self._battery_chk.isChecked(),
            "fullscreen_pause": self._fullscreen_chk.isChecked(),
            "disable_mouse": self._disable_mouse_chk.isChecked(),
            "disable_parallax": self._disable_parallax_chk.isChecked(),
            "quality_profile": self._quality_combo.currentText(),
            "autostart": self._autostart_chk.isChecked(),
            "playlist_interval_minutes": self._playlist_spin.value(),
            "monitor_assignments": self._collect_monitor_assignments(),
            "pywal_source": self._pywal_source_combo.currentData(),
            "pause_app_list": pause_app_list,
            "time_schedule_enabled": self._sched_enabled_chk.isChecked(),
            "time_schedule": schedule,
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
        self._refresh_battery_status()
        self._refresh_app_rule_status()
        self._refresh_schedule_status()

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
        self._refresh_battery_status()
        self._refresh_app_rule_status()
        self._refresh_schedule_status()
