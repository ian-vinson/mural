# platform/routes/search.py
#
# Mural Content Platform
# Copyright (C) 2024  Mural Contributors
# MIT License

"""GET /search — full-text wallpaper search."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy import or_, select, func
from sqlalchemy.ext.asyncio import AsyncSession

from platform.db import get_session
from platform.models import Wallpaper, Tag, wallpaper_tags
from platform.routes.wallpapers import WallpaperListOut, _to_out
from platform.storage import get_storage

router = APIRouter()


@router.get("", response_model=WallpaperListOut)
async def search_wallpapers(
    q: Annotated[str, Query(min_length=1, max_length=256)],
    page: Annotated[int, Query(ge=1)] = 1,
    limit: Annotated[int, Query(ge=1, le=100)] = 24,
    type: str | None = None,
    tags: str | None = None,
    db: AsyncSession = Depends(get_session),
) -> WallpaperListOut:
    """Search published wallpapers by title, description, and tags.

    - **q**: Free-text query matched against title and description.
    - **tags**: Comma-separated list of tag names to filter by.
    - **type**: Wallpaper type filter (``video``, ``scene``, ``web``, ``image``).
    """
    term = f"%{q.lower()}%"
    base = (
        select(Wallpaper)
        .where(Wallpaper.is_published == True)  # noqa: E712
        .where(
            or_(
                Wallpaper.title.ilike(term),
                Wallpaper.description.ilike(term),
            )
        )
    )

    if type:
        base = base.where(Wallpaper.type == type.lower())

    if tags:
        tag_names = [t.strip().lower() for t in tags.split(",") if t.strip()]
        if tag_names:
            base = base.join(
                wallpaper_tags, Wallpaper.id == wallpaper_tags.c.wallpaper_id
            ).join(Tag, Tag.id == wallpaper_tags.c.tag_id).where(Tag.name.in_(tag_names))

    count_q = select(func.count()).select_from(base.subquery())
    total: int = (await db.execute(count_q)).scalar_one()

    query = base.order_by(Wallpaper.downloads.desc()).offset((page - 1) * limit).limit(limit)
    rows = (await db.execute(query)).scalars().all()

    storage = get_storage()
    return WallpaperListOut(
        items=[_to_out(w, storage) for w in rows],
        total=total,
        page=page,
        limit=limit,
    )
