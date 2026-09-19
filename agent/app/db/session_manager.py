"""
Wraps VoiceSession/ConversationTurn CRUD so `agent_entrypoint.py` doesn't
embed SQL. Same "batched, not per-frame" writing philosophy as the earlier
voice-platform gateway project's SessionManager: writes happen at session
start, per completed conversation turn, and session end — not on every
audio frame.

Bridges an important identity gap, stated explicitly rather than glossed
over: `FieldOpsAssistant`'s `tenant_id`/`session_id` (used for Moss
namespacing, Qdrant filtering, and tool-registry calls — all untouched by
this module, per this milestone's explicit scope) are plain strings sourced
from LiveKit room metadata/room name, NOT the UUID primary keys this
Postgres schema uses. `ensure_tenant()` treats that incoming string as a
`Tenant.slug` and looks up-or-creates the corresponding UUID-keyed Tenant
row. The Postgres UUIDs and the Moss/Qdrant/tool-registry string IDs are
deliberately kept as two separate identity schemes rather than forcing one
onto the other — changing what Moss/Qdrant/tools use as their identifier
is out of scope for a database-integration milestone and was explicitly
told not to happen unless genuinely required, which this isn't: both
schemes can reference "the same tenant" via the shared slug without
becoming the same value.
"""
import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import (
    AuditLog,
    ConversationTurn,
    SecurityEvent,
    Tenant,
    ToolCall,
    ToolCallStatus,
    ToolResult,
    TurnRole,
    VoiceSession,
    VoiceSessionStatus,
)


class VoiceSessionRecorder:
    def __init__(self, db: AsyncSession):
        self._db = db

    async def ensure_tenant(self, *, slug: str, name: str | None = None) -> Tenant:
        """Look up a tenant by slug, creating it if this is the first time
        this slug has been seen. Idempotent — safe to call on every session
        start rather than requiring a separate tenant-provisioning step,
        since no such step exists anywhere in this project yet (no admin
        API, no onboarding flow — flagged as a real gap, not built here)."""
        result = await self._db.execute(select(Tenant).where(Tenant.slug == slug))
        tenant = result.scalar_one_or_none()
        if tenant is not None:
            return tenant

        tenant = Tenant(slug=slug, name=name or slug)
        self._db.add(tenant)
        await self._db.flush()
        return tenant

    async def start_session(
        self, *, tenant_id: uuid.UUID, initial_language: str = "en-IN", user_id: uuid.UUID | None = None,
    ) -> VoiceSession:
        session = VoiceSession(
            tenant_id=tenant_id, user_id=user_id, current_language=initial_language,
            language_history=initial_language, status=VoiceSessionStatus.active,
        )
        self._db.add(session)
        await self._db.flush()
        return session

    async def record_turn(
        self, *, tenant_id: uuid.UUID, session_id: uuid.UUID, role: TurnRole, text: str,
        language: str | None = None, retrieval_metadata: dict | None = None,
    ) -> ConversationTurn:
        turn = ConversationTurn(
            tenant_id=tenant_id, session_id=session_id, role=role, text=text,
            language=language, retrieval_metadata=retrieval_metadata or {},
        )
        self._db.add(turn)
        await self._db.flush()
        return turn

    async def end_session(self, session: VoiceSession, *, status: VoiceSessionStatus = VoiceSessionStatus.closed) -> None:
        from sqlalchemy import func

        session.status = status
        session.ended_at = func.now()
        await self._db.flush()

    async def commit(self) -> None:
        await self._db.commit()


class ToolCallRecorder:
    """Persists the ToolCall/ToolResult lifecycle for
    `app/tools/registry.py`'s `execute()`. Deliberately does NOT live in
    `tools/registry.py` itself and is consumed there only through a
    structural Protocol (see registry.py's `ToolCallRecorderProtocol`) —
    this keeps `tools/` free of any hard dependency on SQLAlchemy/the DB
    layer. Every existing caller of `ToolRegistry.execute()` that doesn't
    pass a recorder gets exactly the pre-existing in-memory-only behavior,
    unchanged.

    Lifecycle, matching the task's own spec:
    - `record_call_started`: a ToolCall row (status=pending) BEFORE the
      handler runs — this row's existence is a fact independent of what
      happens next, including a crash mid-handler.
    - `record_rejected`: for arguments that fail validation, or an unknown
      tool name — a ToolCall row with status=rejected, created directly at
      that terminal status, with NO ToolResult row. The absence of a
      ToolResult is itself the structural signal that the handler never
      ran; see models.py's ToolCallStatus.rejected docstring for why this
      distinction is preserved rather than collapsed into `failed`.
    - `record_result`: the ONLY path that creates a ToolResult row —
      called after a handler actually ran (successfully, with an error, or
      via timeout), never before.
    """

    def __init__(self, db: AsyncSession):
        self._db = db

    async def record_call_started(
        self, *, call_id: str, tenant_id: uuid.UUID, session_id: uuid.UUID,
        user_id: uuid.UUID | None, tool_name: str, arguments: dict,
        idempotency_key: str | None = None,
    ) -> ToolCall:
        # `idempotency_key` defaults to None so every pre-existing caller
        # (registry.py and all tests written before contracts existed) is
        # completely unaffected — NULL keys don't participate in the
        # unique constraint.
        row = ToolCall(
            call_id=call_id, tenant_id=tenant_id, session_id=session_id, user_id=user_id,
            tool_name=tool_name, arguments=arguments, status=ToolCallStatus.pending,
            idempotency_key=idempotency_key,
        )
        self._db.add(row)
        await self._db.flush()
        await self._db.commit()
        return row

    async def record_rejected(
        self, *, call_id: str, tenant_id: uuid.UUID, session_id: uuid.UUID,
        user_id: uuid.UUID | None, tool_name: str, arguments: dict,
    ) -> ToolCall:
        row = ToolCall(
            call_id=call_id, tenant_id=tenant_id, session_id=session_id, user_id=user_id,
            tool_name=tool_name, arguments=arguments, status=ToolCallStatus.rejected,
            completed_at=func.now(),
        )
        self._db.add(row)
        await self._db.flush()
        await self._db.commit()
        return row

    async def record_result(
        self, *, tool_call: ToolCall, success: bool, output: dict | None, error: str | None,
        duration_ms: float, final_status: ToolCallStatus,
    ) -> ToolResult:
        result = ToolResult(
            tool_call_id=tool_call.id, success=success, output=output, error=error,
            duration_ms=int(duration_ms),
        )
        self._db.add(result)
        tool_call.status = final_status
        tool_call.completed_at = func.now()
        await self._db.flush()
        await self._db.commit()
        return result

    async def get_by_call_id(self, call_id: str) -> ToolCall | None:
        result = await self._db.execute(select(ToolCall).where(ToolCall.call_id == call_id))
        return result.scalar_one_or_none()


class AuditRecorder:
    """Writes `AuditLog` rows for security-sensitive events.

    Deliberately scoped to authentication/authorization-boundary events
    this session (identity-resolution rejections — failed auth attempts
    are exactly the kind of thing a real security review wants a durable
    record of). NOT wired into every tool call: `ToolCall`/`ToolResult`
    (session 6) already capture a fuller, more structured record of every
    tool execution — including tenant_id and, as of this session, user_id
    — than a parallel `AuditLog` row would; writing both would be pure
    duplication with no new information, not defense in depth. If a
    future need arises for audit events that aren't already captured by
    an existing domain table (e.g. permission changes, tenant
    suspensions), extend this class then, against that real requirement.

    Note `AuditLog.tenant_id` is `NOT NULL` (session 4's schema): there is
    no way to durably record a "tenant does not exist" rejection in this
    table, since there's no valid tenant to attach the row to. That case
    is logged via the application logger only — stated as a real, current
    limitation in STATUS_REPORT.md, not silently worked around with a
    sentinel tenant row, which would be a bigger, undiscussed schema
    change than this milestone's scope.
    """

    def __init__(self, db: AsyncSession):
        self._db = db

    async def record(
        self, *, tenant_id: uuid.UUID, user_id: uuid.UUID | None, action: str,
        resource_type: str, resource_id: str | None = None, metadata: dict | None = None,
    ) -> AuditLog:
        entry = AuditLog(
            tenant_id=tenant_id, user_id=user_id, action=action,
            resource_type=resource_type, resource_id=resource_id, event_metadata=metadata or {},
        )
        self._db.add(entry)
        await self._db.flush()
        await self._db.commit()
        return entry


class SecurityEventRecorder:
    """Writes `SecurityEvent` rows for authentication failures that occur
    BEFORE a tenant could be verified. See docs/adr/001-audit-tenant-id.md.

    Pairs with `AuditRecorder`: together they implement the ADR's routing
    rule, and `is_tenant_verified()` below is the single place that rule
    is encoded, so callers don't each re-derive it (and can't each get it
    subtly wrong).
    """

    def __init__(self, db: AsyncSession):
        self._db = db

    async def record(
        self, *, action: str, reason: str, resource_type: str, resource_id: str | None = None,
        claimed_tenant_slug: str | None = None, claimed_tenant_id: str | None = None,
        claimed_user_identity: str | None = None, metadata: dict | None = None,
    ) -> SecurityEvent:
        entry = SecurityEvent(
            action=action, reason=reason, resource_type=resource_type, resource_id=resource_id,
            claimed_tenant_slug=claimed_tenant_slug, claimed_tenant_id=claimed_tenant_id,
            claimed_user_identity=claimed_user_identity, event_metadata=metadata or {},
        )
        self._db.add(entry)
        await self._db.flush()
        await self._db.commit()
        return entry


def is_tenant_verified(error: Exception) -> bool:
    """ADR 001's routing rule, encoded once.

    Returns True when the tenant was successfully looked up and read from
    Postgres before the rejection occurred — meaning an `audit_logs` row
    can legitimately claim that (verified) tenant. Returns False when no
    tenant was ever verified, meaning the event must go to
    `security_events` instead.

    Implemented as an explicit allow-list of "tenant was verified"
    exception types rather than a deny-list of the two that aren't:
    a future exception type added to identity.py will default to the SAFE
    side (unverified -> security_events) rather than silently gaining
    permission to assert a tenant it may not have checked.
    """
    from app.security.identity import (
        UserInactiveError,
        UserNotFoundError,
        UserTenantMismatchError,
        TenantInactiveError,
    )

    return isinstance(
        error, (TenantInactiveError, UserNotFoundError, UserInactiveError, UserTenantMismatchError)
    )
