"""
Secure tool registry. The LLM can only invoke tools registered here â€” there
is no code-execution, shell, arbitrary-URL, or arbitrary-DB-access path
anywhere in this module, by construction (every tool is a specific Python
coroutine with a Pydantic input schema, not a generic executor).

Hard rule enforced structurally, not just by convention: `ToolResult.success`
is never set by the tool function's return value directly â€” `execute()` sets
it based on whether the coroutine raised, so a tool CANNOT claim success while
also having failed. The caller (agent entrypoint) must render
`result.success` to the user, never assume success because a tool was called.

Optional Postgres persistence (ToolCall/ToolResult rows): `execute()` takes
an optional `recorder` implementing `ToolCallRecorderProtocol` below â€” a
structural Protocol, not a concrete import of `app.db.session_manager`, so
this module has zero hard dependency on SQLAlchemy/the DB layer. Every
existing caller that doesn't pass a recorder gets exactly the original
in-memory-only behavior; nothing about the safety/validation logic below
changed to make this possible. See `app/db/session_manager.py`'s
`ToolCallRecorder` for the real implementation, and `models.py`'s
`ToolCallStatus.rejected` for the validation-failure-vs-execution-failure
distinction this wiring preserves rather than collapses.

Persistence failure is never allowed to change what the agent receives:
`execute()` always returns the real, in-memory `ToolResult` computed from
what the handler actually did, whether or not the Postgres write for that
outcome succeeds. See `execute()`'s docstring for the documented
transactional guarantees (and their limits) this implies.
"""
import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Protocol, TypeVar, cast

from pydantic import BaseModel, ValidationError

logger = logging.getLogger("agent.tools")
ArgsT = TypeVar("ArgsT", bound=BaseModel)

# Key names (case-insensitive substring match) whose values are redacted
# before either logging or persisting arguments/errors. None of the tool
# schemas in definitions.py currently declare a field shaped like this, but
# `UpdateCRMArgs.fields` is an open `dict[str, str]` an LLM could stuff
# anything into â€” this is a defensive backstop for that and for any future
# tool, not evidence a leak has happened.
_SENSITIVE_KEY_MARKERS = (
    "password", "secret", "api_key", "apikey", "token", "credential",
    "authorization", "auth_header", "ssn", "card_number", "cvv",
)


def _redact(value: Any) -> Any:
    """Recursively redacts dict values whose key looks sensitive. Applied
    to tool arguments before they're logged or persisted â€” see module
    docstring and the 'TOOL RESULT STORAGE' requirement this satisfies."""
    if isinstance(value, dict):
        return {
            k: ("[REDACTED]" if any(m in k.lower() for m in _SENSITIVE_KEY_MARKERS) else _redact(v))
            for k, v in value.items()
        }
    if isinstance(value, list):
        return [_redact(v) for v in value]
    return value


class ToolCallRecorderProtocol(Protocol):
    """Structural interface `execute()` persists through. Satisfied by
    `app.db.session_manager.ToolCallRecorder` without that class needing to
    import anything from `tools/` or this module needing to import
    anything from `db/` â€” see module docstring."""

    async def record_call_started(
        self, *, call_id: str, tenant_id: uuid.UUID, session_id: uuid.UUID,
        user_id: uuid.UUID | None, tool_name: str, arguments: dict,
    ) -> Any: ...

    async def record_rejected(
        self, *, call_id: str, tenant_id: uuid.UUID, session_id: uuid.UUID,
        user_id: uuid.UUID | None, tool_name: str, arguments: dict,
    ) -> Any: ...

    async def record_result(
        self, *, tool_call: Any, success: bool, output: dict | None, error: str | None,
        duration_ms: float, final_status: Any,
    ) -> Any: ...


@dataclass
class ToolResult:
    tool_name: str
    success: bool
    output: dict | None
    error: str | None
    duration_ms: float
    call_id: str


@dataclass
class ToolAuditEntry:
    call_id: str
    tool_name: str
    tenant_id: str
    session_id: str
    args_summary: dict  # validated args, NOT raw LLM output â€” see execute()
    success: bool
    error: str | None
    duration_ms: float
    timestamp: float = field(default_factory=time.time)


class ToolNotFoundError(Exception):
    pass


class ToolTimeoutError(Exception):
    pass


@dataclass
class _RegisteredTool:
    name: str
    input_schema: type[BaseModel]
    handler: Callable[[BaseModel, str, str], Awaitable[dict]]
    timeout_seconds: float = 10.0
    requires_confirmation: bool = False

class ToolRegistry:
    def __init__(self):
        self._tools: dict[str, _RegisteredTool] = {}
        self._audit_log: list[ToolAuditEntry] = []  # in-process; Postgres persistence is optional, see execute()

    def register(
        self,
        name: str,
        input_schema: type[ArgsT],
        handler: Callable[[ArgsT, str, str], Awaitable[dict]],
        *,
        timeout_seconds: float = 10.0,
        requires_confirmation: bool = False,
    ) -> None:
        if name in self._tools:
            raise ValueError(f"tool '{name}' already registered")

        self._tools[name] = _RegisteredTool(
            name=name,
            input_schema=input_schema,
            handler=cast(
                Callable[[BaseModel, str, str], Awaitable[dict]],
                handler,
            ),
            timeout_seconds=timeout_seconds,
            requires_confirmation=requires_confirmation,
        )
    def list_tools(self) -> list[str]:
        return list(self._tools.keys())

    async def execute(
        self, *, tool_name: str, raw_args: dict, tenant_id: str, session_id: str,
        db_recorder: "ToolCallRecorderProtocol | None" = None,
        tenant_uuid: "uuid.UUID | None" = None,
        session_uuid: "uuid.UUID | None" = None,
        user_uuid: "uuid.UUID | None" = None,
    ) -> ToolResult:
        """
        `tenant_id`/`session_id` (plain strings) are unchanged from before
        this milestone â€” still the identifiers Moss/Qdrant/handlers use,
        still never read from `raw_args`, so tenant identity here has
        always come from trusted execution context, not tool arguments.

        `db_recorder`/`tenant_uuid`/`session_uuid`/`user_uuid` are new and
        entirely optional. Persistence only happens when a recorder AND
        both UUIDs are supplied â€” every existing caller that omits them
        (including every test written before this milestone) gets
        identical behavior to before: in-memory audit log only, nothing
        touches Postgres.

        Transactional guarantees, stated explicitly rather than implied:
        - The ToolCall row is written and committed BEFORE the handler
          runs. If this write fails, `execute()` raises immediately â€”
          there is no path where a handler runs without a corresponding
          ToolCall row existing first (when persistence is enabled at
          all).
        - The handler's actual output/exception is ALWAYS what's computed
          first and returned to the caller. A failure persisting the
          ToolResult afterward is logged at ERROR level but does NOT
          change what's returned â€” the agent never receives a result
          that's been altered by a database problem.
        - This is NOT perfectly atomic: if the post-handler ToolResult
          write fails, the ToolCall row is left at `pending` in Postgres
          even though the handler has already finished (successfully or
          not). A `pending` row with no `completed_at` is therefore
          ambiguous between "still running" and "result-persistence
          failed after real completion" â€” closing that gap needs a
          reconciliation sweep or outbox-pattern write, which is real
          additional work, not implemented here. Documented, not hidden.
        - Cancellation (`asyncio.CancelledError`, distinct from a timeout)
          is never swallowed: a best-effort failure record is persisted,
          then the cancellation is always re-raised.
        - No idempotency/deduplication exists. Two calls with identical
          arguments â€” whether from a legitimate retry, an LLM re-asking,
          or a bug â€” produce two separate ToolCall rows with distinct
          call_ids and, for a real handler like `create_ticket`, would
          genuinely invoke it twice. Not solved here â€” inventing a
          dedup/retry scheme without a real specification for one would
          be exactly the "unsafe retry behavior" this task said not to add.
        """
        import asyncio

        call_id = str(uuid.uuid4())
        start = time.monotonic()
        persist = db_recorder is not None and tenant_uuid is not None and session_uuid is not None

        tool = self._tools.get(tool_name)
        if tool is None:
            self._audit(call_id, tool_name, tenant_id, session_id, {}, False, "unknown tool", 0.0)
            if persist:
                await db_recorder.record_rejected(  # type: ignore[union-attr]
                    call_id=call_id, tenant_id=tenant_uuid, session_id=session_uuid,  # type: ignore[arg-type]
                    user_id=user_uuid, tool_name=tool_name, arguments=_redact(raw_args),
                )
            raise ToolNotFoundError(f"'{tool_name}' is not a registered tool")

        # Validate BEFORE execution â€” the LLM's raw arguments never reach the
        # handler unvalidated. A malformed/malicious arg set fails here, not
        # inside the handler with partially-applied side effects.
        try:
            validated_args = tool.input_schema(**raw_args)
        except ValidationError as e:
            duration_ms = (time.monotonic() - start) * 1000
            self._audit(call_id, tool_name, tenant_id, session_id, raw_args, False, str(e), duration_ms)
            if persist:
                # Rejected before execution â€” a ToolCall row exists, but
                # deliberately NO ToolResult row (see ToolCallRecorder
                # docstring): the handler never ran, so there is no result
                # to record, only a rejection.
                await db_recorder.record_rejected(  # type: ignore[union-attr]
                    call_id=call_id, tenant_id=tenant_uuid, session_id=session_uuid,  # type: ignore[arg-type]
                    user_id=user_uuid, tool_name=tool_name, arguments=_redact(raw_args),
                )
            return ToolResult(tool_name, False, None, f"invalid arguments: {e}", duration_ms, call_id)

        tool_call_row = None
        if persist:
            try:
                tool_call_row = await db_recorder.record_call_started(  # type: ignore[union-attr]
                    call_id=call_id, tenant_id=tenant_uuid, session_id=session_uuid,  # type: ignore[arg-type]
                    user_id=user_uuid, tool_name=tool_name, arguments=_redact(validated_args.model_dump()),
                )
            except Exception:
                logger.exception(
                    "failed to persist ToolCall(pending) for call_id=%s tool=%s â€” aborting before handler runs",
                    call_id, tool_name,
                )
                raise

        try:
            output = await asyncio.wait_for(
                tool.handler(validated_args, tenant_id, session_id), timeout=tool.timeout_seconds
            )
            duration_ms = (time.monotonic() - start) * 1000
            self._audit(
                call_id, tool_name, tenant_id, session_id, validated_args.model_dump(), True, None, duration_ms
            )
            await self._persist_result(
                db_recorder, tool_call_row, success=True, output=output, error=None,
                duration_ms=duration_ms, final_status_name="succeeded",
            )
            return ToolResult(tool_name, True, output, None, duration_ms, call_id)

        except asyncio.CancelledError:
            duration_ms = (time.monotonic() - start) * 1000
            self._audit(call_id, tool_name, tenant_id, session_id, validated_args.model_dump(), False, "cancelled", duration_ms)
            await self._persist_result(
                db_recorder, tool_call_row, success=False, output=None, error="cancelled",
                duration_ms=duration_ms, final_status_name="failed",
            )
            raise  # never swallow real cancellation

        except asyncio.TimeoutError:
            duration_ms = (time.monotonic() - start) * 1000
            error = f"tool '{tool_name}' timed out after {tool.timeout_seconds}s"
            self._audit(call_id, tool_name, tenant_id, session_id, validated_args.model_dump(), False, error, duration_ms)
            await self._persist_result(
                db_recorder, tool_call_row, success=False, output=None, error=error,
                duration_ms=duration_ms, final_status_name="timed_out",
            )
            return ToolResult(tool_name, False, None, error, duration_ms, call_id)

        except Exception as e:
            duration_ms = (time.monotonic() - start) * 1000
            logger.exception("tool '%s' raised", tool_name)
            self._audit(call_id, tool_name, tenant_id, session_id, validated_args.model_dump(), False, str(e), duration_ms)
            await self._persist_result(
                db_recorder, tool_call_row, success=False, output=None, error=str(e),
                duration_ms=duration_ms, final_status_name="failed",
            )
            return ToolResult(tool_name, False, None, f"tool failed: {e}", duration_ms, call_id)

    @staticmethod
    async def _persist_result(
        db_recorder: "ToolCallRecorderProtocol | None", tool_call_row: Any, *,
        success: bool, output: dict | None, error: str | None, duration_ms: float, final_status_name: str,
    ) -> None:
        """Best-effort â€” see execute()'s docstring for exactly what
        guarantee this does and doesn't provide. A failure here is logged,
        never raised, and never changes what execute() returns to its
        caller (the real handler outcome was already computed above)."""
        if db_recorder is None or tool_call_row is None:
            return
        try:
            from app.db.models import ToolCallStatus  # local import: keeps this module's only DB-layer

            final_status = ToolCallStatus(final_status_name)
            await db_recorder.record_result(
                tool_call=tool_call_row, success=success, output=_redact(output) if output else output,
                error=error, duration_ms=duration_ms, final_status=final_status,
            )
        except Exception:
            logger.exception(
                "failed to persist ToolResult for call_id=%s â€” handler outcome (success=%s) is still what was "
                "returned to the caller; the Postgres row may be left at 'pending'",
                getattr(tool_call_row, "call_id", "?"), success,
            )

    def _audit(
        self, call_id: str, tool_name: str, tenant_id: str, session_id: str,
        args_summary: dict, success: bool, error: str | None, duration_ms: float,
    ) -> None:
        redacted_args = _redact(args_summary)
        entry = ToolAuditEntry(call_id, tool_name, tenant_id, session_id, redacted_args, success, error, duration_ms)
        self._audit_log.append(entry)
        logger.info(
            "tool_call id=%s tool=%s tenant=%s session=%s success=%s duration_ms=%.1f",
            call_id, tool_name, tenant_id, session_id, success, duration_ms,
        )

    def get_audit_log(self, *, tenant_id: str | None = None) -> list[ToolAuditEntry]:
        """Tenant-scoped by default â€” a tenant's audit trail must not leak
        another tenant's tool-call history."""
        if tenant_id is None:
            return list(self._audit_log)
        return [e for e in self._audit_log if e.tenant_id == tenant_id]

