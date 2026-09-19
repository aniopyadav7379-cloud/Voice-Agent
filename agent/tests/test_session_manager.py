"""
Tests for VoiceSessionRecorder against real Postgres (shared session_maker
fixture from conftest.py — same dedicated, disposable test database as
test_db_tenant_isolation.py).
"""
import pytest
from sqlalchemy import select

from app.db.models import ConversationTurn, TurnRole, VoiceSessionStatus
from app.db.session_manager import VoiceSessionRecorder


@pytest.mark.asyncio
async def test_ensure_tenant_is_idempotent(session_maker):
    """Calling ensure_tenant twice with the same slug must return the SAME
    tenant row, not create a duplicate — this is what lets entrypoint()
    call it on every session start without a separate provisioning step."""
    async with session_maker() as db:
        recorder = VoiceSessionRecorder(db)

        tenant1 = await recorder.ensure_tenant(slug="acme-corp")
        await db.commit()

        tenant2 = await recorder.ensure_tenant(slug="acme-corp")
        await db.commit()

        assert tenant1.id == tenant2.id

        from app.db.models import Tenant
        result = await db.execute(select(Tenant).where(Tenant.slug == "acme-corp"))
        rows = result.scalars().all()
        assert len(rows) == 1, "ensure_tenant must not create duplicate rows for the same slug"


@pytest.mark.asyncio
async def test_full_session_lifecycle_persists_correctly(session_maker):
    """Simulates the real entrypoint.py flow: bootstrap tenant, start
    session, record a user turn and an assistant turn, end session — then
    verifies every piece landed correctly via a fresh session (same
    fresh-session discipline as test_db_tenant_isolation.py, for the same
    reason: DB-side defaults/server_default timestamps aren't guaranteed
    visible on the same in-memory object without a refresh)."""
    async with session_maker() as db:
        recorder = VoiceSessionRecorder(db)
        tenant = await recorder.ensure_tenant(slug="field-ops-tenant")
        voice_session = await recorder.start_session(tenant_id=tenant.id, initial_language="en-IN")
        await db.commit()
        session_id = voice_session.id
        tenant_id = tenant.id

        await recorder.record_turn(
            tenant_id=tenant_id, session_id=session_id, role=TurnRole.user,
            text="The pump at Site 14 is making a strange noise", language="en-IN",
        )
        await recorder.record_turn(
            tenant_id=tenant_id, session_id=session_id, role=TurnRole.assistant,
            text="Let me check the maintenance history for that pump.", language="en-IN",
            retrieval_metadata={"sources_queried": ["moss", "qdrant"]},
        )
        await db.commit()

        await recorder.end_session(voice_session)
        await db.commit()

    async with session_maker() as fresh_db:
        from app.db.models import VoiceSession as VS
        result = await fresh_db.execute(select(VS).where(VS.id == session_id))
        persisted_session = result.scalar_one()
        assert persisted_session.status == VoiceSessionStatus.closed
        assert persisted_session.ended_at is not None

        result = await fresh_db.execute(
            select(ConversationTurn).where(ConversationTurn.session_id == session_id).order_by(ConversationTurn.created_at)
        )
        turns = result.scalars().all()
        assert len(turns) == 2
        assert turns[0].role == TurnRole.user
        assert turns[0].text == "The pump at Site 14 is making a strange noise"
        assert turns[1].role == TurnRole.assistant
        assert turns[1].retrieval_metadata == {"sources_queried": ["moss", "qdrant"]}
