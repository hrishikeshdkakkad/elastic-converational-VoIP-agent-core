"""Database service for managing PostgreSQL connections."""

from __future__ import annotations

import os
from contextlib import asynccontextmanager
from typing import AsyncGenerator

import logging

from pydantic import PostgresDsn
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import declarative_base

from src.voice_ai_system.config import settings

logger = logging.getLogger(__name__)

# Base for SQLAlchemy models (used by Alembic as well)
Base = declarative_base()


def _default_database_url() -> str:
    """Return the default async database URL."""
    return os.getenv(
        "DATABASE_URL",
        "postgresql+asyncpg://temporal:temporal@postgresql:5432/voice_ai",
    )


_engine: AsyncEngine | None = None
_sessionmaker: async_sessionmaker[AsyncSession] | None = None


async def init_engine(database_url: str | PostgresDsn | None = None) -> AsyncEngine:
    """Initialise the async engine lazily.

    Args:
        database_url: Database connection URL as string or PostgresDsn object.
                     If None, uses the default from environment variables.

    Returns:
        AsyncEngine: SQLAlchemy async engine instance.
    """
    global _engine, _sessionmaker

    if _engine is not None:
        return _engine

    # Handle both string and PostgresDsn types
    if database_url is None:
        url = _default_database_url()
    elif isinstance(database_url, PostgresDsn):
        url = str(database_url)
    else:
        url = database_url

    _engine = create_async_engine(
        url,
        echo=False,
        pool_pre_ping=True,
        pool_size=settings.db_pool_size,
        max_overflow=settings.db_max_overflow,
        pool_timeout=settings.db_pool_timeout,
    )

    logger.info(
        f"Database engine initialized: pool_size={settings.db_pool_size}, "
        f"max_overflow={settings.db_max_overflow}, pool_timeout={settings.db_pool_timeout}"
    )
    _sessionmaker = async_sessionmaker(
        _engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )
    return _engine


async def dispose_engine() -> None:
    """Dispose the engine (used during shutdown)."""
    global _engine, _sessionmaker
    if _engine is not None:
        await _engine.dispose()
    _engine = None
    _sessionmaker = None


@asynccontextmanager
async def get_db_session() -> AsyncGenerator[AsyncSession, None]:
    """Async session generator that ensures the engine exists."""
    global _sessionmaker

    if _sessionmaker is None:
        await init_engine()

    assert _sessionmaker is not None

    async with _sessionmaker() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
