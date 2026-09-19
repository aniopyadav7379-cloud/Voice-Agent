"""
Machine-readable tool contracts (Phases B, C, D, F).

The split this file enforces, which is the whole point:

    THE MODEL CONTROLS          THE PLATFORM CONTROLS
    ------------------          ---------------------
    tool arguments only         tenant_id, user_id, session_id
                                authorization decision
                                correlation id (call_id)
                                timeout
                                retry decision
                                idempotency key derivation

A `ToolContract` is data, not behavior. It declares what a tool *is* so
the registry can decide how to *run* it — rather than each handler
re-deciding those things inconsistently, which is how side-effecting
tools end up quietly retried or run unauthorized.

WHY NOT A POLICY ENGINE: the task said not to build one unless
necessary, and it isn't. Three enums and a dataclass cover every
decision the current tool set actually needs. Adding a rules DSL here
would be speculative machinery with no caller.
"""
import enum
from dataclasses import dataclass, field

from pydantic import BaseModel


class SideEffect(str, enum.Enum):
    """Phase C classification. Drives retry-safety and idempotency
    requirements — it is not documentation."""

    READ_ONLY = "read_only"
    """No state changes anywhere. Safe to retry freely."""

    WRITE = "write"
    """Changes state inside THIS platform's own database only. Retry
    safety depends on the operation being idempotent."""

    EXTERNAL_SIDE_EFFECT = "external_side_effect"
    """Causes an effect in a system this platform does not own — a
    ticket, a dispatch, a notification a human will actually receive.
    Never retried automatically without an idempotency key, because a
    duplicate is a real-world duplicate, not a duplicate row."""


class IdempotencyMode(str, enum.Enum):
    NOT_REQUIRED = "not_required"
    """Read-only operations. Running twice changes nothing."""

    KEYED = "keyed"
    """Duplicate suppression via a derived idempotency key with a unique
    database constraint. A repeat of the SAME logical request returns the
    ORIGINAL result instead of acting again."""

    UNSAFE_TO_REPEAT = "unsafe_to_repeat"
    """Repeating causes real duplicate harm and no key scheme exists yet.
    Such a tool must never be retried automatically. Declaring this is
    how a tool honestly says "I am not safe yet" rather than silently
    being treated as retryable."""


class AuthorizationLevel(str, enum.Enum):
    """Deliberately NOT full RBAC — the task said to add that only if the
    project demonstrates a real requirement, and it doesn't (there are no
    roles anywhere in the schema; `User` has no role column). This is the
    minimum that expresses a real distinction: some tools only read, some
    change the world.

    Today every authenticated, active user of a tenant may do both. The
    value is that the distinction is DECLARED, so when roles do arrive
    there is one place to enforce them rather than ten handlers to audit.
    """

    AUTHENTICATED = "authenticated"
    """Any active user of an active tenant."""

    AUTHENTICATED_WRITE = "authenticated_write"
    """Same today, but separately named because these are the calls that
    will need a role check first when roles exist."""


@dataclass(frozen=True)
class ToolContract:
    name: str
    description: str
    input_schema: type[BaseModel]
    output_schema: type[BaseModel]
    side_effect: SideEffect
    authorization: AuthorizationLevel
    idempotency: IdempotencyMode
    timeout_seconds: float
    #: Argument field names that participate in the idempotency key. Empty
    #: for tools that don't use keyed idempotency. Explicit rather than
    #: "all fields" because a field like a free-text description may vary
    #: harmlessly between retries of the same logical request.
    idempotency_fields: tuple[str, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        # Fail at import time, not at 3am during an incident.
        if self.idempotency is IdempotencyMode.KEYED and not self.idempotency_fields:
            raise ValueError(f"tool '{self.name}' declares KEYED idempotency but names no idempotency_fields")
        if self.side_effect is SideEffect.READ_ONLY and self.idempotency is not IdempotencyMode.NOT_REQUIRED:
            raise ValueError(f"tool '{self.name}' is READ_ONLY; idempotency should be NOT_REQUIRED")


class FailureClass(str, enum.Enum):
    """Phase F. Retry decisions are made from this classification, never
    from the exception type at the call site."""

    VALIDATION = "validation"
    AUTHORIZATION = "authorization"
    TIMEOUT = "timeout"
    TRANSIENT = "transient"          # network blip, connection reset
    PROVIDER_ERROR = "provider_error"  # upstream 5xx
    BUSINESS_RULE = "business_rule"    # upstream said no, deterministically
    UNKNOWN = "unknown"


#: Which failure classes are retry-safe AT ALL. A True here is necessary
#: but NOT sufficient — `is_retry_safe()` below also requires the tool's
#: own idempotency mode to permit it.
_RETRYABLE_CLASSES = {
    FailureClass.VALIDATION: False,      # same input fails identically
    FailureClass.AUTHORIZATION: False,   # retrying is just a second denial
    FailureClass.BUSINESS_RULE: False,   # upstream decided; asking again is noise
    FailureClass.TRANSIENT: True,
    FailureClass.PROVIDER_ERROR: True,
    FailureClass.TIMEOUT: True,
    FailureClass.UNKNOWN: False,         # default closed — see below
}


def is_retry_safe(contract: ToolContract, failure: FailureClass) -> bool:
    """Both conditions must hold: the failure must be the kind that could
    succeed on a second attempt, AND the tool must be safe to repeat.

    TIMEOUT is the case that makes this two-condition check necessary
    rather than a lookup. A timed-out external write is the single most
    dangerous retry in the system: the request may well have SUCCEEDED
    upstream with only the response lost. Retrying it without an
    idempotency key creates the duplicate ticket this milestone exists to
    prevent. So a timeout is retryable only for READ_ONLY tools or KEYED
    ones — never for UNSAFE_TO_REPEAT.

    UNKNOWN defaults to not-retryable: if we cannot classify a failure we
    cannot reason about whether repeating it is safe.
    """
    if not _RETRYABLE_CLASSES.get(failure, False):
        return False
    if contract.side_effect is SideEffect.READ_ONLY:
        return True
    return contract.idempotency is IdempotencyMode.KEYED
