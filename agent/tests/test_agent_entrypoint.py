"""
Unit tests for the parts of app.agent_entrypoint that don't require a live
LiveKit room/session: `_record_boundary_failure`, `aiter_db_session`, and
`FieldOpsAssistant`'s two function-tool methods. `entrypoint()` itself is
not exercised here -- it needs a real JobContext/room and is explicitly
documented (in the module's own docstring) as not run end to end.
"""
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.agent_entrypoint import FieldOpsAssistant, _record_boundary_failure, aiter_db_session
from app.context.types import ContextBundle
from app.security.identity import TenantNotFoundError, UserNotFoundError
from app.security.session_boundary import AuthenticationRejectedError, NoParticipantError
from app.tools.registry import ToolNotFoundError


# --------------------------------------------------------------------------
# _record_boundary_failure
# --------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_boundary_failure_with_verified_tenant_writes_audit_log():
    tenant_id = uuid.uuid4()
    cause = UserNotFoundError("no such user")
    cause.verified_tenant_id = tenant_id
    error = AuthenticationRejectedError(cause)

    with patch("app.agent_entrypoint.AuditRecorder") as MockAudit, \
         patch("app.agent_entrypoint.SecurityEventRecorder") as MockSecurity:
        MockAudit.return_value.record = AsyncMock()
        MockSecurity.return_value.record = AsyncMock()

        await _record_boundary_failure(db=MagicMock(), error=error, room_name="room-1")

        MockAudit.return_value.record.assert_awaited_once()
        kwargs = MockAudit.return_value.record.await_args.kwargs
        assert kwargs["tenant_id"] == tenant_id
        assert kwargs["action"] == "auth_rejected"
        assert kwargs["resource_type"] == "session"
        assert kwargs["resource_id"] == "room-1"
        assert kwargs["metadata"] == {"reason": "UserNotFoundError"}
        MockSecurity.return_value.record.assert_not_awaited()


@pytest.mark.asyncio
async def test_boundary_failure_without_verified_tenant_writes_security_event():
    cause = TenantNotFoundError("no such tenant")  # verified_tenant_id defaults to None
    error = AuthenticationRejectedError(cause)

    with patch("app.agent_entrypoint.AuditRecorder") as MockAudit, \
         patch("app.agent_entrypoint.SecurityEventRecorder") as MockSecurity:
        MockAudit.return_value.record = AsyncMock()
        MockSecurity.return_value.record = AsyncMock()

        await _record_boundary_failure(db=MagicMock(), error=error, room_name="room-2")

        MockAudit.return_value.record.assert_not_awaited()
        MockSecurity.return_value.record.assert_awaited_once()
        kwargs = MockSecurity.return_value.record.await_args.kwargs
        assert kwargs["action"] == "auth_rejected"
        assert kwargs["reason"] == "TenantNotFoundError"
        assert kwargs["resource_id"] == "room-2"


@pytest.mark.asyncio
async def test_boundary_failure_with_no_cause_uses_error_reason_for_security_event():
    error = NoParticipantError(timeout_seconds=60.0)  # no .cause at all

    with patch("app.agent_entrypoint.AuditRecorder") as MockAudit, \
         patch("app.agent_entrypoint.SecurityEventRecorder") as MockSecurity:
        MockAudit.return_value.record = AsyncMock()
        MockSecurity.return_value.record = AsyncMock()

        await _record_boundary_failure(db=MagicMock(), error=error, room_name="room-3")

        MockAudit.return_value.record.assert_not_awaited()
        kwargs = MockSecurity.return_value.record.await_args.kwargs
        assert kwargs["reason"] == "no_participant"


@pytest.mark.asyncio
async def test_boundary_failure_never_raises_even_if_both_writes_fail():
    cause = UserNotFoundError("no such user")
    cause.verified_tenant_id = uuid.uuid4()
    error = AuthenticationRejectedError(cause)

    with patch("app.agent_entrypoint.AuditRecorder") as MockAudit, \
         patch("app.agent_entrypoint.SecurityEventRecorder") as MockSecurity:
        MockAudit.return_value.record = AsyncMock(side_effect=RuntimeError("db is down"))
        MockSecurity.return_value.record = AsyncMock(side_effect=RuntimeError("also down"))

        # Must not raise -- a failure to record a rejection must never mask
        # the rejection itself.
        await _record_boundary_failure(db=MagicMock(), error=error, room_name="room-4")

        MockSecurity.return_value.record.assert_awaited_once()  # fallback was attempted


@pytest.mark.asyncio
async def test_boundary_failure_falls_back_to_security_event_when_audit_write_fails():
    cause = UserNotFoundError("no such user")
    cause.verified_tenant_id = uuid.uuid4()
    error = AuthenticationRejectedError(cause)

    with patch("app.agent_entrypoint.AuditRecorder") as MockAudit, \
         patch("app.agent_entrypoint.SecurityEventRecorder") as MockSecurity:
        MockAudit.return_value.record = AsyncMock(side_effect=RuntimeError("db is down"))
        MockSecurity.return_value.record = AsyncMock()

        await _record_boundary_failure(db=MagicMock(), error=error, room_name="room-5")

        MockSecurity.return_value.record.assert_awaited_once()


# --------------------------------------------------------------------------
# aiter_db_session
# --------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_aiter_db_session_yields_a_single_session():
    fake_db = MagicMock(name="fake_db_session")

    async def fake_get_db():
        yield fake_db

    with patch("app.db.base.get_db", fake_get_db):
        gen = aiter_db_session()
        db = await anext(gen)
        assert db is fake_db

        with pytest.raises(StopAsyncIteration):
            await anext(gen)


# --------------------------------------------------------------------------
# FieldOpsAssistant.retrieve_context
# --------------------------------------------------------------------------

def make_assistant(orchestrator=None, tool_call_recorder=None):
    return FieldOpsAssistant(
        tenant_id="tenant-a",
        session_id="session-a",
        orchestrator=orchestrator or AsyncMock(),
        tool_call_recorder=tool_call_recorder,
    )


@pytest.mark.asyncio
async def test_retrieve_context_formats_fast_context_only():
    orchestrator = AsyncMock()
    orchestrator.retrieve_context.return_value = ContextBundle(fast_context=[{"text": "recent note"}])
    assistant = make_assistant(orchestrator)

    result = await assistant.retrieve_context(None, "what's the status")

    orchestrator.retrieve_context.assert_awaited_once_with(
        tenant_id="tenant-a", session_id="session-a", query_text="what's the status"
    )
    assert result == "Recent context:\nrecent note"


@pytest.mark.asyncio
async def test_retrieve_context_formats_deep_context_only():
    deep_doc = MagicMock(text="knowledge base excerpt")
    orchestrator = AsyncMock()
    orchestrator.retrieve_context.return_value = ContextBundle(deep_context=[deep_doc])
    assistant = make_assistant(orchestrator)

    result = await assistant.retrieve_context(None, "history of this site")

    assert result == "Knowledge base:\nknowledge base excerpt"


@pytest.mark.asyncio
async def test_retrieve_context_formats_live_data_only():
    orchestrator = AsyncMock()
    orchestrator.retrieve_context.return_value = ContextBundle(live_data={"status": "unavailable"})
    assistant = make_assistant(orchestrator)

    result = await assistant.retrieve_context(None, "current weather")

    assert result == "Live status: {'status': 'unavailable'}"


@pytest.mark.asyncio
async def test_retrieve_context_combines_all_sections_in_order():
    deep_doc = MagicMock(text="kb text")
    orchestrator = AsyncMock()
    orchestrator.retrieve_context.return_value = ContextBundle(
        fast_context=[{"text": "fast text"}], deep_context=[deep_doc], live_data={"a": 1},
    )
    assistant = make_assistant(orchestrator)

    result = await assistant.retrieve_context(None, "everything")

    assert result == "Recent context:\nfast text\n\nKnowledge base:\nkb text\n\nLive status: {'a': 1}"


@pytest.mark.asyncio
async def test_retrieve_context_returns_fallback_message_when_bundle_is_empty():
    orchestrator = AsyncMock()
    orchestrator.retrieve_context.return_value = ContextBundle()
    assistant = make_assistant(orchestrator)

    result = await assistant.retrieve_context(None, "anything")

    assert result == "No relevant context found."


@pytest.mark.asyncio
async def test_retrieve_context_degraded_sources_dont_affect_return_value():
    orchestrator = AsyncMock()
    orchestrator.retrieve_context.return_value = ContextBundle(
        fast_context=[{"text": "ok"}], degraded=["qdrant"],
    )
    assistant = make_assistant(orchestrator)

    result = await assistant.retrieve_context(None, "q")

    assert result == "Recent context:\nok"


# --------------------------------------------------------------------------
# FieldOpsAssistant.call_tool
# --------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_call_tool_reports_success_prefix():
    assistant = make_assistant()
    assistant._tool_registry = AsyncMock()
    assistant._tool_registry.execute.return_value = MagicMock(success=True, output={"ticket_id": "T-1"}, error=None)

    result = await assistant.call_tool(None, "create_ticket", {"site_id": "S-1", "description": "leak"})

    assert result == "SUCCESS: {'ticket_id': 'T-1'}"


@pytest.mark.asyncio
async def test_call_tool_reports_failed_prefix_and_never_claims_success():
    assistant = make_assistant()
    assistant._tool_registry = AsyncMock()
    assistant._tool_registry.execute.return_value = MagicMock(success=False, output=None, error="tool failed: boom")

    result = await assistant.call_tool(None, "escalate_to_human", {"session_id": "s", "reason": "r"})

    assert result == "FAILED: tool failed: boom"
    assert "SUCCESS" not in result


@pytest.mark.asyncio
async def test_call_tool_passes_identity_and_recorder_through_to_registry():
    tenant_uuid, session_uuid, user_uuid = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    recorder = MagicMock()
    assistant = FieldOpsAssistant(
        tenant_id="tenant-a", session_id="session-a", orchestrator=AsyncMock(),
        tool_call_recorder=recorder, tenant_uuid=tenant_uuid, session_uuid=session_uuid, user_uuid=user_uuid,
    )
    assistant._tool_registry = AsyncMock()
    assistant._tool_registry.execute.return_value = MagicMock(success=True, output={}, error=None)

    await assistant.call_tool(None, "escalate_to_human", {"session_id": "s", "reason": "r"})

    kwargs = assistant._tool_registry.execute.await_args.kwargs
    assert kwargs["tool_name"] == "escalate_to_human"
    assert kwargs["tenant_id"] == "tenant-a"
    assert kwargs["session_id"] == "session-a"
    assert kwargs["db_recorder"] is recorder
    assert kwargs["tenant_uuid"] == tenant_uuid
    assert kwargs["session_uuid"] == session_uuid
    assert kwargs["user_uuid"] == user_uuid


@pytest.mark.asyncio
async def test_call_tool_with_real_registry_on_unknown_tool_raises():
    """call_tool has no try/except around execute() -- an unknown tool name
    must propagate ToolNotFoundError rather than being swallowed into a
    quiet 'FAILED' string, since that would blur a programming error into
    an ordinary tool failure."""
    assistant = make_assistant()  # real build_tool_registry() from __init__

    with pytest.raises(ToolNotFoundError):
        await assistant.call_tool(None, "not_a_real_tool", {})


@pytest.mark.asyncio
async def test_call_tool_with_real_registry_stub_tool_fails_honestly():
    assistant = make_assistant()  # real registry, real stub handler

    result = await assistant.call_tool(None, "escalate_to_human", {"session_id": "s-1", "reason": "angry customer"})

    assert result.startswith("FAILED:")
    assert "No real backend is wired up for 'escalate_to_human'" in result
