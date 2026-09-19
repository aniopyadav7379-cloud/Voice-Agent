"""
Registers the tool set your spec requires. Every handler below is a STUB
that validates input correctly and returns a structured, honest failure —
NOT a fake success. No real ticketing/CRM/dispatch/notification backend was
provided in any of the three audited repositories, so there is nothing real
to call. This file exists to prove the schema/validation/audit-logging shape
is right, and to give you exact integration points once real backends exist.

Per the spec's tool-execution rule: "Never claim an action succeeded unless
the actual tool returned success." Every stub here returns success=False
with an explicit reason — the agent-facing contract this enforces is: until
a real backend is wired in, the assistant will truthfully tell the user
every action failed, never fabricate a ticket number or confirmation.
"""
from pydantic import BaseModel, Field

from app.tools.registry import ToolRegistry


class CreateTicketArgs(BaseModel):
    site_id: str = Field(..., min_length=1)
    equipment_id: str | None = None
    description: str = Field(..., min_length=1, max_length=2000)
    priority: str = Field(default="normal", pattern="^(low|normal|high|urgent)$")


class UpdateTicketArgs(BaseModel):
    ticket_id: str = Field(..., min_length=1)
    status: str | None = Field(default=None, pattern="^(open|in_progress|resolved|closed)$")
    notes: str | None = Field(default=None, max_length=2000)


class GetEquipmentStatusArgs(BaseModel):
    equipment_id: str = Field(..., min_length=1)


class GetCustomerDetailsArgs(BaseModel):
    customer_id: str = Field(..., min_length=1)


class GetWorkerLocationArgs(BaseModel):
    worker_id: str = Field(..., min_length=1)


class FindNearestTechnicianArgs(BaseModel):
    site_id: str = Field(..., min_length=1)
    skill_required: str | None = None


class DispatchWorkerArgs(BaseModel):
    worker_id: str = Field(..., min_length=1)
    site_id: str = Field(..., min_length=1)
    ticket_id: str | None = None


class SendNotificationArgs(BaseModel):
    recipient_id: str = Field(..., min_length=1)
    message: str = Field(..., min_length=1, max_length=1000)
    channel: str = Field(default="push", pattern="^(push|sms|email)$")


class UpdateCRMArgs(BaseModel):
    customer_id: str = Field(..., min_length=1)
    fields: dict[str, str]


class EscalateToHumanArgs(BaseModel):
    session_id: str = Field(..., min_length=1)
    reason: str = Field(..., min_length=1, max_length=500)


def _not_implemented(tool_name: str) -> dict:
    return {
        "backend_configured": False,
        "reason": f"No real backend is wired up for '{tool_name}' in this build. "
                  f"This is a stub — it correctly validates input and reports failure, "
                  f"it does not fabricate success.",
    }


async def _create_ticket(args: CreateTicketArgs, tenant_id: str, session_id: str) -> dict:
    raise RuntimeError(_not_implemented("create_ticket")["reason"])


async def _update_ticket(args: UpdateTicketArgs, tenant_id: str, session_id: str) -> dict:
    raise RuntimeError(_not_implemented("update_ticket")["reason"])


async def _get_equipment_status(args: GetEquipmentStatusArgs, tenant_id: str, session_id: str) -> dict:
    raise RuntimeError(_not_implemented("get_equipment_status")["reason"])


async def _get_customer_details(args: GetCustomerDetailsArgs, tenant_id: str, session_id: str) -> dict:
    raise RuntimeError(_not_implemented("get_customer_details")["reason"])


async def _get_worker_location(args: GetWorkerLocationArgs, tenant_id: str, session_id: str) -> dict:
    raise RuntimeError(_not_implemented("get_worker_location")["reason"])


async def _find_nearest_technician(args: FindNearestTechnicianArgs, tenant_id: str, session_id: str) -> dict:
    raise RuntimeError(_not_implemented("find_nearest_technician")["reason"])


async def _dispatch_worker(args: DispatchWorkerArgs, tenant_id: str, session_id: str) -> dict:
    raise RuntimeError(_not_implemented("dispatch_worker")["reason"])


async def _send_notification(args: SendNotificationArgs, tenant_id: str, session_id: str) -> dict:
    raise RuntimeError(_not_implemented("send_notification")["reason"])


async def _update_crm(args: UpdateCRMArgs, tenant_id: str, session_id: str) -> dict:
    raise RuntimeError(_not_implemented("update_CRM")["reason"])


async def _escalate_to_human(args: EscalateToHumanArgs, tenant_id: str, session_id: str) -> dict:
    raise RuntimeError(_not_implemented("escalate_to_human")["reason"])


def build_tool_registry() -> ToolRegistry:
    registry = ToolRegistry()
    registry.register("create_ticket", CreateTicketArgs, _create_ticket)
    registry.register("update_ticket", UpdateTicketArgs, _update_ticket)
    registry.register("get_equipment_status", GetEquipmentStatusArgs, _get_equipment_status)
    registry.register("get_customer_details", GetCustomerDetailsArgs, _get_customer_details)
    registry.register("get_worker_location", GetWorkerLocationArgs, _get_worker_location)
    registry.register("find_nearest_technician", FindNearestTechnicianArgs, _find_nearest_technician)
    registry.register("dispatch_worker", DispatchWorkerArgs, _dispatch_worker, requires_confirmation=True)
    registry.register("send_notification", SendNotificationArgs, _send_notification)
    registry.register("update_CRM", UpdateCRMArgs, _update_crm)
    registry.register("escalate_to_human", EscalateToHumanArgs, _escalate_to_human)
    return registry
