"""
Tests for the first real operational tool (`create_ticket`) and the
contract/authorization/idempotency layer around it.

Real Postgres via the shared disposable-database fixture. The thing
behind `TicketService` is the clearly-labelled TEST ADAPTER
(`InMemoryTicketService`) — no external ticketing system exists or is
contacted. See ticket_service.py.
"""
import asyncio
import uuid

import pytest
from sqlalchemy import select

from app.db.models import (
    Tenant,
    TenantStatus,
    ToolCall,
    ToolCallStatus,
    ToolResult,
    User,
    UserStatus,
    VoiceSession,
    VoiceSessionStatus,
)
from app.db.session_manager import ToolCallRecorder
from app.security.identity import AuthenticatedIdentity, IdentitySource
from app.tools.contracts import FailureClass, IdempotencyMode, SideEffect, is_retry_safe
from app.tools.create_ticket import (
    CREATE_TICKET_CONTRACT,
    AuthorizationError,
    CreateTicketInput,
    authorize,
    build_create_ticket_handler,
)
from app.tools.executor import ContractToolExecutor
from app.tools.idempotency import derive_idempotency_key
from app.tools.ticket_service import InMemoryTicketService, TicketServiceError


async def _bootstrap(db, *, slug: str):
    tenant = Tenant(slug=slug, name=slug, status=TenantStatus.active)
    db.add(tenant)
    await db.flush()
    user = User(tenant_id=tenant.id, external_id=f"{slug}-user", status=UserStatus.active)
    db.add(user)
    await db.flush()
    vs = VoiceSession(
        tenant_id=tenant.id, user_id=user.id, current_language="en-IN", status=VoiceSessionStatus.active
    )
    db.add(vs)
    await db.flush()
    await db.commit()
    identity = AuthenticatedIdentity(
        user_id=user.id, tenant_id=tenant.id, tenant_slug=slug, external_id=user.external_id,
        source=IdentitySource.LIVEKIT_PARTICIPANT,
    )
    return tenant, user, vs, identity


def _executor(db):
    return ContractToolExecutor(db, ToolCallRecorder(db))


VALID_ARGS = {"site_id": "site-14", "description": "pump making a strange noise", "priority": "high"}


# --- 1, 7, 8, 9, 10, 18: happy path end to end -----------------------------

@pytest.mark.asyncio
async def test_valid_create_ticket_persists_full_lifecycle(session_maker):
    async with session_maker() as db:
        tenant, user, vs, identity = await _bootstrap(db, slug="tkt-happy")
        service = InMemoryTicketService()
        result = await _executor(db).execute(
            contract=CREATE_TICKET_CONTRACT, handler=build_create_ticket_handler(service),
            raw_args=VALID_ARGS, identity=identity, session_uuid=vs.id,
        )

        assert result.success is True
        assert result.output["ticket_id"].startswith("TEST-TKT-")
        assert result.output["status"] == "open"
        assert service.create_call_count == 1

    async with session_maker() as fresh:
        call = (await fresh.execute(select(ToolCall).where(ToolCall.call_id == result.call_id))).scalar_one()
        assert call.status == ToolCallStatus.succeeded
        assert call.tenant_id == tenant.id
        assert call.user_id == user.id            # 4: user propagation
        assert call.session_id == vs.id           # 6: session propagation
        assert call.idempotency_key is not None

        res = (await fresh.execute(select(ToolResult).where(ToolResult.tool_call_id == call.id))).scalar_one()
        assert res.success is True
        assert res.output["ticket_id"] == result.output["ticket_id"]  # 18: structured result


# --- 2, 3: invalid and malicious input -------------------------------------

@pytest.mark.asyncio
@pytest.mark.parametrize(
    "bad_args, label",
    [
        ({"site_id": "", "description": "x", "priority": "high"}, "empty site_id"),
        ({"site_id": "s", "description": "x", "priority": "SUPER_URGENT"}, "priority not in enum"),
        ({"description": "x", "priority": "high"}, "missing required site_id"),
        ({"site_id": "s", "description": "x" * 5000, "priority": "high"}, "description over max length"),
        ({"site_id": "s; DROP TABLE tool_calls;--", "description": "x", "priority": "high"}, "SQL-ish injection"),
        ({"site_id": "../../etc/passwd", "description": "x", "priority": "high"}, "path traversal"),
        ({"site_id": "http://evil.example.com", "description": "x", "priority": "high"}, "URL injection"),
    ],
)
async def test_invalid_and_malicious_input_rejected_before_service(session_maker, bad_args, label):
    """Rejected by schema validation BEFORE the handler runs — proven by
    the test adapter's call counter staying at zero."""
    async with session_maker() as db:
        tenant, user, vs, identity = await _bootstrap(db, slug=f"tkt-bad-{abs(hash(label)) % 10000}")
        service = InMemoryTicketService()
        result = await _executor(db).execute(
            contract=CREATE_TICKET_CONTRACT, handler=build_create_ticket_handler(service),
            raw_args=bad_args, identity=identity, session_uuid=vs.id,
        )
        assert result.success is False, label
        assert "invalid arguments" in result.error, label
        assert service.create_call_count == 0, f"{label}: service must never be reached"

    async with session_maker() as fresh:
        call = (await fresh.execute(select(ToolCall).where(ToolCall.call_id == result.call_id))).scalar_one()
        assert call.status == ToolCallStatus.rejected
        res = (await fresh.execute(select(ToolResult).where(ToolResult.tool_call_id == call.id))).scalar_one_or_none()
        assert res is None, "rejected calls must have no ToolResult"


# --- 14: authorization -----------------------------------------------------

@pytest.mark.asyncio
async def test_unauthenticated_execution_is_rejected(session_maker):
    """No identity => no side effect, and nothing persisted claiming a
    tenant (ADR 001's principle)."""
    async with session_maker() as db:
        service = InMemoryTicketService()
        result = await _executor(db).execute(
            contract=CREATE_TICKET_CONTRACT, handler=build_create_ticket_handler(service),
            raw_args=VALID_ARGS, identity=None, session_uuid=uuid.uuid4(),
        )
        assert result.success is False
        assert "not authorized" in result.error
        assert service.create_call_count == 0

    async with session_maker() as fresh:
        assert (await fresh.execute(select(ToolCall))).scalars().all() == []


def test_authorize_rejects_missing_identity():
    with pytest.raises(AuthorizationError):
        authorize(CREATE_TICKET_CONTRACT, None)


# --- 15, 16: tenant / user override attempts -------------------------------

@pytest.mark.asyncio
async def test_model_cannot_override_tenant_or_user(session_maker):
    """The model supplies extra fields trying to redirect the ticket.
    Pydantic drops unknown fields, and tenant/user come from identity —
    so the attempt cannot even reach the service."""
    async with session_maker() as db:
        tenant, user, vs, identity = await _bootstrap(db, slug="tkt-real")
        attacker_tenant = uuid.uuid4()
        attacker_user = uuid.uuid4()

        service = InMemoryTicketService()
        result = await _executor(db).execute(
            contract=CREATE_TICKET_CONTRACT, handler=build_create_ticket_handler(service),
            raw_args={**VALID_ARGS, "tenant_id": str(attacker_tenant), "user_id": str(attacker_user)},
            identity=identity, session_uuid=vs.id,
        )
        assert result.success is True
        created = list(service.tickets.values())[0]
        assert created.tenant_id == tenant.id
        assert created.created_by_user_id == user.id

    async with session_maker() as fresh:
        call = (await fresh.execute(select(ToolCall).where(ToolCall.call_id == result.call_id))).scalar_one()
        assert call.tenant_id == tenant.id
        assert call.user_id == user.id
        assert str(attacker_tenant) not in str(call.arguments)
        assert str(attacker_user) not in str(call.arguments)


# --- 17: idempotency -------------------------------------------------------

@pytest.mark.asyncio
async def test_duplicate_request_does_not_create_second_ticket(session_maker):
    """THE milestone requirement: a retried create_ticket must not create
    two tickets. Proven at the provider level (call_count) and at the
    result level (same ticket_id returned)."""
    async with session_maker() as db:
        tenant, user, vs, identity = await _bootstrap(db, slug="tkt-idem")
        service = InMemoryTicketService()
        handler = build_create_ticket_handler(service)
        ex = _executor(db)

        first = await ex.execute(
            contract=CREATE_TICKET_CONTRACT, handler=handler, raw_args=VALID_ARGS,
            identity=identity, session_uuid=vs.id,
        )
        second = await ex.execute(
            contract=CREATE_TICKET_CONTRACT, handler=handler, raw_args=VALID_ARGS,
            identity=identity, session_uuid=vs.id,
        )

        assert first.success is True and second.success is True
        assert service.create_call_count == 1, "the ticket service must NOT be called twice"
        assert second.output["ticket_id"] == first.output["ticket_id"]
        assert second.output["deduplicated"] is True
        assert first.output.get("deduplicated") is False
        assert len(service.tickets) == 1


@pytest.mark.asyncio
async def test_reworded_description_still_deduplicates(session_maker):
    """Description is excluded from the key on purpose: an agent rewording
    free text between retries must not defeat suppression."""
    async with session_maker() as db:
        tenant, user, vs, identity = await _bootstrap(db, slug="tkt-reword")
        service = InMemoryTicketService()
        handler = build_create_ticket_handler(service)
        ex = _executor(db)

        await ex.execute(contract=CREATE_TICKET_CONTRACT, handler=handler,
                         raw_args=VALID_ARGS, identity=identity, session_uuid=vs.id)
        second = await ex.execute(
            contract=CREATE_TICKET_CONTRACT, handler=handler,
            raw_args={**VALID_ARGS, "description": "the pump sounds wrong, please check"},
            identity=identity, session_uuid=vs.id,
        )
        assert service.create_call_count == 1
        assert second.output["deduplicated"] is True


@pytest.mark.asyncio
async def test_different_site_is_not_deduplicated(session_maker):
    """Suppression must not over-reach: a genuinely different request
    still creates a ticket."""
    async with session_maker() as db:
        tenant, user, vs, identity = await _bootstrap(db, slug="tkt-distinct")
        service = InMemoryTicketService()
        handler = build_create_ticket_handler(service)
        ex = _executor(db)

        await ex.execute(contract=CREATE_TICKET_CONTRACT, handler=handler,
                         raw_args=VALID_ARGS, identity=identity, session_uuid=vs.id)
        await ex.execute(contract=CREATE_TICKET_CONTRACT, handler=handler,
                         raw_args={**VALID_ARGS, "site_id": "site-99"},
                         identity=identity, session_uuid=vs.id)
        assert service.create_call_count == 2
        assert len(service.tickets) == 2


@pytest.mark.asyncio
async def test_idempotency_key_is_scoped_to_tenant(session_maker):
    """Two tenants making byte-identical requests must NOT collide into
    one suppressed action."""
    async with session_maker() as db:
        _, _, vs_a, identity_a = await _bootstrap(db, slug="tkt-tenant-a")
        _, _, vs_b, identity_b = await _bootstrap(db, slug="tkt-tenant-b")
        args = CreateTicketInput(**VALID_ARGS)

        key_a = derive_idempotency_key(
            contract=CREATE_TICKET_CONTRACT, tenant_id=identity_a.tenant_id,
            session_id=vs_a.id, validated_args=args,
        )
        key_b = derive_idempotency_key(
            contract=CREATE_TICKET_CONTRACT, tenant_id=identity_b.tenant_id,
            session_id=vs_b.id, validated_args=args,
        )
        assert key_a != key_b


@pytest.mark.asyncio
async def test_failed_execution_does_not_block_a_genuine_retry(session_maker):
    """Only SUCCEEDED calls suppress. A prior failure must not permanently
    block retrying."""
    async with session_maker() as db:
        tenant, user, vs, identity = await _bootstrap(db, slug="tkt-retry-after-fail")
        failing = InMemoryTicketService(
            fail_with=TicketServiceError("upstream down", FailureClass.PROVIDER_ERROR)
        )
        ex = _executor(db)
        first = await ex.execute(
            contract=CREATE_TICKET_CONTRACT, handler=build_create_ticket_handler(failing),
            raw_args=VALID_ARGS, identity=identity, session_uuid=vs.id,
        )
        assert first.success is False

        working = InMemoryTicketService()
        second = await ex.execute(
            contract=CREATE_TICKET_CONTRACT, handler=build_create_ticket_handler(working),
            raw_args=VALID_ARGS, identity=identity, session_uuid=vs.id,
        )
        assert second.success is True, "a prior failure must not block a real retry"
        assert working.create_call_count == 1


# --- 11, 13, 19: failure path ----------------------------------------------

@pytest.mark.asyncio
async def test_failed_creation_never_fabricates_a_ticket_id(session_maker):
    async with session_maker() as db:
        tenant, user, vs, identity = await _bootstrap(db, slug="tkt-fail")
        service = InMemoryTicketService(
            fail_with=TicketServiceError("provider rejected", FailureClass.PROVIDER_ERROR)
        )
        result = await _executor(db).execute(
            contract=CREATE_TICKET_CONTRACT, handler=build_create_ticket_handler(service),
            raw_args=VALID_ARGS, identity=identity, session_uuid=vs.id,
        )
        assert result.success is False
        assert result.output["ticket_id"] is None

    async with session_maker() as fresh:
        call = (await fresh.execute(select(ToolCall).where(ToolCall.call_id == result.call_id))).scalar_one()
        assert call.status == ToolCallStatus.failed
        res = (await fresh.execute(select(ToolResult).where(ToolResult.tool_call_id == call.id))).scalar_one()
        assert res.success is False
        assert res.output["ticket_id"] is None      # 19: structured error
        assert "provider rejected" in res.output["message"]


# --- 12: timeout -----------------------------------------------------------

@pytest.mark.asyncio
async def test_timeout_is_recorded_as_timed_out_never_success(session_maker):
    async with session_maker() as db:
        tenant, user, vs, identity = await _bootstrap(db, slug="tkt-timeout")
        slow = InMemoryTicketService(delay_seconds=5)
        contract = type(CREATE_TICKET_CONTRACT)(
            **{**CREATE_TICKET_CONTRACT.__dict__, "timeout_seconds": 0.05}
        )
        result = await _executor(db).execute(
            contract=contract, handler=build_create_ticket_handler(slow),
            raw_args=VALID_ARGS, identity=identity, session_uuid=vs.id,
        )
        assert result.success is False
        assert "timed out" in result.error

    async with session_maker() as fresh:
        call = (await fresh.execute(select(ToolCall).where(ToolCall.call_id == result.call_id))).scalar_one()
        assert call.status == ToolCallStatus.timed_out
        res = (await fresh.execute(select(ToolResult).where(ToolResult.tool_call_id == call.id))).scalar_one()
        assert res.success is False


# --- 20: persistence failure -----------------------------------------------

@pytest.mark.asyncio
async def test_result_persistence_failure_does_not_corrupt_returned_result(session_maker):
    async with session_maker() as db:
        tenant, user, vs, identity = await _bootstrap(db, slug="tkt-persist-fail")

        class _BrokenRecorder(ToolCallRecorder):
            async def record_result(self, **kwargs):
                raise RuntimeError("simulated database outage")

        service = InMemoryTicketService()
        executor = ContractToolExecutor(db, _BrokenRecorder(db))
        result = await executor.execute(
            contract=CREATE_TICKET_CONTRACT, handler=build_create_ticket_handler(service),
            raw_args=VALID_ARGS, identity=identity, session_uuid=vs.id,
        )
        # The ticket really was created; the agent must be told the truth.
        assert result.success is True
        assert service.create_call_count == 1


# --- contract / retry policy ------------------------------------------------

def test_create_ticket_contract_classification():
    assert CREATE_TICKET_CONTRACT.side_effect is SideEffect.EXTERNAL_SIDE_EFFECT
    assert CREATE_TICKET_CONTRACT.idempotency is IdempotencyMode.KEYED
    assert "description" not in CREATE_TICKET_CONTRACT.idempotency_fields


def test_retry_policy_for_create_ticket():
    c = CREATE_TICKET_CONTRACT
    assert is_retry_safe(c, FailureClass.TRANSIENT) is True     # keyed => safe
    assert is_retry_safe(c, FailureClass.TIMEOUT) is True       # keyed => safe
    assert is_retry_safe(c, FailureClass.VALIDATION) is False
    assert is_retry_safe(c, FailureClass.AUTHORIZATION) is False
    assert is_retry_safe(c, FailureClass.BUSINESS_RULE) is False
    assert is_retry_safe(c, FailureClass.UNKNOWN) is False      # fail closed
