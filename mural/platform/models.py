# mural/platform/models.py
#
# Mural — Animated Wallpaper Platform for Linux
# Copyright (C) 2024  Mural Contributors
# GPL v3 — see LICENSE

"""Client-side data models for the Mural content platform.

These dataclasses mirror the JSON schema described in DEVGUIDE — PHASE 4
and are used by :class:`~mural.platform.client.PlatformClient` to
deserialise API responses.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass
class PlatformWallpaper:
    """A single wallpaper entry from the Mural platform API.

    Attributes:
        id: UUID string.
        title: Display name.
        description: Long-form description (may be empty).
        author_id: Creator's account UUID.
        author_name: Creator's display name.
        type: One of ``"video"``, ``"scene"``, ``"web"``, ``"image"``.
        tags: List of tag strings.
        resolution: Resolution string, e.g. ``"3440x1440"``.
        file_size_bytes: File size in bytes.
        thumbnail_url: URL to the preview image.
        download_url: Direct download URL (may be a signed S3/R2 URL).
        downloads: Total download count.
        created_at: ISO 8601 creation timestamp.
        updated_at: ISO 8601 last-update timestamp.
    """

    id: str
    title: str
    description: str = ""
    author_id: str = ""
    author_name: str = ""
    type: str = "video"
    tags: list[str] = field(default_factory=list)
    resolution: str = ""
    file_size_bytes: int = 0
    thumbnail_url: str = ""
    download_url: str = ""
    downloads: int = 0
    created_at: str = ""
    updated_at: str = ""

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "PlatformWallpaper":
        """Deserialise from an API response dict.

        Unknown keys are silently ignored so the model stays forward-compatible.

        Args:
            data: Raw dict from the platform API.

        Returns:
            A populated :class:`PlatformWallpaper`.
        """
        return cls(
            id=data.get("id") or "",
            title=data.get("title") or data.get("name") or "Untitled",
            description=data.get("description") or "",
            author_id=data.get("author_id") or "",
            author_name=data.get("author_name") or "",
            type=(data.get("type") or "video").lower(),
            tags=data.get("tags") or [],
            resolution=data.get("resolution") or "",
            file_size_bytes=data.get("file_size_bytes") or 0,
            thumbnail_url=data.get("thumbnail_url") or "",
            download_url=data.get("download_url") or "",
            downloads=data.get("downloads") or 0,
            created_at=data.get("created_at") or "",
            updated_at=data.get("updated_at") or "",
        )

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a plain dict (for local caching)."""
        return {
            "id": self.id,
            "title": self.title,
            "description": self.description,
            "author_id": self.author_id,
            "author_name": self.author_name,
            "type": self.type,
            "tags": self.tags,
            "resolution": self.resolution,
            "file_size_bytes": self.file_size_bytes,
            "thumbnail_url": self.thumbnail_url,
            "download_url": self.download_url,
            "downloads": self.downloads,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


@dataclass
class PlatformPage:
    """A paginated list of wallpapers from the platform API.

    Attributes:
        items: Wallpapers on this page.
        total: Total number of matching wallpapers across all pages.
        page: Current page number (1-based).
        limit: Page size requested.
    """

    items: list[PlatformWallpaper] = field(default_factory=list)
    total: int = 0
    page: int = 1
    limit: int = 24

    @property
    def has_more(self) -> bool:
        """Return ``True`` if there are more pages to fetch."""
        return self.page * self.limit < self.total
