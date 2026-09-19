"""
Idempotency key derivation (Phase D).

WHY `call_id` IS NOT ENOUGH — the question the milestone asked:
`ToolRegistry.execute()` does `call_id = str(uuid.uuid4())` on every
invocation. Two identical logical requests therefore get two different
call_ids. `call_id` correlates ONE execution's ToolCall to its
ToolResult; it says nothing about whether that execution is a repeat.
Using it for duplicate suppression would suppress nothing. Hence a
separate derived key with its own unique constraint
(`uq_tool_calls_idempotency_key`).

WHAT GOES INTO THE KEY, and why each part:

    tenant_id   — trusted context. Two tenants making byte-identical
                  requests are NOT duplicates of each other, and must
                  never collide into one suppressed action.
    session_id  — scopes duplicate suppression to one conversation. The
                  same technician legitimately creating a second ticket
                  for the same pump NEXT WEEK is a new request, not a
                  retry. Without this, a keyed tool would refuse a
                  genuine repeat action forever.
    tool_name   — different tools with coincidentally identical args are
                  unrelated.
    declared arg fields — only the fields the contract names in
                  `idempotency_fields`, so incidental variation (an LLM
                  rewording a free-text description between retries)
                  doesn't defeat suppression.

NOTE tenant_id and session_id come from TRUSTED CONTEXT, never from tool
arguments. A model cannot influence the key's scope, only the declared
business fields within it.

The key is a SHA-256 hex digest: fixed length (fits the String(128)
column), no delimiter-injection ambiguity, and it does not leak argument
values into a column that gets read during incident review.
"""
import hashlib
import json
import uuid

from pydantic import BaseModel

from app.tools.contracts import IdempotencyMode, ToolContract


def derive_idempotency_key(
    *, contract: ToolContract, tenant_id: uuid.UUID, session_id: uuid.UUID, validated_args: BaseModel,
) -> str | None:
    """Returns None for tools that don't declare KEYED idempotency — which
    is what leaves `tool_calls.idempotency_key` NULL for them, so the
    unique constraint binds only rows that opted in (Postgres treats
    NULLs as distinct)."""
    if contract.idempotency is not IdempotencyMode.KEYED:
        return None

    dumped = validated_args.model_dump()
    # Only the declared fields. sort_keys so dict ordering can't change
    # the digest; default=str so UUID/datetime values serialize stably.
    material = {name: dumped.get(name) for name in sorted(contract.idempotency_fields)}
    payload = json.dumps(
        {
            "tenant_id": str(tenant_id),
            "session_id": str(session_id),
            "tool": contract.name,
            "args": material,
        },
        sort_keys=True,
        default=str,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
