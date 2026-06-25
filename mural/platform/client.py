# mural/platform/client.py
#
# Mural — Animated Wallpaper Platform for Linux
# Copyright (C) 2024  Mural Contributors
# GPL v3 — see LICENSE

"""REST API client for the Mural content platform.

:class:`PlatformClient` wraps the Mural platform API with typed methods
and graceful error handling.  All network failures return empty results
rather than raising, keeping the GUI responsive on slow or absent
connections.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Callable

import requests

from mural.platform.models import PlatformPage, PlatformWallpaper

logger = logging.getLogger(__name__)

_DEFAULT_BASE = "https://api.mural.app/v1"
_DEFAULT_TIMEOUT = 10.0
_STREAM_CHUNK = 65536


class PlatformClient:
    """HTTP client for the Mural content platform REST API.

    Args:
        base_url: Base URL of the API.  Override for self-hosted instances.
        timeout: Default request timeout in seconds.
        api_key: Optional bearer token for authenticated endpoints.
    """

    def __init__(
        self,
        base_url: str = _DEFAULT_BASE,
        timeout: float = _DEFAULT_TIMEOUT,
        api_key: str = "",
    ) -> None:
        self._base = base_url.rstrip("/")
        self._timeout = timeout
        self._session = requests.Session()
        self._session.headers["User-Agent"] = "Mural/0.1 (Linux; Python)"
        if api_key:
            self._session.headers["Authorization"] = f"Bearer {api_key}"

    # ------------------------------------------------------------------
    # Browse / search
    # ------------------------------------------------------------------

    def list_wallpapers(
        self,
        *,
        page: int = 1,
        limit: int = 24,
        sort: str = "newest",
        type_filter: str = "all",
    ) -> PlatformPage:
        """Fetch a paginated list of wallpapers.

        Args:
            page: Page number (1-based).
            limit: Results per page.
            sort: Sort key — ``"newest"``, ``"popular"``, ``"trending"``.
            type_filter: Filter by type — ``"all"``, ``"video"``, etc.

        Returns:
            A :class:`~mural.platform.models.PlatformPage`.
        """
        params: dict[str, Any] = {"page": page, "limit": limit, "sort": sort}
        if type_filter != "all":
            params["type"] = type_filter
        return self._get_page(f"{self._base}/wallpapers", params)

    def search(
        self,
        query: str,
        *,
        page: int = 1,
        limit: int = 24,
        type_filter: str = "all",
        tags: list[str] | None = None,
    ) -> PlatformPage:
        """Full-text search across wallpaper titles, descriptions, and tags.

        Args:
            query: Search string.
            page: Page number.
            limit: Results per page.
            type_filter: Optional type filter.
            tags: Optional list of tags to filter by.

        Returns:
            A :class:`~mural.platform.models.PlatformPage`.
        """
        params: dict[str, Any] = {"q": query, "page": page, "limit": limit}
        if type_filter != "all":
            params["type"] = type_filter
        if tags:
            params["tags"] = ",".join(tags)
        return self._get_page(f"{self._base}/search", params)

    def get_wallpaper(self, wallpaper_id: str) -> PlatformWallpaper | None:
        """Fetch metadata for a single wallpaper by ID.

        Args:
            wallpaper_id: UUID string.

        Returns:
            A :class:`~mural.platform.models.PlatformWallpaper`, or ``None``.
        """
        try:
            resp = self._session.get(
                f"{self._base}/wallpapers/{wallpaper_id}", timeout=self._timeout
            )
            resp.raise_for_status()
            return PlatformWallpaper.from_dict(resp.json())
        except Exception as exc:
            logger.warning("get_wallpaper(%r) failed: %s", wallpaper_id, exc)
            return None

    # ------------------------------------------------------------------
    # Download
    # ------------------------------------------------------------------

    def download(
        self,
        wallpaper: PlatformWallpaper,
        dest_path: Path,
        progress_cb: Callable[[int, int], None] | None = None,
    ) -> bool:
        """Download a wallpaper file to *dest_path*.

        Args:
            wallpaper: The wallpaper to download.
            dest_path: Target file path.
            progress_cb: Optional ``(bytes_done, total_bytes)`` callback.

        Returns:
            ``True`` on success.
        """
        url = wallpaper.download_url
        if not url:
            logger.error("download: no download_url for wallpaper %r", wallpaper.id)
            return False

        dest_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with self._session.get(url, stream=True, timeout=60) as resp:
                resp.raise_for_status()
                total = int(resp.headers.get("Content-Length", 0))
                done = 0
                with open(dest_path, "wb") as fh:
                    for chunk in resp.iter_content(chunk_size=_STREAM_CHUNK):
                        if chunk:
                            fh.write(chunk)
                            done += len(chunk)
                            if progress_cb:
                                progress_cb(done, total)
            logger.info("Downloaded %r to %s", wallpaper.title, dest_path)
            return True
        except Exception as exc:
            logger.error("download(%r) failed: %s", wallpaper.id, exc)
            if dest_path.exists():
                dest_path.unlink(missing_ok=True)
            return False

    def fetch_thumbnail(self, url: str) -> bytes | None:
        """Download raw thumbnail image bytes.

        Args:
            url: Thumbnail URL.

        Returns:
            Image bytes, or ``None`` on failure.
        """
        try:
            resp = self._session.get(url, timeout=self._timeout)
            resp.raise_for_status()
            return resp.content
        except Exception as exc:
            logger.debug("fetch_thumbnail(%r) failed: %s", url[:60], exc)
            return None

    # ------------------------------------------------------------------
    # Health
    # ------------------------------------------------------------------

    def is_reachable(self) -> bool:
        """Return ``True`` if the platform API responds to a probe request."""
        try:
            resp = self._session.get(
                f"{self._base}/wallpapers",
                params={"limit": 1},
                timeout=5,
            )
            return resp.ok
        except Exception:
            return False

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _get_page(self, url: str, params: dict[str, Any]) -> PlatformPage:
        """GET *url* with *params* and deserialise the paginated response."""
        try:
            resp = self._session.get(url, params=params, timeout=self._timeout)
            resp.raise_for_status()
            data = resp.json()
            raw_items: list[dict] = data.get("items") or data.get("results") or []
            items = [PlatformWallpaper.from_dict(r) for r in raw_items]
            return PlatformPage(
                items=items,
                total=data.get("total") or len(items),
                page=params.get("page", 1),
                limit=params.get("limit", 24),
            )
        except Exception as exc:
            logger.warning("API request to %s failed: %s", url, exc)
            return PlatformPage()
