"""
Async SQLAlchemy engine/session setup. This project has no prior
Postgres/SQLAlchemy layer to migrate from — confirmed by grepping the
audited Voice-AI-Agent-master source (`sqlalchemy|asyncpg|psycopg|postgres`
across its whole app/ tree: zero matches, it's Qdrant + in-memory only) and
by this project's own `app/memory/` still being an empty placeholder. This
module and `models.py` are a clean build against the spec's required
domain areas, not a port.

Pattern (async engine, `pool_pre_ping`, scoped session-per-request via a
FastAPI-style generator dependency) follows the same shape used and
verified in an earlier, separate voice-gateway project this session's
author built — proven correct there against real Postgres, not
re-derived from scratch here.
"""
from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass


def build_engine(database_url: str):
    return create_async_engine(
        database_url,
        pool_size=20,
        max_overflow=10,
        pool_pre_ping=True,
        echo=False,
    )


def build_session_maker(engine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False, autoflush=False)


# Module-level singletons, constructed lazily from DATABASE_URL so importing
# this module doesn't require a database connection to exist.
_engine = None
_session_maker: async_sessionmaker[AsyncSession] | None = None


def get_engine():
    global _engine
    if _engine is None:
        import os

        _engine = build_engine(os.environ["DATABASE_URL"])
    return _engine


def get_session_maker() -> async_sessionmaker[AsyncSession]:
    global _session_maker
    if _session_maker is None:
        _session_maker = build_session_maker(get_engine())
    return _session_maker


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """Dependency-style generator — yields a scoped session, closes it
    after the caller is done with it."""
    async with get_session_maker()() as session:
        yield session
