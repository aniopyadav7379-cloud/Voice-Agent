"""
Shared fixtures. `session_maker` provisions a dedicated, disposable test
database (never the one DATABASE_URL points at — see
test_db_tenant_isolation.py's module docstring for why that distinction
matters; it was a real bug when this fixture lived duplicated in that file
instead of shared here).
"""
import os

import asyncpg
import pytest_asyncio
from sqlalchemy.engine.url import make_url

from app.db.base import Base, build_engine, build_session_maker

TEST_DB_SUFFIX = "_pytest"


def test_database_url() -> str:
    base_url = make_url(os.environ["DATABASE_URL"])
    test_url = base_url.set(database=base_url.database + TEST_DB_SUFFIX)
    # render_as_string(hide_password=False) — NOT str(test_url), which
    # masks the password as '***' by default and silently produces an
    # unusable connection string. Real bug, found and documented in
    # test_db_tenant_isolation.py's git history; fixed once here so every
    # test file that needs a real DB shares the fix.
    return test_url.render_as_string(hide_password=False)


async def _ensure_test_database_exists() -> None:
    base_url = make_url(os.environ["DATABASE_URL"])
    test_db_name = base_url.database + TEST_DB_SUFFIX

    conn = await asyncpg.connect(
        host=base_url.host, port=base_url.port or 5432,
        user=base_url.username, password=base_url.password, database="postgres",
    )
    try:
        exists = await conn.fetchval("SELECT 1 FROM pg_database WHERE datname = $1", test_db_name)
        if not exists:
            await conn.execute(f'CREATE DATABASE "{test_db_name}"')
    finally:
        await conn.close()


@pytest_asyncio.fixture
async def session_maker():
    await _ensure_test_database_exists()

    engine = build_engine(test_database_url())
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)

    maker = build_session_maker(engine)
    yield maker

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()
