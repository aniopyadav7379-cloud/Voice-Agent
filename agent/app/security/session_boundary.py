"""
The LiveKit session boundary: the single place where an unauthenticated
job becomes an authenticated session, and the gate that enforces the
milestone's core invariant —

  NO USER-SUPPLIED VOICE/TEXT/TOOL REQUEST MAY REACH THE AGENT OR TOOL
  LAYER BEFORE AUTHENTICATED IDENTITY EXISTS.

WHY THIS MODULE EXISTS (the wait_for_participant decision)
----------------------------------------------------------
`JobContext.wait_for_participant()` is the correct primitive and is kept —
but it is NOT sufficient on its own. That conclusion comes from reading
its actual implementation in the installed SDK
(`livekit/agents/utils/participant.py`), not from assuming:

  - It DOES return immediately for an already-joined participant (so a
    reused room or a restarted agent doesn't hang).
  - It DOES raise `RuntimeError("room disconnected while waiting for
    participant")` if the room disconnects mid-wait — via a real
    `connection_state_changed` listener.
  - It filters by participant kind, defaulting to `[5, 3, 0]`, which
    excludes `PARTICIPANT_KIND_AGENT` (4) — so the agent cannot
    accidentally authenticate itself. Verified by inspecting the real
    enum values.
  - It has NO TIMEOUT. `await fut` waits forever if nobody ever joins.

That last point is a real defect in the previous session's code, which
called `wait_for_participant()` bare: a job created for a room that no
one joins would hold an open Postgres session and a worker slot
indefinitely, with no log line and no exit. `establish_authenticated_session()`
below wraps the call in `asyncio.wait_for` to fix exactly that.

MULTIPLE PARTICIPANTS — deliberate decision, stated rather than implied
-----------------------------------------------------------------------
`wait_for_participant()` returns the FIRST matching participant. This
module authenticates that participant and binds the session to them for
its lifetime. It does NOT attempt to authenticate later joiners
separately.

That is a deliberate scope decision, not an oversight: this platform's
product model is a single field worker talking to an agent (see the
project's own scenario: one technician, one pump, one ticket). A
multi-participant room would need a per-speaker identity model reaching
all the way into diarization, per-turn attribution, and per-speaker tool
authorization — a substantially larger design than this milestone
permits. What this module does instead is make the single-participant
assumption EXPLICIT and ENFORCED rather than accidental:
`enforce_single_participant()` registers a real `participant_connected`
listener and, if a second non-agent participant joins, logs a warning and
(by default) shuts the session down rather than silently letting an
unauthenticated second person speak into an authenticated session. Failing
closed is the correct default for an identity boundary.

LATE JOIN AFTER STARTUP
-----------------------
Covered by the same primitive: if no participant has joined yet,
`wait_for_participant()` suspends until one does (up to the timeout). A
participant joining "late" is simply the normal path with a longer wait.

DISCONNECT DURING INITIALISATION
--------------------------------
Two distinct cases, handled distinctly:
  - Room disconnects mid-wait -> `wait_for_participant` raises
    `RuntimeError` -> surfaced here as `SessionBoundaryError`.
  - Participant joins, then drops while we're mid-database-validation ->
    identity resolution still completes (it only needs the attributes
    already received), and the session ends normally through the agent's
    existing shutdown path. We deliberately do NOT special-case this:
    resolving identity for a participant who has since left is harmless,
    and adding a liveness re-check would introduce a race without
    removing one.

NO PER-FRAME DATABASE WORK
--------------------------
`establish_authenticated_session()` performs exactly ONE identity
resolution, at the session boundary, and returns an immutable
`AuthenticatedIdentity` reused for the session's entire lifetime. Nothing
in this module is reachable from the audio path.

VERIFICATION STATUS: SDK VERIFIED + LOCAL TESTED. Every LiveKit API used
here was read from the installed SDK's own source before use. Nothing in
this module has run against a live LiveKit room — that remains BLOCKED.
"""
import asyncio
import logging
from dataclasses import dataclass

from livekit import rtc
from sqlalchemy.ext.asyncio import AsyncSession

from app.security.identity import AuthenticatedIdentity, IdentityResolutionError
from app.security.livekit_identity import resolve_identity_from_participant

logger = logging.getLogger("agent.session_boundary")

# A room whose participant never arrives shouldn't pin a worker slot and a
# Postgres session forever. 60s is generous for a real client that has
# already been issued a token and is connecting.
DEFAULT_PARTICIPANT_WAIT_SECONDS = 60.0


class SessionBoundaryError(Exception):
    """The session could not be established. Carries `reason` as a short
    machine-readable string suitable for `ctx.shutdown(reason=...)` and
    for an audit record's metadata — never a raw exception string, which
    could contain detail we don't want to echo back."""

    def __init__(self, reason: str, message: str):
        self.reason = reason
        super().__init__(message)


class NoParticipantError(SessionBoundaryError):
    def __init__(self, timeout_seconds: float):
        super().__init__(
            "no_participant",
            f"no participant joined within {timeout_seconds}s; abandoning session",
        )


class RoomDisconnectedError(SessionBoundaryError):
    def __init__(self, detail: str):
        super().__init__("room_disconnected", f"room disconnected before identity was established: {detail}")


class AuthenticationRejectedError(SessionBoundaryError):
    """Identity resolution ran and rejected the participant. `cause` is the
    original `IdentityResolutionError` subclass, preserved so the caller
    can decide whether the failure is auditable (see audit_events.py's
    tenant-verification rules)."""

    def __init__(self, cause: IdentityResolutionError):
        self.cause = cause
        super().__init__("auth_rejected", f"identity resolution rejected participant: {type(cause).__name__}")


@dataclass(frozen=True)
class AuthenticatedSession:
    """What a caller gets once, and only once, authentication has
    succeeded. Holding one of these IS the proof that the boundary was
    crossed legitimately — `agent_entrypoint.py` cannot construct the
    agent without it."""

    identity: AuthenticatedIdentity
    participant: rtc.Participant


async def establish_authenticated_session(
    room: rtc.Room,
    db: AsyncSession,
    *,
    wait_for_participant_fn,
    timeout_seconds: float = DEFAULT_PARTICIPANT_WAIT_SECONDS,
) -> AuthenticatedSession:
    """Waits for a participant, resolves and validates their identity, and
    returns an `AuthenticatedSession`.

    `wait_for_participant_fn` is injected rather than calling
    `ctx.wait_for_participant` directly so this function is testable
    without a live room — the production caller passes
    `ctx.wait_for_participant`, tests pass a fake with the same
    signature. This is dependency injection for testability, not an
    abstraction over the SDK: the real function's contract is preserved
    exactly.
    """
    try:
        participant = await asyncio.wait_for(wait_for_participant_fn(), timeout=timeout_seconds)
    except asyncio.TimeoutError as e:
        raise NoParticipantError(timeout_seconds) from e
    except RuntimeError as e:
        # The SDK's documented failure mode for a room that drops while
        # waiting — see module docstring.
        raise RoomDisconnectedError(str(e)) from e

    try:
        identity = await resolve_identity_from_participant(db, participant)
    except IdentityResolutionError as e:
        raise AuthenticationRejectedError(e) from e

    logger.info(
        "session authenticated: tenant=%s user=%s participant=%s",
        identity.tenant_slug, identity.user_id, participant.identity,
    )
    return AuthenticatedSession(identity=identity, participant=participant)


def enforce_single_participant(room: rtc.Room, authenticated: AuthenticatedSession, on_violation) -> None:
    """Registers a real `participant_connected` listener (verified to be a
    genuine `rtc.Room` event name against the installed SDK) that fires
    `on_violation` if a second non-agent participant joins an
    already-authenticated session.

    Fails closed by design — see the module docstring's multi-participant
    section for why this is an explicit, enforced constraint rather than
    an unstated assumption.
    """

    def _on_participant_connected(participant: rtc.RemoteParticipant) -> None:
        if participant.kind == rtc.ParticipantKind.PARTICIPANT_KIND_AGENT:
            return  # other agents are infrastructure, not users
        if participant.identity == authenticated.participant.identity:
            return  # same user reconnecting under the same identity
        logger.warning(
            "unexpected second participant '%s' joined session authenticated for '%s'",
            participant.identity, authenticated.participant.identity,
        )
        on_violation(participant)

    room.on("participant_connected", _on_participant_connected)
