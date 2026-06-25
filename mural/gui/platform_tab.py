# mural/gui/platform_tab.py
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

"""Mural content platform browser tab.

Fetches wallpaper listings from the Mural REST API, displays them in a
card grid with lazy-loaded thumbnails, and supports download to the local
library.

When the platform API is unreachable an offline state is shown with a
retry button — the tab never crashes or blocks the UI.

Network operations (API calls, thumbnail fetches, wallpaper downloads)
all run on background QThreads and communicate results back to the main
thread via Qt signals.
"""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path
from typing import Any, ClassVar

import requests
from PySide6.QtCore import (
    QByteArray,
    QRunnable,
    QSize,
    Qt,
    QThread,
    QThreadPool,
    Signal,
    Slot,
)
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from mural.gui.wallpaper_card import WallpaperCard, WallpaperInfo

# Default platform API base URL; overridden by config in Phase 4.
_DEFAULT_API_BASE = "https://api.mural.app/v1"
_PAGE_SIZE = 24
_THUMB_WORKERS = 4          # concurrent thumbnail fetch threads
_DOWNLOAD_DIR = Path("~/.local/share/mural/downloads").expanduser()


# ---------------------------------------------------------------------------
# Thin API client (wired to real endpoints in Phase 4)
# ---------------------------------------------------------------------------

class _PlatformClient:
    """Minimal REST client for the Mural content platform.

    All methods return empty results (rather than raising) when the API
    is unreachable, so callers never have to handle exceptions.

    Args:
        base_url: Base URL of the Mural API.
        timeout: Request timeout in seconds.
    """

    def __init__(self, base_url: str = _DEFAULT_API_BASE, timeout: float = 10.0) -> None:
        self._base = base_url.rstrip("/")
        self._timeout = timeout
        self._session = requests.Session()
        self._session.headers["User-Agent"] = "Mural/0.1 (Linux)"

    def fetch_wallpapers(
        self,
        *,
        page: int = 1,
        limit: int = _PAGE_SIZE,
        sort: str = "newest",
        type_filter: str = "all",
        query: str = "",
    ) -> tuple[list[dict[str, Any]], int]:
        """Return (items, total_count) from the wallpaper listing endpoint.

        Returns:
            A tuple of (list of wallpaper dicts, total count).  Both are
            empty / zero on network failure.
        """
        params: dict[str, Any] = {"page": page, "limit": limit, "sort": sort}
        if type_filter != "all":
            params["type"] = type_filter
        endpoint = f"{self._base}/search" if query else f"{self._base}/wallpapers"
        if query:
            params["q"] = query
        try:
            resp = self._session.get(endpoint, params=params, timeout=self._timeout)
            resp.raise_for_status()
            data = resp.json()
            items: list[dict] = data.get("items") or data.get("results") or []
            total: int = data.get("total") or len(items)
            return items, total
        except Exception:
            return [], 0

    def fetch_thumbnail_bytes(self, url: str) -> bytes | None:
        """Download thumbnail image bytes, or ``None`` on failure."""
        try:
            resp = self._session.get(url, timeout=self._timeout)
            resp.raise_for_status()
            return resp.content
        except Exception:
            return None

    def download_wallpaper(
        self, download_url: str, dest_path: Path, progress_cb=None
    ) -> bool:
        """Stream a wallpaper file to *dest_path*.

        Args:
            download_url: Direct download URL.
            dest_path: Where to write the file.
            progress_cb: Optional callable(bytes_done, total_bytes).

        Returns:
            ``True`` on success.
        """
        try:
            dest_path.parent.mkdir(parents=True, exist_ok=True)
            with self._session.get(download_url, stream=True, timeout=60) as resp:
                resp.raise_for_status()
                total = int(resp.headers.get("Content-Length", 0))
                done = 0
                with open(dest_path, "wb") as fh:
                    for chunk in resp.iter_content(chunk_size=65536):
                        if chunk:
                            fh.write(chunk)
                            done += len(chunk)
                            if progress_cb:
                                progress_cb(done, total)
            return True
        except Exception:
            return False

    def is_reachable(self) -> bool:
        """Return ``True`` if the platform API responds to a health check."""
        try:
            resp = self._session.get(f"{self._base}/wallpapers", timeout=5, params={"limit": 1})
            return resp.ok
        except Exception:
            return False


def _dict_to_wallpaper_info(data: dict[str, Any]) -> WallpaperInfo:
    """Convert a platform API wallpaper dict to a :class:`WallpaperInfo`."""
    return WallpaperInfo(
        name=data.get("title") or data.get("name") or "Untitled",
        path=data.get("download_url") or data.get("id") or "",
        type=(data.get("type") or "video").lower(),
        thumbnail_path=None,  # loaded lazily
        resolution=data.get("resolution") or "",
        author=data.get("author_name") or "",
        file_size=data.get("file_size_bytes") or 0,
        tags=data.get("tags") or [],
        source="platform",
    )


# ---------------------------------------------------------------------------
# Background workers
# ---------------------------------------------------------------------------

class _FetchListingsWorker(QThread):
    """Fetches a page of wallpaper listings from the platform API.

    Args:
        client: Shared :class:`_PlatformClient` instance.
        page: Page number (1-based).
        sort: Sort key — ``"newest"``, ``"popular"``, or ``"trending"``.
        type_filter: Type filter — ``"all"``, ``"video"``, etc.
        query: Free-text search query.
    """

    results_ready: ClassVar[Signal] = Signal(list, int)   # items, total
    fetch_failed: ClassVar[Signal] = Signal(str)           # error message

    def __init__(
        self,
        client: _PlatformClient,
        page: int = 1,
        sort: str = "newest",
        type_filter: str = "all",
        query: str = "",
    ) -> None:
        super().__init__()
        self._client = client
        self._page = page
        self._sort = sort
        self._type_filter = type_filter
        self._query = query

    def run(self) -> None:
        items, total = self._client.fetch_wallpapers(
            page=self._page,
            sort=self._sort,
            type_filter=self._type_filter,
            query=self._query,
        )
        if items is not None:
            self.results_ready.emit(items, total)
        else:
            self.fetch_failed.emit("Could not reach the Mural platform API.")


class _ThumbnailRunnable(QRunnable):
    """Fetches a single thumbnail image in the thread pool.

    Delivers the result by calling a slot on the target card.

    Args:
        card: The :class:`WallpaperCard` to update.
        url: Thumbnail URL to fetch.
        client: Shared HTTP session.
    """

    def __init__(self, card: WallpaperCard, url: str, client: _PlatformClient) -> None:
        super().__init__()
        self._card = card
        self._url = url
        self._client = client
        self.setAutoDelete(True)

    def run(self) -> None:
        data = self._client.fetch_thumbnail_bytes(self._url)
        if data:
            pixmap = QPixmap()
            pixmap.loadFromData(QByteArray(data))
            if not pixmap.isNull():
                # Qt objects must be set from the main thread; use a
                # queued connection by invoking via the card's method.
                # PySide6 automatically queues cross-thread signal delivery.
                self._card.set_thumbnail(pixmap)


class _DownloadWorker(QThread):
    """Downloads a wallpaper file to the local library directory.

    Args:
        client: Shared HTTP client.
        info: Wallpaper metadata; ``info.path`` is the download URL.
        dest_dir: Directory to save the downloaded file.
    """

    progress: ClassVar[Signal] = Signal(int, int)      # bytes_done, total
    finished: ClassVar[Signal] = Signal(bool, str)     # success, dest_path

    def __init__(
        self,
        client: _PlatformClient,
        info: WallpaperInfo,
        dest_dir: Path = _DOWNLOAD_DIR,
    ) -> None:
        super().__init__()
        self._client = client
        self._info = info
        self._dest_dir = dest_dir

    def run(self) -> None:
        safe_name = "".join(
            c if c.isalnum() or c in "._- " else "_" for c in self._info.name
        ).strip() or "wallpaper"
        ext = Path(self._info.path).suffix or ".mp4"
        dest = self._dest_dir / (safe_name + ext)

        ok = self._client.download_wallpaper(
            self._info.path,
            dest,
            progress_cb=lambda done, total: self.progress.emit(done, total),
        )
        self.finished.emit(ok, str(dest) if ok else "")


# ---------------------------------------------------------------------------
# Platform tab
# ---------------------------------------------------------------------------

class PlatformTab(QWidget):
    """The Mural content platform browser tab.

    Args:
        parent: Optional Qt parent widget.
    """

    wallpaper_selected: ClassVar[Signal] = Signal(WallpaperInfo)
    wallpaper_apply_requested: ClassVar[Signal] = Signal(WallpaperInfo)
    wallpaper_downloaded: ClassVar[Signal] = Signal(str)  # local path

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._client = _PlatformClient()
        self._thread_pool = QThreadPool()
        self._thread_pool.setMaxThreadCount(_THUMB_WORKERS)

        self._current_page = 1
        self._total_results = 0
        self._cards: list[WallpaperCard] = []
        self._raw_items: list[dict] = []   # thumbnails fetched lazily
        self._selected_card: WallpaperCard | None = None
        self._fetch_worker: _FetchListingsWorker | None = None
        self._download_worker: _DownloadWorker | None = None

        self._build_ui()
        self._load_page(reset=True)

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(6)

        root.addLayout(self._build_toolbar())

        # Stacked widget: offline notice | loading | results
        self._stack = QStackedWidget()
        root.addWidget(self._stack, 1)

        self._stack.addWidget(self._build_offline_page())   # index 0
        self._stack.addWidget(self._build_loading_page())   # index 1
        self._stack.addWidget(self._build_results_page())   # index 2

        # Bottom bar: status + load more + download progress
        root.addLayout(self._build_bottom_bar())

    def _build_toolbar(self) -> QHBoxLayout:
        toolbar = QHBoxLayout()
        toolbar.setSpacing(6)

        self._search = QLineEdit()
        self._search.setPlaceholderText("Search platform…")
        self._search.setClearButtonEnabled(True)
        self._search.setFixedHeight(30)
        self._search.returnPressed.connect(lambda: self._load_page(reset=True))
        toolbar.addWidget(self._search, 1)

        self._sort_combo = QComboBox()
        self._sort_combo.setFixedHeight(30)
        for label, key in (("Newest", "newest"), ("Popular", "popular"), ("Trending", "trending")):
            self._sort_combo.addItem(label, key)
        self._sort_combo.currentIndexChanged.connect(lambda _: self._load_page(reset=True))
        toolbar.addWidget(self._sort_combo)

        for label, key in (("All", "all"), ("Video", "video"), ("Scene", "scene"),
                           ("Web", "web"), ("Image", "image")):
            btn = QPushButton(label)
            btn.setCheckable(True)
            btn.setChecked(key == "all")
            btn.setFixedHeight(30)
            btn.clicked.connect(lambda checked, k=key: self._set_type_filter(k))
            setattr(self, f"_btn_{key}", btn)
            toolbar.addWidget(btn)

        refresh_btn = QPushButton("↻")
        refresh_btn.setFixedSize(30, 30)
        refresh_btn.setToolTip("Refresh")
        refresh_btn.clicked.connect(lambda: self._load_page(reset=True))
        toolbar.addWidget(refresh_btn)

        self._active_type = "all"
        return toolbar

    def _build_offline_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        icon = QLabel("🌐")
        icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        icon.setStyleSheet("font-size: 48px;")
        layout.addWidget(icon)

        msg = QLabel("Could not reach the Mural platform.\nCheck your internet connection.")
        msg.setAlignment(Qt.AlignmentFlag.AlignCenter)
        msg.setStyleSheet("color: #888; font-size: 13px;")
        layout.addWidget(msg)

        retry_btn = QPushButton("Retry")
        retry_btn.setFixedWidth(120)
        retry_btn.clicked.connect(lambda: self._load_page(reset=True))
        layout.addWidget(retry_btn, alignment=Qt.AlignmentFlag.AlignCenter)

        return page

    def _build_loading_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        spinner = QLabel("Loading…")
        spinner.setAlignment(Qt.AlignmentFlag.AlignCenter)
        spinner.setStyleSheet("color: #888; font-size: 14px;")
        layout.addWidget(spinner)

        bar = QProgressBar()
        bar.setMaximum(0)
        bar.setFixedWidth(200)
        layout.addWidget(bar, alignment=Qt.AlignmentFlag.AlignCenter)

        return page

    def _build_results_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)

        self._grid_widget = QWidget()
        self._grid_layout = QVBoxLayout(self._grid_widget)
        self._grid_layout.setContentsMargins(0, 0, 0, 0)
        self._grid_layout.setSpacing(0)

        scroll = QScrollArea()
        scroll.setWidget(self._grid_widget)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        layout.addWidget(scroll)

        return page

    def _build_bottom_bar(self) -> QHBoxLayout:
        bar = QHBoxLayout()

        self._status_label = QLabel()
        self._status_label.setStyleSheet("color: #888; font-size: 11px;")
        bar.addWidget(self._status_label)

        bar.addStretch()

        self._dl_progress = QProgressBar()
        self._dl_progress.setFixedWidth(160)
        self._dl_progress.setFixedHeight(14)
        self._dl_progress.setTextVisible(False)
        self._dl_progress.hide()
        bar.addWidget(self._dl_progress)

        self._dl_label = QLabel()
        self._dl_label.setStyleSheet("color: #888; font-size: 11px;")
        self._dl_label.hide()
        bar.addWidget(self._dl_label)

        self._load_more_btn = QPushButton("Load More")
        self._load_more_btn.setFixedHeight(28)
        self._load_more_btn.hide()
        self._load_more_btn.clicked.connect(self._load_next_page)
        bar.addWidget(self._load_more_btn)

        return bar

    # ------------------------------------------------------------------
    # Data loading
    # ------------------------------------------------------------------

    def _load_page(self, reset: bool = False) -> None:
        """Fetch a page of listings from the platform API."""
        if self._fetch_worker and self._fetch_worker.isRunning():
            return

        if reset:
            self._current_page = 1
            self._cards.clear()
            self._raw_items.clear()
            self._selected_card = None
            self._clear_grid()
            self._load_more_btn.hide()

        self._stack.setCurrentIndex(1)  # loading page

        sort = self._sort_combo.currentData() or "newest"
        query = self._search.text().strip()

        self._fetch_worker = _FetchListingsWorker(
            self._client,
            page=self._current_page,
            sort=sort,
            type_filter=self._active_type,
            query=query,
        )
        self._fetch_worker.results_ready.connect(self._on_results_ready)
        self._fetch_worker.fetch_failed.connect(self._on_fetch_failed)
        self._fetch_worker.start()

    def _load_next_page(self) -> None:
        self._current_page += 1
        self._load_page(reset=False)

    @Slot(list, int)
    def _on_results_ready(self, items: list[dict], total: int) -> None:
        self._total_results = total

        if not items and self._current_page == 1:
            self._stack.setCurrentIndex(0)  # offline / empty
            self._status_label.setText("No results.")
            return

        self._stack.setCurrentIndex(2)  # results page

        row_widget = QWidget()
        row_layout = QHBoxLayout(row_widget)
        row_layout.setContentsMargins(0, 4, 0, 4)
        row_layout.setSpacing(12)
        row_layout.setAlignment(Qt.AlignmentFlag.AlignLeft)

        for raw in items:
            info = _dict_to_wallpaper_info(raw)
            self._raw_items.append(raw)
            card = WallpaperCard(info)
            card.selected.connect(self._on_card_selected)
            card.apply_requested.connect(self.wallpaper_apply_requested)
            self._cards.append(card)
            row_layout.addWidget(card)

            thumb_url = raw.get("thumbnail_url") or ""
            if thumb_url:
                runnable = _ThumbnailRunnable(card, thumb_url, self._client)
                self._thread_pool.start(runnable)

            if row_layout.count() >= 4:
                self._grid_layout.addWidget(row_widget)
                row_widget = QWidget()
                row_layout = QHBoxLayout(row_widget)
                row_layout.setContentsMargins(0, 4, 0, 4)
                row_layout.setSpacing(12)
                row_layout.setAlignment(Qt.AlignmentFlag.AlignLeft)

        if row_layout.count():
            row_layout.addStretch()
            self._grid_layout.addWidget(row_widget)

        self._grid_layout.addStretch()

        shown = len(self._cards)
        self._status_label.setText(f"{shown} of {total} wallpapers")
        if shown < total:
            self._load_more_btn.show()
        else:
            self._load_more_btn.hide()

    @Slot(str)
    def _on_fetch_failed(self, message: str) -> None:
        self._stack.setCurrentIndex(0)  # offline page
        self._status_label.setText(message)

    # ------------------------------------------------------------------
    # Card interaction
    # ------------------------------------------------------------------

    def _on_card_selected(self, info: WallpaperInfo) -> None:
        for card in self._cards:
            card.set_selected(card.info is info)
        self.wallpaper_selected.emit(info)

    # ------------------------------------------------------------------
    # Download
    # ------------------------------------------------------------------

    def download_selected(self, info: WallpaperInfo) -> None:
        """Start downloading *info* to the local library.

        Called by the main window's "Download" button in the preview panel.

        Args:
            info: The wallpaper to download.
        """
        if self._download_worker and self._download_worker.isRunning():
            return

        self._dl_progress.setValue(0)
        self._dl_progress.setMaximum(100)
        self._dl_progress.show()
        self._dl_label.setText(f"Downloading {info.name[:24]}…")
        self._dl_label.show()

        self._download_worker = _DownloadWorker(self._client, info)
        self._download_worker.progress.connect(self._on_download_progress)
        self._download_worker.finished.connect(self._on_download_finished)
        self._download_worker.start()

    @Slot(int, int)
    def _on_download_progress(self, done: int, total: int) -> None:
        if total > 0:
            self._dl_progress.setMaximum(100)
            self._dl_progress.setValue(int(done / total * 100))
        else:
            self._dl_progress.setMaximum(0)

    @Slot(bool, str)
    def _on_download_finished(self, success: bool, dest_path: str) -> None:
        self._dl_progress.hide()
        self._dl_label.hide()
        if success:
            self.wallpaper_downloaded.emit(dest_path)
        else:
            self._status_label.setText("Download failed.")

    # ------------------------------------------------------------------
    # Filter helpers
    # ------------------------------------------------------------------

    def _set_type_filter(self, key: str) -> None:
        self._active_type = key
        for k in ("all", "video", "scene", "web", "image"):
            btn: QPushButton = getattr(self, f"_btn_{k}")
            btn.setChecked(k == key)
        self._load_page(reset=True)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _clear_grid(self) -> None:
        while self._grid_layout.count():
            item = self._grid_layout.takeAt(0)
            if item and item.widget():
                item.widget().deleteLater()
