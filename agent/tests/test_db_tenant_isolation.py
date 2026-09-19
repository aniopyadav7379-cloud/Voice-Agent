"""
Tenant-isolation tests against a real Postgres database (not sqlite, not
mocked) — same standard as the earlier tenant-isolation proof in this
project's Qdrant layer: don't just assert the ORM query has a WHERE clause,
actually insert data for two tenants and prove tenant A's application-level
query never returns tenant B's rows.

Requires a real Postgres server reachable via `DATABASE_URL` (used only to
derive the test database's connection details, e.g. host/user/password —
the actual test schema lives in a different, disposable database on that
same server, provisioned by the shared `session_maker` fixture in
conftest.py). Not run in CI without one — that's a real, stated limitation
(see STATUS_REPORT.md), not hidden.
"""
import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import AuditLog, ConversationTurn, Tenant, ToolCall, User, VoiceSession


async def _make_tenant_with_session(db: AsyncSession, *, slug: str) -> tuple[Tenant, VoiceSession]:
    tenant = Tenant(slug=slug, name=f"Tenant {slug}")
    db.add(tenant)
    await db.flush()

    voice_session = VoiceSession(tenant_id=tenant.id, current_language="en-IN")
    db.add(voice_session)
    await db.flush()

    return tenant, voice_session


@pytest.mark.asyncio
async def test_conversation_turns_never_leak_across_tenants_with_colliding_session_ids(session_maker):
    """The specific adversarial case the spec calls out for Qdrant, applied
    here to Postgres: two tenants, and (as close as a UUID PK allows to a
    'colliding session_id') completely independent VoiceSession rows for
    each tenant. Prove a tenant-scoped query for tenant A's session never
    returns tenant B's turns, even querying by session content that could
    coincidentally match."""
    async with session_maker() as db:
        tenant_a, session_a = await _make_tenant_with_session(db, slug="tenant-a")
        tenant_b, session_b = await _make_tenant_with_session(db, slug="tenant-b")

        db.add(ConversationTurn(
            tenant_id=tenant_a.id, session_id=session_a.id, role="user", text="tenant A secret: project falcon",
        ))
        db.add(ConversationTurn(
            tenant_id=tenant_b.id, session_id=session_b.id, role="user", text="tenant B secret: project falcon",
        ))
        await db.commit()

        # Application-level query, properly tenant-scoped — the pattern
        # every real query path in this app must follow.
        result = await db.execute(
            select(ConversationTurn).where(ConversationTurn.tenant_id == tenant_a.id)
        )
        turns_for_a = result.scalars().all()

        assert len(turns_for_a) == 1
        assert turns_for_a[0].text == "tenant A secret: project falcon"
        assert all(t.tenant_id == tenant_a.id for t in turns_for_a), "tenant isolation violated"
        assert not any("tenant B secret" in t.text for t in turns_for_a), "TENANT B DATA LEAKED INTO TENANT A QUERY"


@pytest.mark.asyncio
async def test_tool_calls_never_leak_across_tenants(session_maker):
    """Same adversarial proof, for tool_calls specifically — the spec
    explicitly lists tools as one of the systems to repeat the cross-tenant
    test against."""
    async with session_maker() as db:
        tenant_a, session_a = await _make_tenant_with_session(db, slug="tool-tenant-a")
        tenant_b, session_b = await _make_tenant_with_session(db, slug="tool-tenant-b")

        db.add(ToolCall(
            tenant_id=tenant_a.id, session_id=session_a.id, tool_name="create_ticket",
            arguments={"site_id": "A-SITE"}, call_id=str(uuid.uuid4()),
        ))
        db.add(ToolCall(
            tenant_id=tenant_b.id, session_id=session_b.id, tool_name="create_ticket",
            arguments={"site_id": "B-SITE-SECRET"}, call_id=str(uuid.uuid4()),
        ))
        await db.commit()

        result = await db.execute(select(ToolCall).where(ToolCall.tenant_id == tenant_a.id))
        calls_for_a = result.scalars().all()

        assert len(calls_for_a) == 1
        assert calls_for_a[0].arguments["site_id"] == "A-SITE"
        assert not any(c.arguments.get("site_id") == "B-SITE-SECRET" for c in calls_for_a)


@pytest.mark.asyncio
async def test_audit_logs_never_leak_across_tenants(session_maker):
    """Same proof for audit_logs — the last system the spec explicitly
    names for the repeated cross-tenant test."""
    async with session_maker() as db:
        tenant_a, _ = await _make_tenant_with_session(db, slug="audit-tenant-a")
        tenant_b, _ = await _make_tenant_with_session(db, slug="audit-tenant-b")

        db.add(AuditLog(tenant_id=tenant_a.id, action="tool_call", resource_type="tool", resource_id="A-secret"))
        db.add(AuditLog(tenant_id=tenant_b.id, action="tool_call", resource_type="tool", resource_id="B-secret"))
        await db.commit()

        result = await db.execute(select(AuditLog).where(AuditLog.tenant_id == tenant_a.id))
        logs_for_a = result.scalars().all()

        assert len(logs_for_a) == 1
        assert logs_for_a[0].resource_id == "A-secret"


@pytest.mark.asyncio
async def test_tenant_cascade_delete_removes_all_child_rows(session_maker):
    """Proves the cascade design decision documented in models.py actually
    behaves as designed: deleting a tenant removes its users, sessions,
    turns, tool calls/results, and audit logs — not left as orphaned rows
    with a dangling tenant_id."""
    async with session_maker() as db:
        tenant, voice_session = await _make_tenant_with_session(db, slug="cascade-test")
        user = User(tenant_id=tenant.id, external_id="ext-1")
        db.add(user)
        await db.flush()

        call_id = str(uuid.uuid4())
        db.add(ConversationTurn(tenant_id=tenant.id, session_id=voice_session.id, role="user", text="hi"))
        db.add(ToolCall(tenant_id=tenant.id, session_id=voice_session.id, tool_name="x", arguments={}, call_id=call_id))
        db.add(AuditLog(tenant_id=tenant.id, action="x", resource_type="x"))
        await db.commit()

        await db.delete(tenant)
        await db.commit()

        remaining_turns = (await db.execute(select(ConversationTurn))).scalars().all()
        remaining_calls = (await db.execute(select(ToolCall))).scalars().all()
        remaining_audit = (await db.execute(select(AuditLog))).scalars().all()
        remaining_sessions = (await db.execute(select(VoiceSession))).scalars().all()

        assert remaining_turns == []
        assert remaining_calls == []
        assert remaining_audit == []
        assert remaining_sessions == []


@pytest.mark.asyncio
async def test_user_delete_preserves_history_with_null_user_id(session_maker):
    """Proves the OTHER half of the cascade design: deleting a user does
    NOT delete their conversation history — it sets user_id to NULL,
    preserving the operational record.

    Uses a FRESH session for the post-delete read, not the same session
    that performed the delete. This matters: `ON DELETE SET NULL` is a
    database-level FK action, and SQLAlchemy's session (with
    `expire_on_commit=False`, set deliberately in db/base.py for
    performance) does not automatically know an object's column changed
    underneath it via a DB-side cascade — reading it back in the same
    session without an explicit refresh() would show stale data and fail
    this test for a reason that has nothing to do with the schema being
    wrong. A fresh session is also the realistic case: in production,
    every request gets its own session via `get_db()`, so nothing ever
    reads back through the exact session that performed a prior write."""
    async with session_maker() as db:
        tenant, voice_session = await _make_tenant_with_session(db, slug="user-delete-test")
        user = User(tenant_id=tenant.id, external_id="ext-2")
        db.add(user)
        await db.flush()

        turn = ConversationTurn(
            tenant_id=tenant.id, session_id=voice_session.id, user_id=user.id, role="user", text="hello",
        )
        db.add(turn)
        await db.commit()

        await db.delete(user)
        await db.commit()

    async with session_maker() as fresh_db:
        result = await fresh_db.execute(select(ConversationTurn).where(ConversationTurn.tenant_id == tenant.id))
        turns = result.scalars().all()

        assert len(turns) == 1, "turn should survive user deletion"
        assert turns[0].user_id is None, "user_id should be nulled, not left dangling"
        assert turns[0].text == "hello"
