"""
Unit tests for app.tools.definitions.build_tool_registry() and its stub
handlers (everything except create_ticket, which has its own dedicated
test file backed by a real Postgres-persisted flow).

These confirm the contract the module's docstring promises: every stub
validates its input correctly and reports success=False with an honest
"no real backend" reason -- it never fabricates a success.
"""
import pytest

from app.tools.definitions import build_tool_registry
from app.tools.registry import ToolRegistry

# (tool_name, valid_args) for every stub except create_ticket.
STUB_TOOLS = [
    ("update_ticket", {"ticket_id": "T-1", "status": "resolved"}),
    ("get_equipment_status", {"equipment_id": "EQ-1"}),
    ("get_customer_details", {"customer_id": "CUST-1"}),
    ("get_worker_location", {"worker_id": "W-1"}),
    ("find_nearest_technician", {"site_id": "SITE-1"}),
    ("dispatch_worker", {"worker_id": "W-1", "site_id": "SITE-1"}),
    ("send_notification", {"recipient_id": "W-1", "message": "hello"}),
    ("update_CRM", {"customer_id": "CUST-1", "fields": {"name": "Jane"}}),
    ("escalate_to_human", {"session_id": "S-1", "reason": "needs a human"}),
]


def test_build_tool_registry_registers_all_ten_tools():
    registry = build_tool_registry()
    assert isinstance(registry, ToolRegistry)
    assert sorted(registry.list_tools()) == sorted(
        ["create_ticket"] + [name for name, _ in STUB_TOOLS]
    )


def test_dispatch_worker_requires_confirmation():
    registry = build_tool_registry()
    assert registry._tools["dispatch_worker"].requires_confirmation is True


def test_registering_a_duplicate_tool_name_raises():
    registry = build_tool_registry()
    existing = registry._tools["escalate_to_human"]
    with pytest.raises(ValueError, match="escalate_to_human"):
        registry.register("escalate_to_human", existing.input_schema, existing.handler)


@pytest.mark.asyncio
@pytest.mark.parametrize("tool_name,valid_args", STUB_TOOLS)
async def test_stub_tool_reports_honest_failure_not_fake_success(tool_name, valid_args):
    registry = build_tool_registry()

    result = await registry.execute(
        tool_name=tool_name, raw_args=valid_args, tenant_id="tenant-a", session_id="session-a"
    )

    assert result.success is False
    assert result.output is None
    assert "No real backend is wired up" in result.error
    assert "does not fabricate success" in result.error


@pytest.mark.asyncio
async def test_stub_tool_still_validates_input_before_running(monkeypatch):
    """Even though the handler always fails, validation must run first --
    a malformed call should fail with a validation error, not the generic
    'no backend' message, and the handler must never be invoked."""
    registry = build_tool_registry()

    result = await registry.execute(
        tool_name="update_ticket",
        raw_args={"ticket_id": "T-1", "status": "not-a-real-status"},
        tenant_id="tenant-a",
        session_id="session-a",
    )

    assert result.success is False
    assert "invalid arguments" in result.error
    assert "No real backend" not in result.error


@pytest.mark.asyncio
async def test_send_notification_rejects_invalid_channel():
    registry = build_tool_registry()

    result = await registry.execute(
        tool_name="send_notification",
        raw_args={"recipient_id": "W-1", "message": "hi", "channel": "carrier-pigeon"},
        tenant_id="tenant-a",
        session_id="session-a",
    )

    assert result.success is False
    assert "invalid arguments" in result.error


@pytest.mark.asyncio
async def test_send_notification_defaults_channel_to_push():
    registry = build_tool_registry()

    result = await registry.execute(
        tool_name="send_notification",
        raw_args={"recipient_id": "W-1", "message": "hi"},
        tenant_id="tenant-a",
        session_id="session-a",
    )

    # Still fails (stub), but must have passed validation to get there.
    assert "invalid arguments" not in (result.error or "")
    assert "No real backend is wired up for 'send_notification'" in result.error
