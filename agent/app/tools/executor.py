"""
Contract-aware execution for tools that declare a `ToolContract`.

DELIBERATELY A SEPARATE PATH, NOT A REWRITE OF `registry.py`. The task
said not to break call_id correlation, the rejected/failed/timed_out
states, tenant isolation, or user_id propagation — and `registry.py`
already implements all of those, tested by 26 passing tests. Rewriting it
to add contracts would put every one of those guarantees at risk for no
benefit. This executor reuses the SAME `ToolCallRecorder` lifecycle and
the SAME status semantics; it adds only what contracts require:
authorization, idempotency suppression, and contract-declared timeouts.

LIFECYCLE (identical in shape to registry.py's, by design):

    authorize            -> AuthorizationError  => rejected, no ToolResult
    validate args        -> ValidationError     => rejected, no ToolResult
    derive idempotency key
    check for prior execution -> found          => return ORIGINAL result,
                                                   no second side effect
    ToolCall(pending) committed BEFORE the handler runs
    handler (bounded by contract.timeout_seconds)
    ToolResult + final status: succeeded | failed | timed_out

TRANSACTIONAL HONESTY, unchanged from session 6 and restated because this
is now a tool with a REAL external side effect: the handler runs first and
its true outcome is always what the agent receives. If persisting the
ToolResult afterwards fails, that is logged, the agent still gets the
truth, and the ToolCall row may be left at `pending`. For an
external-side-effect tool this means a `pending` row can mean "the ticket
may exist but we failed to record the result." That is a real
reconciliation gap, documented rather than hidden: closing it needs an
outbox or a reconciliation sweep against the provider, which is not built
here.
"""
import asyncio
import logging
import time
import uuid

from pydantic import BaseModel, ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import ToolCall, ToolCallStatus, ToolResult
from app.security.identity import AuthenticatedIdentity
from app.tools.contracts import IdempotencyMode, ToolContract
from app.tools.create_ticket import AuthorizationError, authorize
from app.tools.idempotency import derive_idempotency_key
from app.tools.registry import ToolResult as InMemoryToolResult
from app.tools.registry import _redact

logger = logging.getLogger("agent.tools.executor")


class ContractToolExecutor:
    def __init__(self, db: AsyncSession, recorder):
        self._db = db
        self._recorder = recorder

    async def execute(
        self, *, contract: ToolContract, handler, raw_args: dict, identity: AuthenticatedIdentity | None,
        session_uuid: uuid.UUID,
    ) -> InMemoryToolResult:
        call_id = str(uuid.uuid4())
        start = time.monotonic()

        # --- authorization, before anything else touches the DB ---
        try:
            authorize(contract, identity)
        except AuthorizationError as e:
            duration_ms = (time.monotonic() - start) * 1000
            logger.warning("tool '%s' authorization rejected: %s", contract.name, e)
            if identity is not None:
                await self._recorder.record_rejected(
                    call_id=call_id, tenant_id=identity.tenant_id, session_id=session_uuid,
                    user_id=identity.user_id, tool_name=contract.name, arguments=_redact(raw_args),
                )
            # With no identity there is no tenant to attribute a row to —
            # same principle as ADR 001: never claim an unverified tenant.
            return InMemoryToolResult(contract.name, False, None, f"not authorized: {e}", duration_ms, call_id)

        assert identity is not None  # narrowed by authorize()

        # --- validation, before the handler ---
        try:
            validated: BaseModel = contract.input_schema(**raw_args)
        except ValidationError as e:
            duration_ms = (time.monotonic() - start) * 1000
            await self._recorder.record_rejected(
                call_id=call_id, tenant_id=identity.tenant_id, session_id=session_uuid,
                user_id=identity.user_id, tool_name=contract.name, arguments=_redact(raw_args),
            )
            return InMemoryToolResult(contract.name, False, None, f"invalid arguments: {e}", duration_ms, call_id)

        # --- idempotency: has this exact logical request already run? ---
        idem_key = derive_idempotency_key(
            contract=contract, tenant_id=identity.tenant_id, session_id=session_uuid, validated_args=validated,
        )
        if idem_key is not None:
            prior = await self._find_prior_result(idem_key)
            if prior is not None:
                duration_ms = (time.monotonic() - start) * 1000
                logger.info(
                    "tool '%s' deduplicated against prior call_id=%s — handler NOT re-executed",
                    contract.name, prior[0].call_id,
                )
                output = dict(prior[1].output or {})
                output["deduplicated"] = True
                return InMemoryToolResult(contract.name, prior[1].success, output, prior[1].error, duration_ms, call_id)

        # --- ToolCall(pending) committed BEFORE the side effect ---
        tool_call_row = await self._recorder.record_call_started(
            call_id=call_id, tenant_id=identity.tenant_id, session_id=session_uuid,
            user_id=identity.user_id, tool_name=contract.name, arguments=_redact(validated.model_dump()),
            idempotency_key=idem_key,
        )

        # --- execute, bounded by the contract's timeout ---
        try:
            output_model = await asyncio.wait_for(
                handler(validated, identity=identity), timeout=contract.timeout_seconds
            )
            duration_ms = (time.monotonic() - start) * 1000
            output = output_model.model_dump()
            success = bool(output.get("success", False))
            await self._persist(
                tool_call_row, success=success, output=output,
                error=None if success else output.get("message"),
                duration_ms=duration_ms,
                status=ToolCallStatus.succeeded if success else ToolCallStatus.failed,
            )
            return InMemoryToolResult(contract.name, success, output, None if success else output.get("message"), duration_ms, call_id)

        except asyncio.TimeoutError:
            duration_ms = (time.monotonic() - start) * 1000
            error = f"tool '{contract.name}' timed out after {contract.timeout_seconds}s"
            logger.warning(
                "%s — NOTE: for an EXTERNAL_SIDE_EFFECT tool the upstream action may still have "
                "succeeded; this is recorded as timed_out, never as success", error,
            )
            await self._persist(
                tool_call_row, success=False, output=None, error=error,
                duration_ms=duration_ms, status=ToolCallStatus.timed_out,
            )
            return InMemoryToolResult(contract.name, False, None, error, duration_ms, call_id)

        except asyncio.CancelledError:
            duration_ms = (time.monotonic() - start) * 1000
            await self._persist(
                tool_call_row, success=False, output=None, error="cancelled",
                duration_ms=duration_ms, status=ToolCallStatus.failed,
            )
            raise  # never swallow cancellation

        except Exception as e:
            duration_ms = (time.monotonic() - start) * 1000
            logger.exception("tool '%s' raised", contract.name)
            await self._persist(
                tool_call_row, success=False, output=None, error=str(e),
                duration_ms=duration_ms, status=ToolCallStatus.failed,
            )
            return InMemoryToolResult(contract.name, False, None, f"tool failed: {e}", duration_ms, call_id)

    async def _find_prior_result(self, idem_key: str):
        """Returns (ToolCall, ToolResult) for a prior SUCCESSFUL execution
        of this key, or None.

        Only `succeeded` calls suppress a repeat. A prior failure or
        timeout must NOT block a genuine retry — the whole point of
        retrying a transient failure is to actually try again.
        """
        result = await self._db.execute(
            select(ToolCall).where(
                ToolCall.idempotency_key == idem_key, ToolCall.status == ToolCallStatus.succeeded
            )
        )
        call = result.scalar_one_or_none()
        if call is None:
            return None
        res = (await self._db.execute(select(ToolResult).where(ToolResult.tool_call_id == call.id))).scalar_one_or_none()
        if res is None:
            return None
        return call, res

    async def _persist(self, tool_call_row, *, success, output, error, duration_ms, status) -> None:
        """Best-effort — a persistence failure never changes what the
        caller receives. See module docstring on the reconciliation gap
        this leaves for external-side-effect tools.

        IDEMPOTENCY KEY RELEASE: the key reserves its unique slot while a
        call is in flight — that is what makes two CONCURRENT identical
        requests race-safe, since only one can insert. But if the call
        ends in any state other than `succeeded`, the key is cleared, so
        a genuine retry can run.

        Found by a test, not by inspection: `test_failed_execution_does_not
        _block_a_genuine_retry` failed with a UniqueViolationError on the
        retry's INSERT. `_find_prior_result` already only suppressed on
        `succeeded`, but the database constraint blocked the retry before
        that logic was ever consulted. Without this release, one transient
        provider outage would permanently prevent the user from ever
        creating that ticket.
        """
        if status is not ToolCallStatus.succeeded:
            tool_call_row.idempotency_key = None
        try:
            await self._recorder.record_result(
                tool_call=tool_call_row, success=success, output=output, error=error,
                duration_ms=duration_ms, final_status=status,
            )
        except Exception:
            logger.exception(
                "failed to persist ToolResult for call_id=%s; handler outcome (success=%s) is still "
                "what was returned. ToolCall may remain 'pending' — see module docstring.",
                getattr(tool_call_row, "call_id", "?"), success,
            )
