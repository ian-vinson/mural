# mural/gui/mainwindow.py
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

"""Main application window.

Layout (mirrors the DEVGUIDE specification)::

    ┌──────────────────────────────────────────────────┐
    │  [Library]  [Platform]  [Settings]               │
    ├────────────────────────┬─────────────────────────┤
    │                        │  Preview Panel          │
    │  Wallpaper grid        │  ┌───────────────────┐  │
    │  (active tab content)  │  │  Thumbnail        │  │
    │                        │  └───────────────────┘  │
    │                        │  Name · Type · Author   │
    │                        │  Resolution · Tags      │
    │                        │                         │
    │                        │  [Set as Wallpaper ▼]   │
    │                        │  [Download]             │
    └────────────────────────┴─────────────────────────┘
    │ Status bar                                       │
    └──────────────────────────────────────────────────┘

The Settings tab replaces the left panel at full width; the preview
panel is hidden while Settings is active.

Connections
-----------
LibraryTab.wallpaper_selected   → _PreviewPanel.show_wallpaper
PlatformTab.wallpaper_selected  → _PreviewPanel.show_wallpaper
_PreviewPanel "Set as Wallpaper"→ core.SetWallpaper(monitor, path)
PlatformTab.wallpaper_downloaded→ LibraryTab.refresh
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

from PySide6.QtCore import QSize, Qt, QTimer
from PySide6.QtGui import QAction, QIcon, QKeySequence, QPixmap
from PySide6.QtWidgets import (
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSplitter,
    QStackedWidget,
    QStatusBar,
    QTabBar,
    QToolBar,
    QVBoxLayout,
    QWidget,
)

from mural.gui.library_tab import LibraryTab
from mural.gui.platform_tab import PlatformTab
from mural.gui.settings_tab import SettingsTab
from mural.gui.wallpaper_card import WallpaperInfo

_PREVIEW_MIN_W = 280
_PREVIEW_MAX_W = 380
_THUMB_MAX_H   = 200
_WIN_MIN_W     = 920
_WIN_MIN_H     = 580


# ---------------------------------------------------------------------------
# Preview panel
# ---------------------------------------------------------------------------

class _PreviewPanel(QWidget):
    """The right-hand panel showing metadata and apply controls for the
    currently selected wallpaper.

    Args:
        core_proxy: dasbus proxy for ``com.mural.Core``.  May be ``None``.
        platform_tab: Reference used to trigger downloads.
        parent: Optional Qt parent.
    """

    def __init__(
        self,
        core_proxy: Any | None,
        platform_tab: PlatformTab,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._core = core_proxy
        self._platform_tab = platform_tab
        self._current_info: WallpaperInfo | None = None

        self.setMinimumWidth(_PREVIEW_MIN_W)
        self.setMaximumWidth(_PREVIEW_MAX_W)
        self._build_ui()
        self._show_empty_state()

    # ------------------------------------------------------------------
    # Construction
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(10)

        # Thumbnail
        self._thumb_label = QLabel()
        self._thumb_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._thumb_label.setFixedHeight(_THUMB_MAX_H)
        self._thumb_label.setStyleSheet(
            "background: #1A1A1A; border-radius: 6px;"
        )
        self._thumb_label.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed
        )
        layout.addWidget(self._thumb_label)

        # Metadata
        meta_frame = QFrame()
        meta_frame.setFrameShape(QFrame.Shape.NoFrame)
        meta_layout = QVBoxLayout(meta_frame)
        meta_layout.setContentsMargins(0, 0, 0, 0)
        meta_layout.setSpacing(4)

        self._name_label = QLabel()
        self._name_label.setWordWrap(True)
        self._name_label.setStyleSheet("font-size: 14px; font-weight: bold;")
        meta_layout.addWidget(self._name_label)

        self._type_label  = _meta_row("Type:", "")
        self._author_label = _meta_row("Author:", "")
        self._res_label   = _meta_row("Resolution:", "")
        self._size_label  = _meta_row("Size:", "")
        self._tags_label  = _meta_row("Tags:", "")
        for lbl in (self._type_label, self._author_label,
                    self._res_label, self._size_label, self._tags_label):
            meta_layout.addWidget(lbl)

        layout.addWidget(meta_frame)
        layout.addStretch()

        # Separator
        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet("color: #333;")
        layout.addWidget(sep)

        # Monitor selector
        monitor_row = QHBoxLayout()
        monitor_row.addWidget(QLabel("Monitor:"))
        self._monitor_combo = QComboBox()
        self._monitor_combo.setFixedHeight(28)
        self._monitor_combo.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed
        )
        monitor_row.addWidget(self._monitor_combo, 1)
        layout.addLayout(monitor_row)

        # Action buttons
        self._apply_btn = QPushButton("Set as Wallpaper")
        self._apply_btn.setFixedHeight(34)
        self._apply_btn.clicked.connect(self._on_apply)
        layout.addWidget(self._apply_btn)

        self._download_btn = QPushButton("Download to Library")
        self._download_btn.setFixedHeight(30)
        self._download_btn.hide()
        self._download_btn.clicked.connect(self._on_download)
        layout.addWidget(self._download_btn)

        self._open_btn = QPushButton("Open in File Manager")
        self._open_btn.setFixedHeight(30)
        self._open_btn.hide()
        self._open_btn.clicked.connect(self._on_open_folder)
        layout.addWidget(self._open_btn)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def show_wallpaper(self, info: WallpaperInfo) -> None:
        """Populate the panel with metadata from *info*.

        Args:
            info: The selected :class:`~mural.gui.wallpaper_card.WallpaperInfo`.
        """
        self._current_info = info

        # Thumbnail
        if info.thumbnail_path and Path(info.thumbnail_path).exists():
            px = QPixmap(info.thumbnail_path).scaled(
                self._thumb_label.width(),
                _THUMB_MAX_H,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            self._thumb_label.setPixmap(px)
        else:
            self._thumb_label.setPixmap(QPixmap())
            self._thumb_label.setText("No preview")

        # Metadata labels
        self._name_label.setText(info.name or "Untitled")
        _set_meta(self._type_label, "Type:", info.type.capitalize())
        _set_meta(self._author_label, "Author:", info.author or "—")
        _set_meta(self._res_label, "Resolution:", info.resolution or "—")
        _set_meta(self._size_label, "Size:", _fmt_size(info.file_size) if info.file_size else "—")
        _set_meta(self._tags_label, "Tags:", ", ".join(info.tags) if info.tags else "—")

        # Show/hide action buttons based on source
        is_platform = info.source == "platform"
        self._download_btn.setVisible(is_platform)
        self._open_btn.setVisible(not is_platform)
        self._apply_btn.setEnabled(True)

        self._refresh_monitor_list()

    def refresh_monitors(self) -> None:
        """Re-query the Core Service for the current monitor list."""
        self._refresh_monitor_list()

    def set_core_proxy(self, proxy: Any) -> None:
        """Update the Core Service proxy reference.

        Args:
            proxy: A live dasbus proxy for ``com.mural.Core``.
        """
        self._core = proxy
        self._refresh_monitor_list()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _show_empty_state(self) -> None:
        self._thumb_label.setPixmap(QPixmap())
        self._thumb_label.setText("Select a wallpaper")
        self._name_label.setText("")
        for lbl in (self._type_label, self._author_label,
                    self._res_label, self._size_label, self._tags_label):
            lbl.setText("")
        self._apply_btn.setEnabled(False)
        self._download_btn.hide()
        self._open_btn.hide()

    def _refresh_monitor_list(self) -> None:
        """Re-populate the monitor combo from the Core Service."""
        current = self._monitor_combo.currentText()
        self._monitor_combo.clear()

        monitors: list[str] = []
        if self._core:
            try:
                monitors = list(self._core.GetMonitors())
            except Exception:
                pass

        if not monitors:
            self._monitor_combo.addItem("(no monitors detected)")
            return

        for m in monitors:
            self._monitor_combo.addItem(m)

        # Restore previous selection if still available.
        idx = self._monitor_combo.findText(current)
        if idx >= 0:
            self._monitor_combo.setCurrentIndex(idx)

    def _on_apply(self) -> None:
        """Apply the current wallpaper to the selected monitor via D-Bus."""
        if not self._current_info:
            return

        monitor = self._monitor_combo.currentText()
        if not monitor or monitor.startswith("("):
            QMessageBox.warning(self, "No Monitor", "No monitor selected.")
            return

        if self._current_info.source == "platform" and not Path(self._current_info.path).exists():
            QMessageBox.information(
                self,
                "Download First",
                "Download this wallpaper to your library before applying it.",
            )
            return

        if not self._core:
            QMessageBox.warning(
                self,
                "Service Unavailable",
                "The Mural Core Service is not running.\n"
                "Start it with: systemctl --user start mural-core.service",
            )
            return

        try:
            ok = self._core.SetWallpaper(monitor, self._current_info.path)
            if not ok:
                QMessageBox.warning(self, "Apply Failed",
                                    "The Core Service could not apply the wallpaper.\n"
                                    "Check that linux-wallpaperengine is installed.")
        except Exception as exc:
            QMessageBox.critical(self, "D-Bus Error", str(exc))

    def _on_download(self) -> None:
        """Delegate download to the platform tab."""
        if self._current_info:
            self._platform_tab.download_selected(self._current_info)

    def _on_open_folder(self) -> None:
        """Open the wallpaper's parent directory in the system file manager."""
        if not self._current_info:
            return
        path = Path(self._current_info.path)
        folder = path.parent if path.is_file() else path
        try:
            subprocess.Popen(["xdg-open", str(folder)])
        except FileNotFoundError:
            QMessageBox.information(self, "Not Available",
                                    "xdg-open not found — open the folder manually.")


# ---------------------------------------------------------------------------
# Helper functions for metadata rows
# ---------------------------------------------------------------------------

def _meta_row(key: str, value: str) -> QLabel:
    """Return a styled metadata label showing ``key value``."""
    lbl = QLabel()
    lbl.setWordWrap(True)
    lbl.setStyleSheet("font-size: 12px; color: #ccc;")
    _set_meta(lbl, key, value)
    return lbl


def _set_meta(label: QLabel, key: str, value: str) -> None:
    if value:
        label.setText(f"<b>{key}</b> {value}")
        label.show()
    else:
        label.hide()


def _fmt_size(size_bytes: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if size_bytes < 1024:
            return f"{size_bytes:.0f} {unit}"
        size_bytes //= 1024
    return f"{size_bytes:.0f} GB"


# ---------------------------------------------------------------------------
# Main window
# ---------------------------------------------------------------------------

class MainWindow(QMainWindow):
    """The Mural main application window.

    Owns the tab bar, left content stack, and right preview panel.
    Wires signals between tabs and the Core Service proxy.

    Args:
        core_proxy: Live dasbus proxy for ``com.mural.Core``, or ``None``
            when the Core Service is unavailable.
        parent: Optional Qt parent widget.
    """

    def __init__(
        self,
        core_proxy: Any | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._core = core_proxy

        self.setWindowTitle("Mural")
        self.setMinimumSize(_WIN_MIN_W, _WIN_MIN_H)
        self.resize(1200, 700)

        self._build_ui()
        self._build_menu()
        self._build_status_bar()
        self._connect_signals()

        # Poll service status every 10 seconds.
        self._status_timer = QTimer(self)
        self._status_timer.timeout.connect(self._update_status_bar)
        self._status_timer.start(10_000)
        self._update_status_bar()

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # Custom tab bar
        root.addWidget(self._build_tab_bar())

        # Main splitter: left content | right preview
        self._splitter = QSplitter(Qt.Orientation.Horizontal)
        self._splitter.setHandleWidth(1)
        self._splitter.setChildrenCollapsible(False)
        root.addWidget(self._splitter, 1)

        # Left: stacked widget (Library | Platform | Settings)
        self._stack = QStackedWidget()
        self._splitter.addWidget(self._stack)

        # Build the three tab contents
        self._library_tab = LibraryTab()
        self._platform_tab = PlatformTab()
        self._settings_tab = SettingsTab(core_proxy=self._core)

        self._stack.addWidget(self._library_tab)   # index 0
        self._stack.addWidget(self._platform_tab)  # index 1
        self._stack.addWidget(self._settings_tab)  # index 2

        # Right: preview panel
        self._preview = _PreviewPanel(
            core_proxy=self._core,
            platform_tab=self._platform_tab,
        )
        preview_frame = QFrame()
        preview_frame.setFrameShape(QFrame.Shape.NoFrame)
        preview_frame.setStyleSheet("background: #141414; border-left: 1px solid #2a2a2a;")
        pf_layout = QVBoxLayout(preview_frame)
        pf_layout.setContentsMargins(0, 0, 0, 0)
        pf_layout.addWidget(self._preview)
        self._splitter.addWidget(preview_frame)

        # Splitter proportions: ~70 / 30
        total = _WIN_MIN_W
        self._splitter.setSizes([int(total * 0.68), int(total * 0.32)])

    def _build_tab_bar(self) -> QWidget:
        bar_widget = QWidget()
        bar_widget.setFixedHeight(42)
        bar_widget.setStyleSheet(
            "background: #1A1A2E; border-bottom: 1px solid #2a2a2a;"
        )
        layout = QHBoxLayout(bar_widget)
        layout.setContentsMargins(8, 0, 8, 0)
        layout.setSpacing(0)

        btn_style = (
            "QPushButton {"
            "  background: transparent; color: #aaa;"
            "  border: none; padding: 0 18px; font-size: 13px; height: 42px;"
            "}"
            "QPushButton:checked {"
            "  color: #fff; border-bottom: 2px solid #2979FF;"
            "}"
            "QPushButton:hover:!checked { color: #ddd; }"
        )

        self._tab_btns: list[QPushButton] = []
        for i, label in enumerate(("Library", "Platform", "Settings")):
            btn = QPushButton(label)
            btn.setCheckable(True)
            btn.setChecked(i == 0)
            btn.setStyleSheet(btn_style)
            btn.clicked.connect(lambda _, idx=i: self._switch_tab(idx))
            self._tab_btns.append(btn)
            layout.addWidget(btn)

        layout.addStretch()

        # Service indicator dot
        self._service_dot = QLabel("●")
        self._service_dot.setToolTip("Core Service status")
        self._service_dot.setStyleSheet("color: #FF5252; font-size: 16px; padding-right: 8px;")
        layout.addWidget(self._service_dot)

        return bar_widget

    def _build_menu(self) -> None:
        menubar = self.menuBar()

        file_menu = menubar.addMenu("&File")
        refresh_action = QAction("&Refresh Library", self)
        refresh_action.setShortcut(QKeySequence("Ctrl+R"))
        refresh_action.triggered.connect(self._library_tab.refresh)
        file_menu.addAction(refresh_action)
        file_menu.addSeparator()
        quit_action = QAction("&Quit", self)
        quit_action.setShortcut(QKeySequence("Ctrl+Q"))
        quit_action.triggered.connect(self.close)
        file_menu.addAction(quit_action)

        view_menu = menubar.addMenu("&View")
        lib_action = QAction("&Library", self)
        lib_action.setShortcut(QKeySequence("Ctrl+1"))
        lib_action.triggered.connect(lambda: self._switch_tab(0))
        plat_action = QAction("&Platform", self)
        plat_action.setShortcut(QKeySequence("Ctrl+2"))
        plat_action.triggered.connect(lambda: self._switch_tab(1))
        sett_action = QAction("&Settings", self)
        sett_action.setShortcut(QKeySequence("Ctrl+3"))
        sett_action.triggered.connect(lambda: self._switch_tab(2))
        view_menu.addActions([lib_action, plat_action, sett_action])

        service_menu = menubar.addMenu("&Service")
        start_action = QAction("Start Core Service", self)
        start_action.triggered.connect(self._start_service)
        restart_action = QAction("Restart Core Service", self)
        restart_action.triggered.connect(self._restart_service)
        stop_action = QAction("Stop Core Service", self)
        stop_action.triggered.connect(self._stop_service)
        service_menu.addActions([start_action, restart_action, stop_action])

    def _build_status_bar(self) -> None:
        bar = QStatusBar()
        self.setStatusBar(bar)
        self._status_bar_label = QLabel("Ready")
        bar.addWidget(self._status_bar_label)

    # ------------------------------------------------------------------
    # Signal wiring
    # ------------------------------------------------------------------

    def _connect_signals(self) -> None:
        # Both content tabs feed the same preview panel.
        self._library_tab.wallpaper_selected.connect(self._preview.show_wallpaper)
        self._library_tab.wallpaper_apply_requested.connect(self._on_quick_apply)
        self._platform_tab.wallpaper_selected.connect(self._preview.show_wallpaper)
        self._platform_tab.wallpaper_apply_requested.connect(self._on_quick_apply)

        # Downloaded platform wallpaper → refresh local library.
        self._platform_tab.wallpaper_downloaded.connect(self._on_download_complete)

        # Settings saved → propagate to service and preview panel.
        self._settings_tab.settings_saved.connect(self._on_settings_saved)

    # ------------------------------------------------------------------
    # Tab switching
    # ------------------------------------------------------------------

    def _switch_tab(self, index: int) -> None:
        """Switch the left stack to *index* and update tab button states."""
        self._stack.setCurrentIndex(index)
        for i, btn in enumerate(self._tab_btns):
            btn.setChecked(i == index)

        # Hide preview panel when Settings is active (index 2).
        show_preview = index != 2
        self._splitter.widget(1).setVisible(show_preview)

    # ------------------------------------------------------------------
    # Service menu actions
    # ------------------------------------------------------------------

    def _start_service(self) -> None:
        subprocess.Popen(
            ["systemctl", "--user", "start", "mural-core.service"],
            start_new_session=True,
        )
        QTimer.singleShot(2000, self._update_status_bar)

    def _restart_service(self) -> None:
        subprocess.Popen(
            ["systemctl", "--user", "restart", "mural-core.service"],
            start_new_session=True,
        )
        QTimer.singleShot(2000, self._update_status_bar)

    def _stop_service(self) -> None:
        subprocess.Popen(
            ["systemctl", "--user", "stop", "mural-core.service"],
            start_new_session=True,
        )
        QTimer.singleShot(1000, self._update_status_bar)

    # ------------------------------------------------------------------
    # Event handlers
    # ------------------------------------------------------------------

    def _on_quick_apply(self, info: WallpaperInfo) -> None:
        """Apply *info* immediately to the primary monitor on double-click."""
        if not self._core:
            return
        try:
            monitors: list[str] = list(self._core.GetMonitors())
        except Exception:
            return
        if monitors:
            try:
                self._core.SetWallpaper(monitors[0], info.path)
                self.statusBar().showMessage(
                    f"Applied '{info.name}' to {monitors[0]}", 4000
                )
            except Exception as exc:
                self.statusBar().showMessage(f"Apply failed: {exc}", 5000)

    def _on_download_complete(self, local_path: str) -> None:
        """Refresh the library and show a status message after a download."""
        self._library_tab.refresh()
        name = Path(local_path).stem
        self.statusBar().showMessage(f"Downloaded '{name}' to library.", 5000)

    def _on_settings_saved(self, settings: dict) -> None:
        """Propagate relevant settings changes to the Core Service."""
        self._preview.refresh_monitors()
        self._update_status_bar()

    def _update_status_bar(self) -> None:
        """Poll the Core Service for status and update the indicator dot."""
        running = False
        if self._core:
            try:
                status = self._core.GetStatus()
                running = bool(status.get("running", False))
            except Exception:
                pass

        colour = "#00C853" if running else "#FF5252"
        self._service_dot.setStyleSheet(
            f"color: {colour}; font-size: 16px; padding-right: 8px;"
        )
        self._service_dot.setToolTip(
            "Core Service: running" if running else "Core Service: stopped"
        )

    # ------------------------------------------------------------------
    # Window close
    # ------------------------------------------------------------------

    def closeEvent(self, event) -> None:  # type: ignore[override]
        """Hide to tray instead of quitting when a tray icon is present."""
        from PySide6.QtWidgets import QApplication  # noqa: PLC0415
        app = QApplication.instance()
        if app and hasattr(app, "tray") and app.tray.isVisible():  # type: ignore[attr-defined]
            self.hide()
            event.ignore()
        else:
            event.accept()
