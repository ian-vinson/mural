# platform/models.py
#
# Mural Content Platform — Server-side database models
# Copyright (C) 2024  Mural Contributors
# MIT License — see platform/LICENSE

"""SQLAlchemy ORM models for the Mural content platform.

Run with Alembic for migrations.  For v1, a simple ``Base.metadata.create_all``
is acceptable during development.

Tables:
    users       — creator accounts
    wallpapers  — wallpaper metadata
    tags        — normalised tag strings
    wallpaper_tags — many-to-many join
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Table,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    """Shared declarative base for all ORM models."""
    pass


# Many-to-many join table between wallpapers and tags
wallpaper_tags = Table(
    "wallpaper_tags",
    Base.metadata,
    Column("wallpaper_id", UUID(as_uuid=True), ForeignKey("wallpapers.id"), primary_key=True),
    Column("tag_id",       Integer,             ForeignKey("tags.id"),        primary_key=True),
)


class User(Base):
    """Creator account."""

    __tablename__ = "users"

    id:           Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    username:     Mapped[str]       = mapped_column(String(64), unique=True, nullable=False)
    email:        Mapped[str]       = mapped_column(String(254), unique=True, nullable=False)
    password_hash:Mapped[str]       = mapped_column(String(256), nullable=False)
    is_active:    Mapped[bool]      = mapped_column(Boolean, default=True)
    created_at:   Mapped[datetime]  = mapped_column(DateTime(timezone=True), server_default=func.now())

    wallpapers: Mapped[list["Wallpaper"]] = relationship("Wallpaper", back_populates="author")


class Tag(Base):
    """Normalised tag string."""

    __tablename__ = "tags"

    id:   Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)

    wallpapers: Mapped[list["Wallpaper"]] = relationship(
        "Wallpaper", secondary=wallpaper_tags, back_populates="tags"
    )


class Wallpaper(Base):
    """Wallpaper metadata record."""

    __tablename__ = "wallpapers"

    id:              Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    title:           Mapped[str]       = mapped_column(String(256), nullable=False)
    description:     Mapped[str]       = mapped_column(Text, default="")
    author_id:       Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    type:            Mapped[str]       = mapped_column(String(16), nullable=False)  # video|scene|web|image
    resolution:      Mapped[str]       = mapped_column(String(32), default="")
    file_size_bytes: Mapped[int]       = mapped_column(BigInteger, default=0)
    storage_key:     Mapped[str]       = mapped_column(String(512), nullable=False)   # R2 object key
    thumbnail_key:   Mapped[str]       = mapped_column(String(512), default="")       # R2 thumbnail key
    downloads:       Mapped[int]       = mapped_column(Integer, default=0)
    is_published:    Mapped[bool]      = mapped_column(Boolean, default=False)
    created_at:      Mapped[datetime]  = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at:      Mapped[datetime]  = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    author: Mapped["User"]      = relationship("User", back_populates="wallpapers")
    tags:   Mapped[list["Tag"]] = relationship("Tag", secondary=wallpaper_tags, back_populates="wallpapers")
