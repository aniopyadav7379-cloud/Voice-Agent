"""
Ticketing service boundary (Phase G).

NO EXTERNAL TICKETING PROVIDER EXISTS. That is a finding, not an
assumption: all three supplied repositories were searched for
`create_ticket|ticketing|servicenow|zendesk|jira|freshdesk`. The only two
hits were a JavaScript test fixture in moss-main and a prose mention of
Zendesk in primd-main's market-research document (a repo deliberately
excluded from this project). Neither is an implementation.

So this file defines the BOUNDARY a real provider would plug into, plus a
deterministic adapter for tests. It does not pretend to be an
integration.

    TicketService (abstract)         <- what the tool depends on
        |
        +-- InMemoryTicketService    <- TEST ADAPTER. Clearly labelled.
        |                               Deterministic, no network.
        +-- <YourProviderAdapter>    <- NOT IMPLEMENTED. Needs real
                                        credentials and a real API.

The tool layer depends only on the abstract class, so adding a real
provider is one new file and one wiring change — no tool, persistence, or
agent code moves.
"""
import asyncio
import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone

from app.tools.contracts import FailureClass


@dataclass(frozen=True)
class TicketRequest:
    """Business arguments ONLY. Note what is absent: tenant_id, user_id,
    session_id. Those are supplied by the platform from authenticated
    context at the call site, never by the model — which is precisely why
    they are not fields here. There is no field for a model to fill in
    order to attribute a ticket to another tenant."""

    site_id: str
    description: str
    priority: str
    equipment_id: str | None = None


@dataclass(frozen=True)
class TicketRecord:
    ticket_id: str
    status: str
    site_id: str
    tenant_id: uuid.UUID
    created_by_user_id: uuid.UUID
    created_at: datetime


class TicketServiceError(Exception):
    """Carries a `failure_class` so the registry's retry policy can reason
    about the failure without inspecting provider-specific exception
    types — see contracts.is_retry_safe()."""

    def __init__(self, message: str, failure_class: FailureClass = FailureClass.UNKNOWN):
        self.failure_class = failure_class
        super().__init__(message)


class TicketService(ABC):
    """The production adapter interface.

    A real implementation must: enforce its own timeout (the registry also
    bounds it, but defence in depth), map provider errors onto
    `FailureClass` honestly, and never invent a ticket id when creation
    did not actually succeed.
    """

    @abstractmethod
    async def create_ticket(
        self, *, request: TicketRequest, tenant_id: uuid.UUID, user_id: uuid.UUID,
    ) -> TicketRecord:
        """Raise `TicketServiceError` on failure. Returning a TicketRecord
        means a ticket genuinely exists."""
        raise NotImplementedError


class InMemoryTicketService(TicketService):
    """*** TEST ADAPTER — NOT A PRODUCTION INTEGRATION ***

    Deterministic, in-process, no network. Exists so the end-to-end path
    (auth -> contract -> authorization -> idempotency -> persistence ->
    result) can be tested for real without inventing a provider or
    claiming a live integration.

    Any ticket id it returns is local and meaningless outside this
    process. Nothing here has ever contacted a ticketing system.
    """

    def __init__(self, *, fail_with: TicketServiceError | None = None, delay_seconds: float = 0.0):
        self.tickets: dict[str, TicketRecord] = {}
        self._fail_with = fail_with
        self._delay_seconds = delay_seconds
        self.create_call_count = 0  # lets tests assert the provider was/wasn't hit twice

    async def create_ticket(
        self, *, request: TicketRequest, tenant_id: uuid.UUID, user_id: uuid.UUID,
    ) -> TicketRecord:
        self.create_call_count += 1
        if self._delay_seconds:
            await asyncio.sleep(self._delay_seconds)  # used to exercise timeout behavior
        if self._fail_with is not None:
            raise self._fail_with

        ticket_id = f"TEST-TKT-{len(self.tickets) + 1:05d}"
        record = TicketRecord(
            ticket_id=ticket_id, status="open", site_id=request.site_id,
            tenant_id=tenant_id, created_by_user_id=user_id,
            created_at=datetime.now(timezone.utc),
        )
        self.tickets[ticket_id] = record
        return record
