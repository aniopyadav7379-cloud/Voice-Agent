"""
Turns a decoded JWT's claims (or, on the LiveKit path, a trusted
participant's identity/attributes — see livekit_identity.py) into a
validated `AuthenticatedIdentity`, backed by real Postgres lookups. A
cryptographically valid token is necessary but not sufficient: this module
is what actually implements the spec's 6-step validation chain (validate
token -> extract identity -> resolve user -> resolve tenant -> verify user
belongs to tenant -> establish context) for steps 3-5.

Every rejection reason gets its own exception type, deliberately, so a
caller (or a test) can tell "tenant doesn't exist" apart from "tenant
exists but is suspended" apart from "user exists but belongs to a
different tenant" — collapsing these into one generic 403 would satisfy
the letter of "reject invalid identities" while losing exactly the
distinctions a real security review would want in the logs/tests.
"""
import enum
import uuid
from dataclasses import dataclass, replace
from typing import TypeVar

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Tenant, TenantStatus, User, UserStatus


class IdentitySource(str, enum.Enum):
    """How an `AuthenticatedIdentity` was established. Recorded explicitly
    because the two paths have genuinely different trust derivations — see
    the class docstring below — and a reader of a session's logs or audit
    trail should not have to infer which one was used."""

    APP_JWT = "app_jwt"
    """Resolved from this application's own JWT: signature verified here,
    then `sub`/`tenant_slug` claims re-validated against Postgres."""

    LIVEKIT_PARTICIPANT = "livekit_participant"
    """Resolved from a LiveKit participant's identity/attributes. The
    token's signature was verified by LiveKit's own server before the
    participant could join; this application re-validates the referenced
    user/tenant against Postgres but does not re-verify that signature."""


@dataclass(frozen=True)
class AuthenticatedIdentity:
    """THE canonical identity object. Both authentication paths converge
    here; nothing downstream needs to know which one produced it.

        app JWT ──> decode_access_token() ──> resolve_identity() ─────┐
                    (signature verified)      (Postgres validated)    │
                                                                      ├──> AuthenticatedIdentity
        LiveKit ──> participant.identity  ──> resolve_identity_by_ids()┘
        token       + .attributes             (Postgres validated)
        (verified by LiveKit's server)

    Frozen deliberately: once established at the session boundary, an
    identity must not be mutated by anything downstream. `session_id` is
    optional because identity is established BEFORE a VoiceSession row
    exists (the session is created using this identity, not the reverse);
    `with_session()` returns a new instance once that row exists, rather
    than mutating this one.
    """

    user_id: uuid.UUID
    tenant_id: uuid.UUID
    tenant_slug: str
    external_id: str
    source: IdentitySource = IdentitySource.APP_JWT
    session_id: uuid.UUID | None = None

    def with_session(self, session_id: uuid.UUID) -> "AuthenticatedIdentity":
        return replace(self, session_id=session_id)


class IdentityResolutionError(Exception):
    """Base for every reason identity resolution can be rejected.

    `verified_tenant_id` is set ONLY by rejection paths that successfully
    read a real tenant row from Postgres before rejecting. It is the
    verified row's primary key — never a value echoed back from client
    input. Paths that never verified a tenant leave it None, which is
    what routes them to `security_events` instead of `audit_logs` (see
    docs/adr/001-audit-tenant-id.md and
    session_manager.is_tenant_verified).
    """

    verified_tenant_id: "uuid.UUID | None" = None


class TenantNotFoundError(IdentityResolutionError):
    pass


class TenantInactiveError(IdentityResolutionError):
    pass


class UserNotFoundError(IdentityResolutionError):
    pass


class UserInactiveError(IdentityResolutionError):
    pass


class UserTenantMismatchError(IdentityResolutionError):
    """The user row exists and is active, and the tenant row exists and is
    active, but the user does not actually belong to the claimed tenant.
    Distinct from UserNotFoundError because the failure mode is different:
    this is someone (or some token) trying to claim tenant membership they
    don't have, not a simple lookup miss."""


_E = TypeVar("_E", bound=IdentityResolutionError)


def _with_verified_tenant(error: _E, tenant_id: uuid.UUID) -> _E:
    """Attaches the VERIFIED tenant's primary key to a rejection, so
    downstream audit routing can tell this rejection apart from one where
    no tenant was ever confirmed. Typed generically so each call site
    keeps its own exception type (mypy flagged a real issue when these
    were assigned through one shared `err` variable)."""
    error.verified_tenant_id = tenant_id
    return error



async def resolve_identity(db: AsyncSession, *, external_id: str, tenant_slug: str) -> AuthenticatedIdentity:
    """The JWT path: `external_id`/`tenant_slug` come from a decoded,
    signature-verified `TokenPayload` (see jwt_auth.py) — never from
    unauthenticated request data."""
    tenant_result = await db.execute(select(Tenant).where(Tenant.slug == tenant_slug))
    tenant = tenant_result.scalar_one_or_none()
    if tenant is None:
        raise TenantNotFoundError(f"no tenant with slug '{tenant_slug}'")
    if tenant.status != TenantStatus.active:
        raise _with_verified_tenant(TenantInactiveError(f"tenant '{tenant_slug}' is not active (status={tenant.status.value})"), tenant.id)

    user_result = await db.execute(
        select(User).where(User.tenant_id == tenant.id, User.external_id == external_id)
    )
    user = user_result.scalar_one_or_none()
    if user is None:
        raise _with_verified_tenant(UserNotFoundError(f"no user with external_id '{external_id}' under tenant '{tenant_slug}'"), tenant.id)
    if user.status != UserStatus.active:
        raise _with_verified_tenant(UserInactiveError(f"user '{external_id}' is not active (status={user.status.value})"), tenant.id)

    # Redundant with the query's own WHERE clause above, but stated as an
    # explicit check anyway — the spec asks for this as its own numbered
    # validation step, and a future refactor of the query (e.g. to look
    # the user up by ID alone) shouldn't silently drop this guarantee.
    if user.tenant_id != tenant.id:
        raise _with_verified_tenant(UserTenantMismatchError(f"user '{external_id}' does not belong to tenant '{tenant_slug}'"), tenant.id)

    return AuthenticatedIdentity(
        user_id=user.id, tenant_id=tenant.id, tenant_slug=tenant.slug, external_id=user.external_id,
        source=IdentitySource.APP_JWT,
    )


async def resolve_identity_by_ids(
    db: AsyncSession, *, user_id: uuid.UUID, tenant_id: uuid.UUID
) -> AuthenticatedIdentity:
    """The LiveKit participant path (see livekit_identity.py): by the time
    a participant appears in `ctx.room`, LiveKit's own server has already
    validated the signed access token that carried `user_id`/`tenant_id`
    as attributes — those values are not forgeable by the connecting
    client. But a token minted an hour ago doesn't guarantee the user is
    STILL active now, so this re-runs the same active/mismatch checks
    against current Postgres state rather than trusting the token's
    snapshot-in-time claims as permanently valid."""
    tenant = (await db.execute(select(Tenant).where(Tenant.id == tenant_id))).scalar_one_or_none()
    if tenant is None:
        raise TenantNotFoundError(f"no tenant with id '{tenant_id}'")
    if tenant.status != TenantStatus.active:
        raise _with_verified_tenant(TenantInactiveError(f"tenant '{tenant.slug}' is not active (status={tenant.status.value})"), tenant.id)

    user = (await db.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
    if user is None:
        raise _with_verified_tenant(UserNotFoundError(f"no user with id '{user_id}'"), tenant.id)
    if user.status != UserStatus.active:
        raise _with_verified_tenant(UserInactiveError(f"user '{user_id}' is not active (status={user.status.value})"), tenant.id)
    if user.tenant_id != tenant.id:
        raise _with_verified_tenant(UserTenantMismatchError(f"user '{user_id}' does not belong to tenant '{tenant_id}'"), tenant.id)

    return AuthenticatedIdentity(
        user_id=user.id, tenant_id=tenant.id, tenant_slug=tenant.slug, external_id=user.external_id,
        source=IdentitySource.LIVEKIT_PARTICIPANT,
    )
