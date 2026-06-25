# platform/routes/wallpapers.py
#
# Mural Content Platform
# Copyright (C) 2024  Mural Contributors
# MIT License

"""GET /wallpapers and GET /wallpapers/{id} endpoints."""

from __future__ import annotations

import uuid
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from platform.db import get_session
from platform.models import Wallpaper, Tag
from platform.storage import get_storage

router = APIRouter()


# ---------------------------------------------------------------------------
# Response schemas
# ---------------------------------------------------------------------------

class WallpaperOut(BaseModel):
    """Public wallpaper representation returned by the API."""

    id: str
    title: str
    description: str
    author_id: str
    author_name: str
    type: str
    tags: list[str]
    resolution: str
    file_size_bytes: int
    thumbnail_url: str
    download_url: str
    downloads: int
    created_at: str
    updated_at: str

    model_config = {"from_attributes": True}


class WallpaperListOut(BaseModel):
    items: list[WallpaperOut]
    total: int
    page: int
    limit: int


# ---------------------------------------------------------------------------
# Route handlers
# ---------------------------------------------------------------------------

@router.get("", response_model=WallpaperListOut)
async def list_wallpapers(
    page: Annotated[int, Query(ge=1)] = 1,
    limit: Annotated[int, Query(ge=1, le=100)] = 24,
    sort: Literal["newest", "popular", "trending"] = "newest",
    type: str | None = None,
    db: AsyncSession = Depends(get_session),
) -> WallpaperListOut:
    """List published wallpapers with optional type filter and sort.

    - **newest**:   ordered by ``created_at`` descending
    - **popular**:  ordered by ``downloads`` descending
    - **trending**: ordered by recent downloads (proxied by downloads for v1)
    """
    query = select(Wallpaper).where(Wallpaper.is_published == True)  # noqa: E712
    if type:
        query = query.where(Wallpaper.type == type.lower())

    count_q = select(func.count()).select_from(query.subquery())
    total: int = (await db.execute(count_q)).scalar_one()

    order_col = {
        "newest":   Wallpaper.created_at.desc(),
        "popular":  Wallpaper.downloads.desc(),
        "trending": Wallpaper.downloads.desc(),
    }[sort]
    query = query.order_by(order_col).offset((page - 1) * limit).limit(limit)
    rows = (await db.execute(query)).scalars().all()

    storage = get_storage()
    items = [_to_out(w, storage) for w in rows]
    return WallpaperListOut(items=items, total=total, page=page, limit=limit)


@router.get("/{wallpaper_id}", response_model=WallpaperOut)
async def get_wallpaper(
    wallpaper_id: str,
    db: AsyncSession = Depends(get_session),
) -> WallpaperOut:
    """Fetch a single wallpaper by UUID."""
    try:
        wid = uuid.UUID(wallpaper_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid wallpaper ID")

    row = await db.get(Wallpaper, wid)
    if not row or not row.is_published:
        raise HTTPException(status_code=404, detail="Wallpaper not found")

    # Increment download counter when the record is fetched.
    row.downloads += 1
    await db.commit()

    return _to_out(row, get_storage())


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _to_out(w: Wallpaper, storage) -> WallpaperOut:
    return WallpaperOut(
        id=str(w.id),
        title=w.title,
        description=w.description,
        author_id=str(w.author_id),
        author_name=w.author.username if w.author else "",
        type=w.type,
        tags=[t.name for t in (w.tags or [])],
        resolution=w.resolution,
        file_size_bytes=w.file_size_bytes,
        thumbnail_url=storage.public_url(w.thumbnail_key) if w.thumbnail_key else "",
        download_url=storage.presigned_url(w.storage_key),
        downloads=w.downloads,
        created_at=w.created_at.isoformat() if w.created_at else "",
        updated_at=w.updated_at.isoformat() if w.updated_at else "",
    )
