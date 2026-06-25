# platform/db.py — async SQLAlchemy session factory
# MIT License

from __future__ import annotations
import os
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

DATABASE_URL = os.environ.get(
    "DATABASE_URL",
    "postgresql+asyncpg://mural:mural@localhost/mural",
)

_engine = create_async_engine(DATABASE_URL, echo=False, pool_pre_ping=True)
_SessionLocal = async_sessionmaker(_engine, expire_on_commit=False)


async def get_session() -> AsyncSession:
    """FastAPI dependency that yields a database session."""
    async with _SessionLocal() as session:
        yield session
