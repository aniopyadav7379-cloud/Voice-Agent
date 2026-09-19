"""
Tests for the session boundary (milestone question A) and the audit
tenant_id decision (question B, docs/adr/001-audit-tenant-id.md).

The `wait_for_participant_fn` seam in `establish_authenticated_session()`
exists precisely so these lifecycle cases can be tested without a live
LiveKit room. What is being tested here is OUR lifecycle logic — the
timeout, the routing, the fail-closed behavior — not LiveKit's own
`wait_for_participant()`, whose contract was established by reading its
real source (see session_boundary.py's docstring). Nothing here is, or
claims to be, live-room verification.
"""
import asyncio
import uuid

import pytest
from sqlalchemy import select

from app.db.models import (
    AuditLog,
    SecurityEvent,
    Tenant,
    TenantStatus,
    User,
    UserStatus,
)
from app.db.session_manager import AuditRecorder, SecurityEventRecorder, is_tenant_verified
from app.security.identity import (
    IdentitySource,
    TenantInactiveError,
    TenantNotFoundError,
    UserInactiveError,
    UserNotFoundError,
    UserTenantMismatchError,
    resolve_identity,
)
from app.security.livekit_identity import ParticipantIdentityError
from app.security.session_boundary import (
    AuthenticationRejectedError,
    NoParticipantError,
    RoomDisconnectedError,
    establish_authenticated_session,
)


class _FakeParticipant:
    """Carries exactly the two real `rtc.Participant` fields the boundary
    reads, shaped as `mint_livekit_token()` actually produces them."""

    def __init__(self, identity: str, attributes: dict):
        self.identity = identity
        self.attributes = attributes


async def _make_tenant(db, *, slug, status=TenantStatus.active):
    t = Tenant(slug=slug, name=slug, status=status)
    db.add(t)
    await db.flush()
    return t


async def _make_user(db, *, tenant_id, external_id, status=UserStatus.active):
    u = User(tenant_id=tenant_id, external_id=external_id, status=status)
    db.add(u)
    await db.flush()
    return u


# ---------------------------------------------------------------------------
# A. Session-boundary lifecycle
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_participant_arrives_normally_and_authenticates(session_maker):
    """Case 1 + 2 + 8: normal arrival, valid identity, authenticated
    session becomes available."""
    async with session_maker() as db:
        tenant = await _make_tenant(db, slug="boundary-ok")
        user = await _make_user(db, tenant_id=tenant.id, external_id="alice")
        await db.commit()

        participant = _FakeParticipant(str(user.id), {"tenant_id": str(tenant.id)})

        async def _wait():
            return participant

        session = await establish_authenticated_session(
            room=None, db=db, wait_for_participant_fn=_wait, timeout_seconds=5,
        )
        assert session.identity.user_id == user.id
        assert session.identity.tenant_id == tenant.id
        assert session.identity.source == IdentitySource.LIVEKIT_PARTICIPANT
        assert session.participant is participant


@pytest.mark.asyncio
async def test_no_participant_within_timeout_raises_rather_than_hanging(session_maker):
    """Case 7 — and the real defect this module was written to fix.
    `wait_for_participant()` has NO timeout of its own (verified by
    reading its source): without this wrapper a room nobody joins would
    hold a DB session and worker slot forever."""
    async with session_maker() as db:

        async def _never_returns():
            await asyncio.sleep(3600)

        with pytest.raises(NoParticipantError) as exc:
            await establish_authenticated_session(
                room=None, db=db, wait_for_participant_fn=_never_returns, timeout_seconds=0.05,
            )
        assert exc.value.reason == "no_participant"


@pytest.mark.asyncio
async def test_room_disconnect_during_initialization_is_surfaced(session_maker):
    """Case 6: the SDK raises RuntimeError("room disconnected while
    waiting for participant") — verified as its real, documented failure
    mode by reading the source. Confirm we translate it, not swallow it."""
    async with session_maker() as db:

        async def _disconnects():
            raise RuntimeError("room disconnected while waiting for participant")

        with pytest.raises(RoomDisconnectedError) as exc:
            await establish_authenticated_session(
                room=None, db=db, wait_for_participant_fn=_disconnects, timeout_seconds=5,
            )
        assert exc.value.reason == "room_disconnected"


@pytest.mark.asyncio
async def test_malformed_participant_attributes_are_rejected(session_maker):
    """Case 4: attributes present but unusable."""
    async with session_maker() as db:

        async def _wait():
            return _FakeParticipant(str(uuid.uuid4()), {})  # missing tenant_id

        with pytest.raises(AuthenticationRejectedError) as exc:
            await establish_authenticated_session(
                room=None, db=db, wait_for_participant_fn=_wait, timeout_seconds=5,
            )
        assert isinstance(exc.value.cause, ParticipantIdentityError)


@pytest.mark.asyncio
async def test_invalid_participant_identity_is_rejected(session_maker):
    """Case 3: identity string isn't even a UUID."""
    async with session_maker() as db:

        async def _wait():
            return _FakeParticipant("definitely-not-a-uuid", {"tenant_id": str(uuid.uuid4())})

        with pytest.raises(AuthenticationRejectedError):
            await establish_authenticated_session(
                room=None, db=db, wait_for_participant_fn=_wait, timeout_seconds=5,
            )


@pytest.mark.asyncio
async def test_participant_belonging_to_another_tenant_is_rejected(session_maker):
    """Case 5: real user, real tenant, but the user isn't in that tenant."""
    async with session_maker() as db:
        tenant_a = await _make_tenant(db, slug="boundary-a")
        tenant_b = await _make_tenant(db, slug="boundary-b")
        user_a = await _make_user(db, tenant_id=tenant_a.id, external_id="bob")
        await db.commit()

        async def _wait():
            return _FakeParticipant(str(user_a.id), {"tenant_id": str(tenant_b.id)})

        with pytest.raises(AuthenticationRejectedError) as exc:
            await establish_authenticated_session(
                room=None, db=db, wait_for_participant_fn=_wait, timeout_seconds=5,
            )
        assert isinstance(exc.value.cause, UserTenantMismatchError)


@pytest.mark.asyncio
async def test_no_authenticated_session_means_no_identity_for_tools(session_maker):
    """Case 9, tested structurally rather than behaviorally: on any
    boundary failure, `establish_authenticated_session` raises instead of
    returning, so there is NO `AuthenticatedSession` object in existence —
    and `agent_entrypoint.py` cannot construct `FieldOpsAssistant` (which
    owns the tool registry) without one. The tool layer is unreachable,
    not merely un-called."""
    async with session_maker() as db:

        async def _wait():
            return _FakeParticipant("bad", {})

        result = None
        try:
            result = await establish_authenticated_session(
                room=None, db=db, wait_for_participant_fn=_wait, timeout_seconds=5,
            )
        except AuthenticationRejectedError:
            pass
        assert result is None, "no AuthenticatedSession may exist after a failed boundary"


# ---------------------------------------------------------------------------
# B. Audit routing (ADR 001)
# ---------------------------------------------------------------------------

def test_routing_rule_matches_adr_exactly():
    """The ADR's routing table, asserted directly. An allow-list, so a
    future exception type defaults to the SAFE side."""
    assert is_tenant_verified(TenantInactiveError("x")) is True
    assert is_tenant_verified(UserNotFoundError("x")) is True
    assert is_tenant_verified(UserInactiveError("x")) is True
    assert is_tenant_verified(UserTenantMismatchError("x")) is True
    # Never verified a tenant -> must NOT be allowed to claim one.
    assert is_tenant_verified(TenantNotFoundError("x")) is False
    assert is_tenant_verified(ParticipantIdentityError("x")) is False


@pytest.mark.asyncio
async def test_tenant_verified_rejections_carry_the_verified_tenant_id(session_maker):
    """Proves the id attached to a rejection is the one READ FROM
    POSTGRES, not echoed from client input — which is what makes writing
    it to the tenant-scoped audit table legitimate."""
    async with session_maker() as db:
        tenant = await _make_tenant(db, slug="verified-id-tenant")
        await db.commit()

        with pytest.raises(UserNotFoundError) as exc:
            await resolve_identity(db, external_id="ghost", tenant_slug="verified-id-tenant")
        assert exc.value.verified_tenant_id == tenant.id


@pytest.mark.asyncio
async def test_unverified_tenant_rejection_carries_no_tenant_id(session_maker):
    """The whole point of ADR 001: when no tenant was verified, there is
    no tenant id to attach, so nothing downstream can claim one."""
    async with session_maker() as db:
        with pytest.raises(TenantNotFoundError) as exc:
            await resolve_identity(db, external_id="anyone", tenant_slug="no-such-tenant-at-all")
        assert exc.value.verified_tenant_id is None


@pytest.mark.asyncio
async def test_security_event_table_cannot_claim_a_tenant(session_maker):
    """Structural proof of the ADR's invariant: `SecurityEvent` has no
    tenant_id column and no FK to tenants at all. `claimed_*` fields are
    plain strings recording an assertion, not a relationship."""
    async with session_maker() as db:
        recorder = SecurityEventRecorder(db)
        event = await recorder.record(
            action="auth_rejected", reason="TenantNotFoundError", resource_type="session",
            resource_id="room-1", claimed_tenant_slug="attacker-claimed-tenant",
        )
        assert not hasattr(event, "tenant_id"), "SecurityEvent must not have a tenant_id field"

    async with session_maker() as fresh:
        row = (await fresh.execute(select(SecurityEvent))).scalar_one()
        assert row.claimed_tenant_slug == "attacker-claimed-tenant"
        assert row.reason == "TenantNotFoundError"


@pytest.mark.asyncio
async def test_audit_log_still_requires_a_real_tenant(session_maker):
    """AuditLog is unchanged by this milestone: still NOT NULL, still
    FK-constrained. Writing a nonexistent tenant must fail at the
    database level, not merely by convention."""
    async with session_maker() as db:
        recorder = AuditRecorder(db)
        with pytest.raises(Exception):
            await recorder.record(
                tenant_id=uuid.uuid4(), user_id=None, action="auth_rejected",
                resource_type="session", resource_id="room-x",
            )
        await db.rollback()


@pytest.mark.asyncio
async def test_inactive_tenant_rejection_is_auditable_to_audit_logs(session_maker):
    """TenantInactiveError sits on the audit_logs side of the ADR's
    routing table: the tenant row WAS read, so claiming it is verified."""
    async with session_maker() as db:
        tenant = await _make_tenant(db, slug="inactive-auditable", status=TenantStatus.suspended)
        await _make_user(db, tenant_id=tenant.id, external_id="carol")
        await db.commit()

        with pytest.raises(TenantInactiveError) as exc:
            await resolve_identity(db, external_id="carol", tenant_slug="inactive-auditable")

        assert is_tenant_verified(exc.value) is True
        await AuditRecorder(db).record(
            tenant_id=exc.value.verified_tenant_id, user_id=None, action="auth_rejected",
            resource_type="session", resource_id="room-y", metadata={"reason": "TenantInactiveError"},
        )

    async with session_maker() as fresh:
        row = (await fresh.execute(select(AuditLog))).scalar_one()
        assert row.tenant_id == tenant.id
        assert row.event_metadata["reason"] == "TenantInactiveError"


# ---------------------------------------------------------------------------
# Identity model
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_app_jwt_and_livekit_paths_produce_the_same_identity_type(session_maker):
    """Both paths converge on one canonical object, differing only in the
    explicitly-recorded `source`."""
    from app.security.identity import resolve_identity_by_ids

    async with session_maker() as db:
        tenant = await _make_tenant(db, slug="converge-tenant")
        user = await _make_user(db, tenant_id=tenant.id, external_id="dave")
        await db.commit()

        via_jwt = await resolve_identity(db, external_id="dave", tenant_slug="converge-tenant")
        via_lk = await resolve_identity_by_ids(db, user_id=user.id, tenant_id=tenant.id)

        assert type(via_jwt) is type(via_lk)
        assert via_jwt.user_id == via_lk.user_id
        assert via_jwt.tenant_id == via_lk.tenant_id
        assert via_jwt.source == IdentitySource.APP_JWT
        assert via_lk.source == IdentitySource.LIVEKIT_PARTICIPANT


def test_authenticated_identity_is_immutable():
    """Frozen: nothing downstream may mutate an established identity."""
    from app.security.identity import AuthenticatedIdentity
    import dataclasses

    identity = AuthenticatedIdentity(
        user_id=uuid.uuid4(), tenant_id=uuid.uuid4(), tenant_slug="t", external_id="e",
    )
    with pytest.raises(dataclasses.FrozenInstanceError):
        identity.tenant_id = uuid.uuid4()  # type: ignore[misc]

    session_id = uuid.uuid4()
    bound = identity.with_session(session_id)
    assert bound.session_id == session_id
    assert identity.session_id is None, "with_session must not mutate the original"
