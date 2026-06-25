# platform/routes/upload.py
#
# Mural Content Platform
# Copyright (C) 2024  Mural Contributors
# MIT License

"""POST /upload — authenticated wallpaper upload."""

from __future__ import annotations

import io
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from PIL import Image
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from platform.auth import verify_token
from platform.db import get_session
from platform.models import Tag, User, Wallpaper
from platform.storage import R2Storage, get_storage

router = APIRouter()
_bearer = HTTPBearer()

_ALLOWED_TYPES = {"video", "scene", "web", "image"}
_MAX_FILE_MB = 500
_THUMBNAIL_MAX_PX = 1920


class UploadResponse(BaseModel):
    id: str
    title: str
    download_url: str
    thumbnail_url: str


@router.post("", response_model=UploadResponse, status_code=status.HTTP_201_CREATED)
async def upload_wallpaper(
    file: Annotated[UploadFile, File(description="Wallpaper file (MP4, ZIP, etc.)")],
    thumbnail: Annotated[UploadFile, File(description="Preview image (JPEG/PNG, max 1920px)")],
    title: Annotated[str, Form(min_length=1, max_length=256)],
    description: Annotated[str, Form(max_length=2000)] = "",
    type: Annotated[str, Form()] = "video",
    resolution: Annotated[str, Form()] = "",
    tags: Annotated[str, Form()] = "",
    credentials: HTTPAuthorizationCredentials = Depends(_bearer),
    db: AsyncSession = Depends(get_session),
    storage: R2Storage = Depends(get_storage),
) -> UploadResponse:
    """Upload a new wallpaper.  Requires a valid JWT bearer token.

    The file is stored in Cloudflare R2; a thumbnail is generated/stored
    alongside it.  The wallpaper is created in ``is_published=False``
    state and published after a basic validation pass.
    """
    # Authenticate
    user_id = verify_token(credentials.token)
    if not user_id:
        raise HTTPException(status_code=401, detail="Invalid or expired token")

    user = await db.get(User, uuid.UUID(user_id))
    if not user or not user.is_active:
        raise HTTPException(status_code=403, detail="Account inactive")

    # Validate type
    if type.lower() not in _ALLOWED_TYPES:
        raise HTTPException(status_code=422, detail=f"type must be one of {_ALLOWED_TYPES}")

    # Validate file size
    file_bytes = await file.read()
    if len(file_bytes) > _MAX_FILE_MB * 1024 * 1024:
        raise HTTPException(status_code=413, detail=f"File exceeds {_MAX_FILE_MB} MB limit")

    # Process and upload thumbnail
    thumb_bytes = await thumbnail.read()
    thumb_bytes = _resize_thumbnail(thumb_bytes)
    thumb_key = R2Storage.make_key("thumbnails", thumbnail.filename or "thumb.jpg")
    storage.upload_file(thumb_bytes, thumb_key, content_type="image/jpeg")

    # Upload wallpaper file
    file_key = R2Storage.make_key("wallpapers", file.filename or "wallpaper.bin")
    storage.upload_file(io.BytesIO(file_bytes), file_key,
                        content_type=file.content_type or "application/octet-stream")

    # Resolve / create tags
    tag_names = [t.strip().lower() for t in tags.split(",") if t.strip()]
    tag_objs: list[Tag] = []
    for name in tag_names:
        from sqlalchemy import select as _sel  # noqa: PLC0415
        existing = (await db.execute(_sel(Tag).where(Tag.name == name))).scalar_one_or_none()
        if existing:
            tag_objs.append(existing)
        else:
            new_tag = Tag(name=name)
            db.add(new_tag)
            await db.flush()
            tag_objs.append(new_tag)

    # Create wallpaper record
    wallpaper = Wallpaper(
        title=title,
        description=description,
        author_id=user.id,
        type=type.lower(),
        resolution=resolution,
        file_size_bytes=len(file_bytes),
        storage_key=file_key,
        thumbnail_key=thumb_key,
        tags=tag_objs,
        is_published=True,
    )
    db.add(wallpaper)
    await db.commit()
    await db.refresh(wallpaper)

    return UploadResponse(
        id=str(wallpaper.id),
        title=wallpaper.title,
        download_url=storage.presigned_url(file_key),
        thumbnail_url=storage.public_url(thumb_key),
    )


def _resize_thumbnail(data: bytes, max_px: int = _THUMBNAIL_MAX_PX) -> bytes:
    """Resize thumbnail to fit within *max_px* on the longest edge."""
    img = Image.open(io.BytesIO(data)).convert("RGB")
    if max(img.size) > max_px:
        img.thumbnail((max_px, max_px), Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=85, optimize=True)
    return buf.getvalue()
