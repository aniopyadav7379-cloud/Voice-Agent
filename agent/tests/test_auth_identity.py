"""
Tests for authentication and trusted identity propagation. Real Postgres
(shared `session_maker` fixture from conftest.py) for anything that touches
the database; pure unit tests for JWT mechanics that don't need it.
"""
import os
import uuid

import pytest
from sqlalchemy import select

from app.db.models import Tenant, TenantStatus, ToolCall, User, UserStatus, VoiceSession, VoiceSessionStatus
from app.db.session_manager import AuditRecorder, ToolCallRecorder, VoiceSessionRecorder
from app.security.identity import (
    TenantInactiveError,
    TenantNotFoundError,
    UserInactiveError,
    UserNotFoundError,
    UserTenantMismatchError,
    resolve_identity,
    resolve_identity_by_ids,
)
from app.security.jwt_auth import (
    ExpiredTokenError,
    InvalidTokenError,
    MalformedTokenError,
    create_access_token,
    decode_access_token,
)
from app.security.livekit_identity import ParticipantIdentityError, mint_livekit_token, resolve_identity_from_participant
from app.tools.registry import ToolRegistry
from pydantic import BaseModel

os.environ.setdefault("JWT_SECRET", "test-secret-at-least-32-bytes-long-ok")


# ---------------------------------------------------------------------------
# 1-4: JWT mechanics — no database needed
# ---------------------------------------------------------------------------

def test_valid_token_round_trips():
    token = create_access_token(subject="alice", tenant_slug="acme")
    payload = decode_access_token(token)
    assert payload.sub == "alice"
    assert payload.tenant_slug == "acme"


def test_invalid_token_bad_signature_is_rejected():
    token = create_access_token(subject="alice", tenant_slug="acme")
    tampered = token[:-4] + "abcd"  # corrupt the signature portion
    with pytest.raises(InvalidTokenError):
        decode_access_token(tampered)


def test_expired_token_is_rejected():
    token = create_access_token(subject="alice", tenant_slug="acme", ttl_seconds=-10)
    with pytest.raises(ExpiredTokenError):
        decode_access_token(token)


def test_malformed_token_missing_claims_is_rejected():
    import jwt as pyjwt

    # Cryptographically valid, but missing the required `tenant_slug` claim.
    bad_token = pyjwt.encode(
        {"sub": "alice", "iat": 0, "exp": 9999999999, "iss": "voice-agent-platform"},
        os.environ["JWT_SECRET"], algorithm="HS256",
    )
    with pytest.raises(MalformedTokenError):
        decode_access_token(bad_token)


# ---------------------------------------------------------------------------
# 5-9: identity resolution rejection reasons — real Postgres
# ---------------------------------------------------------------------------

async def _make_tenant(db, *, slug: str, status: TenantStatus = TenantStatus.active) -> Tenant:
    tenant = Tenant(slug=slug, name=slug, status=status)
    db.add(tenant)
    await db.flush()
    return tenant


async def _make_user(db, *, tenant_id, external_id: str, status: UserStatus = UserStatus.active) -> User:
    user = User(tenant_id=tenant_id, external_id=external_id, status=status)
    db.add(user)
    await db.flush()
    return user


@pytest.mark.asyncio
async def test_nonexistent_tenant_is_rejected(session_maker):
    async with session_maker() as db:
        with pytest.raises(TenantNotFoundError):
            await resolve_identity(db, external_id="alice", tenant_slug="no-such-tenant")


@pytest.mark.asyncio
async def test_inactive_tenant_is_rejected(session_maker):
    async with session_maker() as db:
        tenant = await _make_tenant(db, slug="suspended-co", status=TenantStatus.suspended)
        await _make_user(db, tenant_id=tenant.id, external_id="alice")
        await db.commit()

        with pytest.raises(TenantInactiveError):
            await resolve_identity(db, external_id="alice", tenant_slug="suspended-co")


@pytest.mark.asyncio
async def test_nonexistent_user_is_rejected(session_maker):
    async with session_maker() as db:
        await _make_tenant(db, slug="acme-nouser")
        await db.commit()

        with pytest.raises(UserNotFoundError):
            await resolve_identity(db, external_id="ghost", tenant_slug="acme-nouser")


@pytest.mark.asyncio
async def test_inactive_user_is_rejected(session_maker):
    async with session_maker() as db:
        tenant = await _make_tenant(db, slug="acme-inactive-user")
        await _make_user(db, tenant_id=tenant.id, external_id="bob", status=UserStatus.disabled)
        await db.commit()

        with pytest.raises(UserInactiveError):
            await resolve_identity(db, external_id="bob", tenant_slug="acme-inactive-user")


@pytest.mark.asyncio
async def test_user_tenant_mismatch_is_rejected(session_maker):
    """A real user_id and a real tenant_id, both individually valid and
    active, but the user doesn't belong to that tenant."""
    async with session_maker() as db:
        tenant_a = await _make_tenant(db, slug="mismatch-a")
        tenant_b = await _make_tenant(db, slug="mismatch-b")
        user = await _make_user(db, tenant_id=tenant_a.id, external_id="carol")
        await db.commit()

        with pytest.raises(UserTenantMismatchError):
            await resolve_identity_by_ids(db, user_id=user.id, tenant_id=tenant_b.id)


@pytest.mark.asyncio
async def test_valid_identity_resolves_successfully(session_maker):
    async with session_maker() as db:
        tenant = await _make_tenant(db, slug="acme-valid")
        user = await _make_user(db, tenant_id=tenant.id, external_id="dave")
        await db.commit()

        identity = await resolve_identity(db, external_id="dave", tenant_slug="acme-valid")
        assert identity.user_id == user.id
        assert identity.tenant_id == tenant.id
        assert identity.tenant_slug == "acme-valid"


# ---------------------------------------------------------------------------
# LiveKit identity mapping (10-ish: the mint -> participant -> resolve chain)
# ---------------------------------------------------------------------------

class _FakeParticipant:
    """Stands in for rtc.Participant — carries exactly the two real fields
    (identity, attributes) resolve_identity_from_participant reads, shaped
    exactly as mint_livekit_token would actually produce them (verified
    against the real SDK separately — see livekit_identity.py's own
    verification notes)."""

    def __init__(self, identity: str, attributes: dict):
        self.identity = identity
        self.attributes = attributes


@pytest.mark.asyncio
async def test_livekit_participant_identity_resolves_to_same_authenticated_identity(session_maker):
    async with session_maker() as db:
        tenant = await _make_tenant(db, slug="lk-tenant")
        user = await _make_user(db, tenant_id=tenant.id, external_id="erin")
        await db.commit()

        from app.security.identity import AuthenticatedIdentity

        original = AuthenticatedIdentity(
            user_id=user.id, tenant_id=tenant.id, tenant_slug="lk-tenant", external_id="erin",
        )
        token = mint_livekit_token(
            identity=original, room_name="room-1", api_key="fake-key", api_secret="fake-secret-32-bytes-long-enough-ok",
        )

        # Decode the real token (unverified — just to build the fake
        # participant the way a real LiveKit server would present it to
        # the agent after actually verifying the signature itself).
        import jwt as pyjwt

        claims = pyjwt.decode(token, options={"verify_signature": False})
        participant = _FakeParticipant(identity=claims["sub"], attributes=claims["attributes"])

        resolved = await resolve_identity_from_participant(db, participant)
        assert resolved.user_id == user.id
        assert resolved.tenant_id == tenant.id


@pytest.mark.asyncio
async def test_livekit_participant_with_malformed_identity_is_rejected(session_maker):
    async with session_maker() as db:
        participant = _FakeParticipant(identity="not-a-uuid", attributes={"tenant_id": str(uuid.uuid4())})
        with pytest.raises(ParticipantIdentityError):
            await resolve_identity_from_participant(db, participant)


@pytest.mark.asyncio
async def test_livekit_participant_for_deactivated_user_is_rejected(session_maker):
    """Proves the 'still active NOW' re-check documented in
    resolve_identity_by_ids: a token minted while the user was active, but
    the user has since been deactivated in Postgres."""
    async with session_maker() as db:
        tenant = await _make_tenant(db, slug="lk-deactivated")
        user = await _make_user(db, tenant_id=tenant.id, external_id="frank")
        await db.commit()

        participant = _FakeParticipant(identity=str(user.id), attributes={"tenant_id": str(tenant.id)})

        # Deactivate AFTER the (hypothetical) token was minted.
        user.status = UserStatus.disabled
        await db.commit()

        with pytest.raises(UserInactiveError):
            await resolve_identity_from_participant(db, participant)


# ---------------------------------------------------------------------------
# Dev token endpoint gating (18)
# ---------------------------------------------------------------------------

def test_dev_token_endpoint_returns_404_outside_development(monkeypatch):
    monkeypatch.delenv("ENVIRONMENT", raising=False)
    from starlette.testclient import TestClient
    from app.security.token_service import app as token_app

    client = TestClient(token_app)
    resp = client.post("/v1/dev/token", json={"tenant_slug": "x", "external_id": "y", "room_name": "z"})
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_dev_token_endpoint_works_in_development(session_maker, monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "development")
    monkeypatch.setenv("LIVEKIT_API_KEY", "fake-lk-key")
    monkeypatch.setenv("LIVEKIT_API_SECRET", "fake-lk-secret-32-bytes-long-enough")

    # Point the token service's own session-maker singleton at the same
    # dedicated test database this test file otherwise uses directly.
    import app.db.base as db_base
    from tests.conftest import test_database_url

    db_base._engine = db_base.build_engine(test_database_url())
    db_base._session_maker = db_base.build_session_maker(db_base._engine)

    from starlette.testclient import TestClient
    from app.security.token_service import app as token_app

    client = TestClient(token_app)
    resp = client.post(
        "/v1/dev/token", json={"tenant_slug": "dev-flow-tenant", "external_id": "grace", "room_name": "dev-room"}
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["access_token"]
    assert body["livekit_token"]
    assert uuid.UUID(body["tenant_id"])
    assert uuid.UUID(body["user_id"])

    # Reset the singleton so later tests in this file get a fresh engine
    # bound to the fixture's own event loop instead of this test's.
    db_base._engine = None
    db_base._session_maker = None


# ---------------------------------------------------------------------------
# Tool-identity propagation + non-overridability (10-16, 19-20)
# ---------------------------------------------------------------------------

class _EchoArgs(BaseModel):
    message: str


async def _echo_handler(args, tenant_id, session_id):
    return {"echoed": args.message}


@pytest.mark.asyncio
async def test_authenticated_user_id_propagates_to_toolcall(session_maker):
    async with session_maker() as db:
        tenant = await _make_tenant(db, slug="propagate-tenant")
        user = await _make_user(db, tenant_id=tenant.id, external_id="henry")
        vs = VoiceSession(tenant_id=tenant.id, user_id=user.id, current_language="en-IN", status=VoiceSessionStatus.active)
        db.add(vs)
        await db.flush()
        await db.commit()

        registry = ToolRegistry()
        registry.register("echo", _EchoArgs, _echo_handler)
        recorder = ToolCallRecorder(db)

        result = await registry.execute(
            tool_name="echo", raw_args={"message": "hi"}, tenant_id=str(tenant.id), session_id=str(vs.id),
            db_recorder=recorder, tenant_uuid=tenant.id, session_uuid=vs.id, user_uuid=user.id,
        )

    async with session_maker() as fresh_db:
        call_row = (await fresh_db.execute(select(ToolCall).where(ToolCall.call_id == result.call_id))).scalar_one()
        assert call_row.user_id == user.id, "ToolCall.user_id must reflect the authenticated user"
        assert call_row.tenant_id == tenant.id


@pytest.mark.asyncio
async def test_session_ownership_recorded_on_voicesession(session_maker):
    async with session_maker() as db:
        tenant = await _make_tenant(db, slug="ownership-tenant")
        user = await _make_user(db, tenant_id=tenant.id, external_id="iris")
        await db.commit()

        recorder = VoiceSessionRecorder(db)
        vs = await recorder.start_session(tenant_id=tenant.id, user_id=user.id)
        await db.commit()
        session_id = vs.id

    async with session_maker() as fresh_db:
        row = (await fresh_db.execute(select(VoiceSession).where(VoiceSession.id == session_id))).scalar_one()
        assert row.user_id == user.id


@pytest.mark.asyncio
async def test_tool_arguments_cannot_override_authenticated_tenant_or_user(session_maker):
    """Structural proof, not a behavioral guess: registry.execute() never
    reads tenant_id/user_id from raw_args at all — there is no code path
    by which a model-supplied argument could reach ToolCall.tenant_id or
    ToolCall.user_id. This test calls with a raw_args payload that TRIES
    to smuggle a different tenant/user, and confirms the persisted row
    still reflects only the trusted execution context."""

    class _ArgsWithSneakyFields(BaseModel):
        message: str
        tenant_id: str | None = None  # a real field an LLM could be tricked into filling
        user_id: str | None = None

    async def _handler(args, tenant_id, session_id):
        return {"echoed": args.message}

    async with session_maker() as db:
        real_tenant = await _make_tenant(db, slug="real-tenant")
        real_user = await _make_user(db, tenant_id=real_tenant.id, external_id="real-user")
        attacker_tenant = await _make_tenant(db, slug="attacker-tenant")
        attacker_user = await _make_user(db, tenant_id=attacker_tenant.id, external_id="attacker-user")
        vs = VoiceSession(tenant_id=real_tenant.id, user_id=real_user.id, current_language="en-IN", status=VoiceSessionStatus.active)
        db.add(vs)
        await db.flush()
        await db.commit()

        registry = ToolRegistry()
        registry.register("sneaky", _ArgsWithSneakyFields, _handler)
        recorder = ToolCallRecorder(db)

        result = await registry.execute(
            tool_name="sneaky",
            raw_args={"message": "hi", "tenant_id": str(attacker_tenant.id), "user_id": str(attacker_user.id)},
            tenant_id=str(real_tenant.id), session_id=str(vs.id),
            db_recorder=recorder, tenant_uuid=real_tenant.id, session_uuid=vs.id, user_uuid=real_user.id,
        )
        assert result.success is True

    async with session_maker() as fresh_db:
        call_row = (await fresh_db.execute(select(ToolCall).where(ToolCall.call_id == result.call_id))).scalar_one()
        assert call_row.tenant_id == real_tenant.id, "tenant_id must come from trusted context, not raw_args"
        assert call_row.user_id == real_user.id, "user_id must come from trusted context, not raw_args"
        assert str(call_row.tenant_id) != str(attacker_tenant.id)
        assert str(call_row.user_id) != str(attacker_user.id)


@pytest.mark.asyncio
async def test_audit_log_records_identity_for_rejected_auth_attempt(session_maker):
    async with session_maker() as db:
        tenant = await _make_tenant(db, slug="audit-identity-tenant", status=TenantStatus.suspended)
        await db.commit()

        recorder = AuditRecorder(db)
        entry = await recorder.record(
            tenant_id=tenant.id, user_id=None, action="auth_rejected",
            resource_type="session", resource_id="room-xyz", metadata={"reason": "TenantInactiveError"},
        )
        assert entry.tenant_id == tenant.id
        assert entry.action == "auth_rejected"

    async with session_maker() as fresh_db:
        from app.db.models import AuditLog

        row = (await fresh_db.execute(select(AuditLog).where(AuditLog.tenant_id == tenant.id))).scalar_one()
        assert row.resource_id == "room-xyz"
        assert row.event_metadata["reason"] == "TenantInactiveError"


@pytest.mark.asyncio
async def test_cross_tenant_toolcalls_still_isolated_with_user_id_present(session_maker):
    """Re-proves session 6's tenant-isolation guarantee still holds now
    that user_id flows through the same path."""
    async with session_maker() as db:
        tenant_a = await _make_tenant(db, slug="xtenant-a")
        user_a = await _make_user(db, tenant_id=tenant_a.id, external_id="user-a")
        tenant_b = await _make_tenant(db, slug="xtenant-b")
        user_b = await _make_user(db, tenant_id=tenant_b.id, external_id="user-b")
        vs_a = VoiceSession(tenant_id=tenant_a.id, user_id=user_a.id, current_language="en-IN", status=VoiceSessionStatus.active)
        vs_b = VoiceSession(tenant_id=tenant_b.id, user_id=user_b.id, current_language="en-IN", status=VoiceSessionStatus.active)
        db.add_all([vs_a, vs_b])
        await db.flush()
        await db.commit()

        registry = ToolRegistry()
        registry.register("echo", _EchoArgs, _echo_handler)

        await registry.execute(
            tool_name="echo", raw_args={"message": "a's data"}, tenant_id=str(tenant_a.id), session_id=str(vs_a.id),
            db_recorder=ToolCallRecorder(db), tenant_uuid=tenant_a.id, session_uuid=vs_a.id, user_uuid=user_a.id,
        )
        await registry.execute(
            tool_name="echo", raw_args={"message": "b's secret"}, tenant_id=str(tenant_b.id), session_id=str(vs_b.id),
            db_recorder=ToolCallRecorder(db), tenant_uuid=tenant_b.id, session_uuid=vs_b.id, user_uuid=user_b.id,
        )

    async with session_maker() as fresh_db:
        result = await fresh_db.execute(select(ToolCall).where(ToolCall.tenant_id == tenant_a.id))
        calls_for_a = result.scalars().all()
        assert len(calls_for_a) == 1
        assert calls_for_a[0].user_id == user_a.id
        assert not any("b's secret" in str(c.arguments) for c in calls_for_a)


# ---------------------------------------------------------------------------
# Production token exchange (/v1/livekit/token) — available in ALL environments
# ---------------------------------------------------------------------------

def _prod_client(monkeypatch, session_maker):
    monkeypatch.setenv("LIVEKIT_API_KEY", "fake-lk-key")
    monkeypatch.setenv("LIVEKIT_API_SECRET", "fake-lk-secret-32-bytes-long-enough")
    import app.db.base as db_base
    from tests.conftest import test_database_url

    db_base._engine = db_base.build_engine(test_database_url())
    db_base._session_maker = db_base.build_session_maker(db_base._engine)
    from starlette.testclient import TestClient
    from app.security.token_service import app as token_app

    return TestClient(token_app)


@pytest.mark.asyncio
async def test_production_token_exchange_succeeds_for_valid_identity(session_maker, monkeypatch):
    async with session_maker() as db:
        tenant = await _make_tenant(db, slug="prod-exchange")
        await _make_user(db, tenant_id=tenant.id, external_id="prod-user")
        await db.commit()

    token = create_access_token(subject="prod-user", tenant_slug="prod-exchange")
    client = _prod_client(monkeypatch, session_maker)
    resp = client.post(
        "/v1/livekit/token", json={"room_name": "prod-room"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["livekit_token"]

    import app.db.base as db_base
    db_base._engine = None
    db_base._session_maker = None


@pytest.mark.asyncio
async def test_production_token_exchange_rejects_expired_token(session_maker, monkeypatch):
    """Expiry is ENFORCED at the exchange boundary, not advisory."""
    async with session_maker() as db:
        tenant = await _make_tenant(db, slug="prod-expired")
        await _make_user(db, tenant_id=tenant.id, external_id="expired-user")
        await db.commit()

    token = create_access_token(subject="expired-user", tenant_slug="prod-expired", ttl_seconds=-10)
    client = _prod_client(monkeypatch, session_maker)
    resp = client.post(
        "/v1/livekit/token", json={"room_name": "r"}, headers={"Authorization": f"Bearer {token}"}
    )
    assert resp.status_code == 401
    assert "Invalid token" in resp.text

    import app.db.base as db_base
    db_base._engine = None
    db_base._session_maker = None


@pytest.mark.asyncio
async def test_production_token_exchange_rejects_deactivated_user(session_maker, monkeypatch):
    """Security invariant 6 in action: a perfectly valid signature is NOT
    sufficient. The user was deactivated after the token was minted."""
    async with session_maker() as db:
        tenant = await _make_tenant(db, slug="prod-deactivated")
        user = await _make_user(db, tenant_id=tenant.id, external_id="soon-disabled")
        await db.commit()
        token = create_access_token(subject="soon-disabled", tenant_slug="prod-deactivated")
        user.status = UserStatus.disabled
        await db.commit()

    client = _prod_client(monkeypatch, session_maker)
    resp = client.post(
        "/v1/livekit/token", json={"room_name": "r"}, headers={"Authorization": f"Bearer {token}"}
    )
    assert resp.status_code == 403

    import app.db.base as db_base
    db_base._engine = None
    db_base._session_maker = None


def test_production_token_exchange_requires_bearer_header(monkeypatch):
    monkeypatch.setenv("LIVEKIT_API_KEY", "fake-lk-key")
    monkeypatch.setenv("LIVEKIT_API_SECRET", "fake-lk-secret-32-bytes-long-enough")
    from starlette.testclient import TestClient
    from app.security.token_service import app as token_app

    client = TestClient(token_app)
    resp = client.post("/v1/livekit/token", json={"room_name": "r"}, headers={"Authorization": "NotBearer x"})
    assert resp.status_code == 401
