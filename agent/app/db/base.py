"""
Async SQLAlchemy engine/session setup.
"""
import os
from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy.pool import AsyncAdaptedQueuePool


class Base(DeclarativeBase):
    pass


def build_engine(database_url: str, *, test_mode: bool = False):
    connect_args = {
        "prepared_statement_cache_size": 0,
        "statement_cache_size": 0,
        "command_timeout": 10,
    }

    if test_mode:
        return create_async_engine(
            database_url,
            connect_args=connect_args,
            poolclass=AsyncAdaptedQueuePool,
            pool_size=2,
            max_overflow=1,
            pool_pre_ping=True,
            pool_recycle=300,
            echo=False,
        )

    return create_async_engine(
        database_url,
        connect_args=connect_args,
        pool_size=20,
        max_overflow=10,
        pool_pre_ping=True,
        echo=False,
    )


def build_session_maker(engine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(
        bind=engine,
        class_=AsyncSession,
        expire_on_commit=False,
        autoflush=False,
    )


_engine = None
_session_maker: async_sessionmaker[AsyncSession] | None = None


def get_session_maker() -> async_sessionmaker[AsyncSession]:
    global _engine, _session_maker
    if _session_maker is None:
        _engine = build_engine(os.environ["DATABASE_URL"])
        _session_maker = build_session_maker(_engine)
    return _session_maker


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    session_maker = get_session_maker()
    async with session_maker() as session:
        yield session