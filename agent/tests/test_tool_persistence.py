"""
Tests for the ToolCall/ToolResult persistence wiring in
`app/tools/registry.py` + `app/db/session_manager.py::ToolCallRecorder`.
Real Postgres (shared `session_maker` fixture from conftest.py — the
dedicated, disposable test database), not mocked at the DB layer.
"""
import uuid

import pytest
from pydantic import BaseModel
from sqlalchemy import select

from app.db.models import Tenant, ToolCall, ToolCallStatus, ToolResult, VoiceSession, VoiceSessionStatus
from app.db.session_manager import ToolCallRecorder
from app.tools.registry import ToolNotFoundError, ToolRegistry


class _EchoArgs(BaseModel):
    message: str
    password: str | None = None  # deliberately sensitive-shaped, for redaction tests


async def _echo_handler(args: _EchoArgs, tenant_id: str, session_id: str) -> dict:
    return {"echoed": args.message}


async def _boom_handler(args: _EchoArgs, tenant_id: str, session_id: str) -> dict:
    raise RuntimeError("simulated real failure")


async def _slow_handler(args: _EchoArgs, tenant_id: str, session_id: str) -> dict:
    import asyncio

    await asyncio.sleep(10)  # longer than the 0.05s timeout used in the timeout test
    return {"should": "never get here"}


def _build_registry() -> ToolRegistry:
    registry = ToolRegistry()
    registry.register("echo", _EchoArgs, _echo_handler)
    registry.register("boom", _EchoArgs, _boom_handler)
    registry.register("slow", _EchoArgs, _slow_handler, timeout_seconds=0.05)
    return registry


async def _bootstrap_tenant_session(db, *, slug: str):
    tenant = Tenant(slug=slug, name=slug)
    db.add(tenant)
    await db.flush()
    voice_session = VoiceSession(tenant_id=tenant.id, current_language="en-IN", status=VoiceSessionStatus.active)
    db.add(voice_session)
    await db.flush()
    await db.commit()
    return tenant, voice_session


@pytest.mark.asyncio
async def test_successful_call_persists_toolcall_and_toolresult_with_correct_correlation(session_maker):
    async with session_maker() as db:
        tenant, voice_session = await _bootstrap_tenant_session(db, slug="tool-persist-success")
        recorder = ToolCallRecorder(db)
        registry = _build_registry()

        result = await registry.execute(
            tool_name="echo", raw_args={"message": "hello"}, tenant_id=str(tenant.id), session_id=str(voice_session.id),
            db_recorder=recorder, tenant_uuid=tenant.id, session_uuid=voice_session.id,
        )
        assert result.success is True
        assert result.output == {"echoed": "hello"}

    async with session_maker() as fresh_db:
        call_row = (await fresh_db.execute(select(ToolCall).where(ToolCall.call_id == result.call_id))).scalar_one()
        assert call_row.status == ToolCallStatus.succeeded
        assert call_row.tenant_id == tenant.id
        assert call_row.session_id == voice_session.id
        assert call_row.completed_at is not None

        result_row = (await fresh_db.execute(select(ToolResult).where(ToolResult.tool_call_id == call_row.id))).scalar_one()
        assert result_row.success is True
        assert result_row.output == {"echoed": "hello"}, "structured output must persist as real JSON, not a stringified blob"
        assert result_row.error is None

        # call_id correlation: ToolCall.call_id (the app-level identifier)
        # correctly maps to ToolResult via ToolCall.id (the DB-level FK),
        # not some second, redundant identifier.
        assert result_row.tool_call_id == call_row.id


@pytest.mark.asyncio
async def test_failed_handler_never_persists_fabricated_success(session_maker):
    """The mandatory invariant, tested explicitly and directly against the
    database — not just against the in-memory ToolResult."""
    async with session_maker() as db:
        tenant, voice_session = await _bootstrap_tenant_session(db, slug="tool-persist-failure")
        recorder = ToolCallRecorder(db)
        registry = _build_registry()

        result = await registry.execute(
            tool_name="boom", raw_args={"message": "x"}, tenant_id=str(tenant.id), session_id=str(voice_session.id),
            db_recorder=recorder, tenant_uuid=tenant.id, session_uuid=voice_session.id,
        )
        assert result.success is False

    async with session_maker() as fresh_db:
        call_row = (await fresh_db.execute(select(ToolCall).where(ToolCall.call_id == result.call_id))).scalar_one()
        assert call_row.status == ToolCallStatus.failed

        result_row = (await fresh_db.execute(select(ToolResult).where(ToolResult.tool_call_id == call_row.id))).scalar_one()
        assert result_row.success is False, "MUST NOT be True — the handler actually raised"
        assert "simulated real failure" in result_row.error
        assert result_row.output is None


@pytest.mark.asyncio
async def test_timeout_persists_timed_out_status(session_maker):
    async with session_maker() as db:
        tenant, voice_session = await _bootstrap_tenant_session(db, slug="tool-persist-timeout")
        recorder = ToolCallRecorder(db)
        registry = _build_registry()

        result = await registry.execute(
            tool_name="slow", raw_args={"message": "x"}, tenant_id=str(tenant.id), session_id=str(voice_session.id),
            db_recorder=recorder, tenant_uuid=tenant.id, session_uuid=voice_session.id,
        )
        assert result.success is False
        assert "timed out" in result.error

    async with session_maker() as fresh_db:
        call_row = (await fresh_db.execute(select(ToolCall).where(ToolCall.call_id == result.call_id))).scalar_one()
        assert call_row.status == ToolCallStatus.timed_out

        result_row = (await fresh_db.execute(select(ToolResult).where(ToolResult.tool_call_id == call_row.id))).scalar_one()
        assert result_row.success is False


@pytest.mark.asyncio
async def test_invalid_arguments_are_rejected_with_no_toolresult_row(session_maker):
    """Validation failures get a ToolCall(status=rejected) and explicitly
    NO ToolResult row — the handler never ran, so there is nothing to
    record a result for. This is the distinction the task asked to
    preserve, verified structurally, not just by status string."""
    async with session_maker() as db:
        tenant, voice_session = await _bootstrap_tenant_session(db, slug="tool-persist-invalid-args")
        recorder = ToolCallRecorder(db)
        registry = _build_registry()

        result = await registry.execute(
            tool_name="echo", raw_args={"not_a_real_field": 123}, tenant_id=str(tenant.id), session_id=str(voice_session.id),
            db_recorder=recorder, tenant_uuid=tenant.id, session_uuid=voice_session.id,
        )
        assert result.success is False
        assert "invalid arguments" in result.error

    async with session_maker() as fresh_db:
        call_row = (await fresh_db.execute(select(ToolCall).where(ToolCall.call_id == result.call_id))).scalar_one()
        assert call_row.status == ToolCallStatus.rejected

        result_row = (await fresh_db.execute(select(ToolResult).where(ToolResult.tool_call_id == call_row.id))).scalar_one_or_none()
        assert result_row is None, "a rejected call must have NO ToolResult row"


@pytest.mark.asyncio
async def test_unknown_tool_name_is_rejected_and_persisted(session_maker):
    async with session_maker() as db:
        tenant, voice_session = await _bootstrap_tenant_session(db, slug="tool-persist-unknown")
        recorder = ToolCallRecorder(db)
        registry = _build_registry()

        with pytest.raises(ToolNotFoundError):
            await registry.execute(
                tool_name="delete_entire_database", raw_args={}, tenant_id=str(tenant.id), session_id=str(voice_session.id),
                db_recorder=recorder, tenant_uuid=tenant.id, session_uuid=voice_session.id,
            )

    async with session_maker() as fresh_db:
        result = await fresh_db.execute(
            select(ToolCall).where(ToolCall.tenant_id == tenant.id, ToolCall.tool_name == "delete_entire_database")
        )
        call_row = result.scalar_one()
        assert call_row.status == ToolCallStatus.rejected

        result_row = await fresh_db.execute(select(ToolResult).where(ToolResult.tool_call_id == call_row.id))
        assert result_row.scalar_one_or_none() is None


@pytest.mark.asyncio
async def test_sensitive_arguments_are_redacted_before_persisting(session_maker):
    """Structural proof, not a string-search on a log line: the persisted
    JSONB `arguments` column must not contain the raw secret value."""
    async with session_maker() as db:
        tenant, voice_session = await _bootstrap_tenant_session(db, slug="tool-persist-redaction")
        recorder = ToolCallRecorder(db)
        registry = _build_registry()

        result = await registry.execute(
            tool_name="echo", raw_args={"message": "hi", "password": "hunter2-super-secret"},
            tenant_id=str(tenant.id), session_id=str(voice_session.id),
            db_recorder=recorder, tenant_uuid=tenant.id, session_uuid=voice_session.id,
        )

    async with session_maker() as fresh_db:
        call_row = (await fresh_db.execute(select(ToolCall).where(ToolCall.call_id == result.call_id))).scalar_one()
        assert call_row.arguments["password"] == "[REDACTED]"
        assert "hunter2-super-secret" not in str(call_row.arguments)


@pytest.mark.asyncio
async def test_tool_calls_never_leak_across_tenants_via_persistence(session_maker):
    """The mandatory cross-tenant test, applied specifically to the new
    persistence path (session 4 already proved this at the raw-model
    level; this proves it end-to-end through registry.execute())."""
    async with session_maker() as db:
        tenant_a, session_a = await _bootstrap_tenant_session(db, slug="tool-tenant-a-exec")
        tenant_b, session_b = await _bootstrap_tenant_session(db, slug="tool-tenant-b-exec")
        registry = _build_registry()
        recorder_a = ToolCallRecorder(db)
        recorder_b = ToolCallRecorder(db)

        await registry.execute(
            tool_name="echo", raw_args={"message": "tenant A data"}, tenant_id=str(tenant_a.id), session_id=str(session_a.id),
            db_recorder=recorder_a, tenant_uuid=tenant_a.id, session_uuid=session_a.id,
        )
        await registry.execute(
            tool_name="echo", raw_args={"message": "tenant B secret data"}, tenant_id=str(tenant_b.id), session_id=str(session_b.id),
            db_recorder=recorder_b, tenant_uuid=tenant_b.id, session_uuid=session_b.id,
        )

    async with session_maker() as fresh_db:
        result = await fresh_db.execute(select(ToolCall).where(ToolCall.tenant_id == tenant_a.id))
        calls_for_a = result.scalars().all()
        assert len(calls_for_a) == 1
        assert calls_for_a[0].arguments["message"] == "tenant A data"
        assert not any("tenant B secret" in str(c.arguments) for c in calls_for_a)


@pytest.mark.asyncio
async def test_repeated_calls_are_not_deduplicated(session_maker):
    """Documents the actual (non-)guarantee: no idempotency exists. Two
    calls with identical arguments produce two distinct ToolCall rows."""
    async with session_maker() as db:
        tenant, voice_session = await _bootstrap_tenant_session(db, slug="tool-persist-repeat")
        recorder = ToolCallRecorder(db)
        registry = _build_registry()

        r1 = await registry.execute(
            tool_name="echo", raw_args={"message": "same"}, tenant_id=str(tenant.id), session_id=str(voice_session.id),
            db_recorder=recorder, tenant_uuid=tenant.id, session_uuid=voice_session.id,
        )
        r2 = await registry.execute(
            tool_name="echo", raw_args={"message": "same"}, tenant_id=str(tenant.id), session_id=str(voice_session.id),
            db_recorder=recorder, tenant_uuid=tenant.id, session_uuid=voice_session.id,
        )
        assert r1.call_id != r2.call_id

    async with session_maker() as fresh_db:
        result = await fresh_db.execute(select(ToolCall).where(ToolCall.tenant_id == tenant.id))
        rows = result.scalars().all()
        assert len(rows) == 2


@pytest.mark.asyncio
async def test_toolresult_persistence_failure_does_not_corrupt_returned_result(session_maker):
    """Simulates a DB write failure AFTER the handler has already
    succeeded. execute() must still return the real, correct ToolResult —
    a persistence problem must never be allowed to change what the agent
    is told happened."""
    async with session_maker() as db:
        tenant, voice_session = await _bootstrap_tenant_session(db, slug="tool-persist-write-failure")
        registry = _build_registry()

        class _BrokenRecorder(ToolCallRecorder):
            async def record_result(self, **kwargs):
                raise RuntimeError("simulated database outage during result write")

        recorder = _BrokenRecorder(db)

        result = await registry.execute(
            tool_name="echo", raw_args={"message": "still correct"}, tenant_id=str(tenant.id), session_id=str(voice_session.id),
            db_recorder=recorder, tenant_uuid=tenant.id, session_uuid=voice_session.id,
        )

        # The handler DID succeed — execute() must reflect that truthfully,
        # regardless of the persistence layer blowing up afterward.
        assert result.success is True
        assert result.output == {"echoed": "still correct"}


@pytest.mark.asyncio
async def test_no_persistence_params_falls_back_to_original_in_memory_only_behavior(session_maker):
    """Explicit regression proof that every pre-existing caller (every
    test written before this milestone, and any future caller that
    doesn't opt in) is completely unaffected."""
    registry = _build_registry()
    result = await registry.execute(tool_name="echo", raw_args={"message": "no db involved"}, tenant_id="t", session_id="s")
    assert result.success is True
    assert result.output == {"echoed": "no db involved"}

    async with session_maker() as fresh_db:
        count = (await fresh_db.execute(select(ToolCall))).scalars().all()
        assert count == [], "no ToolCall row should exist anywhere — persistence was never opted into"
