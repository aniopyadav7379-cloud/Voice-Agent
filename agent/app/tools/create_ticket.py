"""
`create_ticket` — the first real operational action (Phase G).

End-to-end path this exercises:

  authenticated identity -> authorization -> validated args -> idempotency
  key -> ToolCall(pending) -> TicketService -> ToolResult -> final status

WHAT IS AND ISN'T REAL HERE, stated plainly:
  - The tool, contract, authorization, idempotency, validation,
    persistence, and failure handling are REAL and tested.
  - The thing on the other side of `TicketService` is a TEST ADAPTER.
    No external ticketing system exists in any supplied repository (see
    ticket_service.py for the search that established this), and none has
    been contacted. `TicketRecord.ticket_id` values are local only.
"""
import uuid
from datetime import datetime

from pydantic import BaseModel, Field

from app.security.identity import AuthenticatedIdentity
from app.tools.contracts import (
    AuthorizationLevel,
    IdempotencyMode,
    SideEffect,
    ToolContract,
)
from app.tools.ticket_service import TicketRequest, TicketService, TicketServiceError

#: Constrained to an explicit enum rather than free text — an LLM cannot
#: invent a priority the downstream system doesn't understand.
PRIORITIES = ("low", "normal", "high", "urgent")


class CreateTicketInput(BaseModel):
    """Business arguments the MODEL supplies. Deliberately contains no
    tenant_id/user_id/session_id field: there is nothing here for a model
    to fill in order to redirect a ticket to another tenant.

    Every field is length- and format-bounded. Validation runs before the
    handler, so malformed or oversized input never reaches the service.
    """

    site_id: str = Field(..., min_length=1, max_length=64, pattern=r"^[A-Za-z0-9._\-]+$")
    description: str = Field(..., min_length=1, max_length=2000)
    priority: str = Field(default="normal", pattern=r"^(low|normal|high|urgent)$")
    equipment_id: str | None = Field(default=None, max_length=64, pattern=r"^[A-Za-z0-9._\-]+$")


class CreateTicketOutput(BaseModel):
    """Structured result the agent receives. `success=False` carries a
    reason and NO ticket_id — a ticket id is never populated unless a
    ticket genuinely exists."""

    success: bool
    ticket_id: str | None = None
    status: str | None = None
    message: str
    deduplicated: bool = False
    """True when this request matched a prior execution's idempotency key
    and the ORIGINAL result was returned instead of creating a second
    ticket. Surfaced so the agent can say 'that ticket already exists'
    rather than implying it just made a new one."""


CREATE_TICKET_CONTRACT = ToolContract(
    name="create_ticket",
    description=(
        "Create a maintenance ticket for a site, optionally about specific equipment. "
        "Use when the user explicitly asks to raise, open, or file a ticket."
    ),
    input_schema=CreateTicketInput,
    output_schema=CreateTicketOutput,
    side_effect=SideEffect.EXTERNAL_SIDE_EFFECT,
    authorization=AuthorizationLevel.AUTHENTICATED_WRITE,
    idempotency=IdempotencyMode.KEYED,
    # Description is deliberately EXCLUDED from the key: an agent retrying
    # the same logical request may reword free text, and that must not
    # defeat duplicate suppression. Site + equipment + priority identify
    # the logical action.
    idempotency_fields=("site_id", "equipment_id", "priority"),
    timeout_seconds=10.0,
)


class AuthorizationError(Exception):
    """Raised before execution when the identity may not run this tool."""


def authorize(contract: ToolContract, identity: AuthenticatedIdentity | None) -> None:
    """The authorization policy, deliberately minimal (the task said not
    to build RBAC absent a demonstrated requirement, and the schema has no
    roles anywhere).

    Today the rule is: an authenticated identity is required for every
    tool, and write-classified tools require the same. Both levels are
    enforced through this one function so that when roles do arrive, this
    is the only place that changes.

    The security-relevant part is not the level check — it's that a
    missing identity is rejected outright. `identity is None` means the
    session boundary did not produce an AuthenticatedIdentity, and no
    side-effecting tool may run in that state.
    """
    if identity is None:
        raise AuthorizationError(f"tool '{contract.name}' requires an authenticated identity")
    if contract.authorization is AuthorizationLevel.AUTHENTICATED_WRITE:
        # No role model exists yet; an active authenticated user of an
        # active tenant (already validated at the session boundary) is the
        # current bar. Named separately so it is greppable when roles land.
        return
    return


def build_create_ticket_handler(service: TicketService):
    """Returns a handler bound to a concrete `TicketService`. The service
    is injected rather than constructed here so production wiring and the
    test adapter use the identical code path."""

    async def _handler(
        args: CreateTicketInput, *, identity: AuthenticatedIdentity,
    ) -> CreateTicketOutput:
        # tenant_id/user_id come from TRUSTED CONTEXT, never from `args`.
        request = TicketRequest(
            site_id=args.site_id,
            description=args.description,
            priority=args.priority,
            equipment_id=args.equipment_id,
        )
        try:
            record = await service.create_ticket(
                request=request, tenant_id=identity.tenant_id, user_id=identity.user_id,
            )
        except TicketServiceError as e:
            # Never fabricate a ticket_id on failure.
            return CreateTicketOutput(success=False, message=f"ticket creation failed: {e}")

        return CreateTicketOutput(
            success=True, ticket_id=record.ticket_id, status=record.status,
            message=f"Ticket {record.ticket_id} created for site {record.site_id}.",
        )

    return _handler
