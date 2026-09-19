# Session Status Report — Real-Time Voice-to-Action Platform

This covers Phases 1–4 (audit, comparison, selection, ADR) fully, plus a
first slice of Phase 5–10 (LiveKit + Moss integration skeleton, secure tool
registry). It does NOT claim phases 11–20 are complete. Per your own
instruction — "do not exaggerate completion" — every item below is marked
IMPLEMENTED, PARTIAL, or NOT STARTED against what was actually run in this
session, not what looks plausible.

## What was actually verified this session (not just written)

- **Moss SDK is real**: installed `moss==1.11.0` from PyPI, inspected its
  actual `.pyi` type stubs rather than trusting its README. `MossClient`
  requires real `project_id`/`project_key` — cannot be used with zero
  credentials, contrary to what a surface reading might suggest.
- **primd-main is NOT part of Moss**: its own README explicitly positions it
  as a competing alternative ("The only direct overlap is Moss/InferEdge").
  Excluded from the architecture rather than force-fit, with the reasoning
  documented in the ADR above.
- **DreamSupport (Voice-AI-Agent-master) audited by reading actual code**,
  not README claims: confirmed real Sarvam STT/TTS calls, real
  `sentence-transformers` embeddings, real Qdrant usage, and a genuinely
  working romanized-language override — all better than the mocks in our
  earlier `voice-platform` gateway project.
- **`livekit-agents`, `livekit-plugins-google`, `livekit-plugins-silero`,
  `livekit-plugins-deepgram` are real, installed, and their APIs match what
  the code uses** — checked via `inspect.signature`, not assumed.
- **`app/agent_entrypoint.py` imports cleanly** against all of the above,
  confirming the code is syntactically and structurally correct against the
  real SDK surface.
- **The ported romanized-LID logic was tested against your spec's own
  mandatory example** ("Mujhe Hyderabad ka weather batao") — and the honest
  result is it does NOT trip the override (1 marker hit, needs 2). A longer,
  more typical utterance does. This is a real finding about the limits of
  the ported logic, not a passed/failed checkbox glossed over.
- **`ContextOrchestrator` routing verified with 3 test cases**: a plain
  conversational turn queries Moss only; a "maintenance history" query
  correctly adds Qdrant; a "status right now" query correctly adds the live
  API — confirming the "don't blindly query every system" requirement holds
  in code, not just in the docstring.
- **`ToolRegistry` safety properties verified with 4 test cases**:
  `create_ticket` cannot report fabricated success (no backend exists, and
  it correctly says so); a malicious/malformed argument is rejected by
  schema validation before the handler ever runs; an unregistered tool name
  is rejected outright; the audit log is correctly tenant-scoped.

## Component status

| Component | Status | Notes |
|---|---|---|
| Repository audit (all 3 repos) | **IMPLEMENTED** | Real code read, not README-trusted |
| Architecture Decision Record | **IMPLEMENTED** | See conversation above |
| `MossContextProvider` | **PARTIAL** | Correct against real SDK; NOT run against a live Moss project (no credentials, no network path from this sandbox) |
| `QdrantKnowledgeProvider` | **PARTIAL** | Tenant-isolation filter pattern already proven in the earlier gateway project; collection creation verified against Qdrant's `:memory:` local mode here, real embeddings not yet run end-to-end (see below) |
| `SentenceTransformerEmbedder` | **NOT VERIFIED END-TO-END** | Code is correct against the real `sentence-transformers` API; this sandbox has no network path to huggingface.co to actually download `multilingual-e5-small` weights, so the model load itself has not been run here |
| Romanized LID | **IMPLEMENTED, tested, one real limitation documented** | See above |
| `ContextOrchestrator` | **IMPLEMENTED, tested** | Rule-based heuristic, not learned — documented as a known simplification |
| `ToolRegistry` + required tool set | **IMPLEMENTED, tested** | All 10 required tools registered with strict Pydantic schemas; every handler is an honest stub (no real ticketing/CRM/dispatch backend exists in any audited repo) |
| LiveKit agent entrypoint | **PARTIAL** | Imports cleanly against real SDKs; NOT run against a live LiveKit room (no `LIVEKIT_URL`/`LIVEKIT_API_KEY`/`LIVEKIT_API_SECRET`, no network path to LiveKit Cloud from this sandbox) |
| Real Sarvam STT/TTS as LiveKit plugins | **NOT STARTED** | DreamSupport's Sarvam integration is Pipecat-`FrameProcessor`-shaped; needs a genuine rewrite as `livekit.agents.stt.STT`/`tts.TTS` subclasses. Deepgram used as a placeholder in the entrypoint specifically because it already has an official LiveKit plugin — swap once the Sarvam port exists |
| Frontend | **NOT STARTED** | No frontend existed in any of the three repos or the earlier gateway despite the prompt's finding note claiming one did; that claim was inaccurate and is corrected here |
| PostgreSQL schema/migrations for this platform | **NOT STARTED** | Earlier gateway's Postgres/Alembic patterns are reusable but not yet adapted to this platform's tenant/session/tool-call/audit schema |
| Security audit (prompt injection defense, SSRF, etc.) | **NOT STARTED** | Tool registry's input validation is a partial security control; the fuller audit list (CORS, WebSocket auth for LiveKit tokens, prompt-injection separation between system/user/retrieved-content) has not been done |
| Load/performance testing | **NOT STARTED** | No numbers to report; would be dishonest to estimate them |
| Deployment (Docker, k8s, LiveKit Cloud config) | **NOT STARTED** | |

## Hard blockers for continuing past this point

Real credentials and real network access to these are required and don't
exist in this environment — no amount of further code-writing here resolves
them:
- `MOSS_PROJECT_ID` / `MOSS_PROJECT_KEY` (moss.dev signup)
- `LIVEKIT_URL` / `LIVEKIT_API_KEY` / `LIVEKIT_API_SECRET` (LiveKit Cloud or self-hosted)
- `GEMINI_API_KEY` (you said this comes via your cloud secret manager — correct, never asked for here)
- Sarvam API key, if you want the real Indic STT/TTS port
- A real operational backend (ticketing/CRM/dispatch system) for the 10 tools to actually call

## Session 2: Sarvam STT/TTS ported to real LiveKit plugins

Picked up exactly where session 1 left off — no re-audit, no architecture
changes, no changes to `moss_provider.py`, `qdrant_provider.py`,
`orchestrator.py`, `tools/registry.py`, or `tools/definitions.py`, per
explicit instruction. Only new files plus `agent_entrypoint.py`'s
STT/TTS wiring and `requirements.txt` changed.

### What was implemented

- `app/voice_providers/sarvam/stt.py` — real LiveKit `stt.STT` plugin.
  Batch path (`_recognize_impl`) and streaming path (`SpeechStream._run`)
  both ported from Voice-AI-Agent-master's `SarvamSTTService`/
  `SarvamSTTStreamingService` — same URLs, same request/response shapes,
  same fail-safe-to-batch design on any streaming failure.
- `app/voice_providers/sarvam/tts.py` — real LiveKit `tts.TTS` plugin.
  Batch (`ChunkedStream`) and streaming (`SynthesizeStream`) both ported
  from `SarvamTTSService`/`SarvamTTSStreamingService`, including digit-
  spelling via the verbatim-copied `num_to_words.py`.
- `app/context/num_to_words.py` — copied verbatim from the source, not
  rewritten (149 lines, self-contained, no reason to touch working logic).
- Romanized-LID override (`romanized_lid.py`, from session 1) wired into
  every STT transcript path, batch and streaming, not just one.
- `agent_entrypoint.py` updated to use the real Sarvam plugins instead of
  the Deepgram placeholders from session 1.
- 7 tests added (`tests/test_sarvam_stt.py`, `tests/test_sarvam_tts.py`),
  mocking only the HTTP/WS boundary. **All 7 pass.**

### A real bug found and fixed mid-session, not glossed over

The first draft of `SpeechStream._run()` wrapped the whole connect-and-
process sequence in a `while True:` retry loop. If Sarvam's WebSocket
failed to connect *before any audio frame was ever read*, the
`has_ended` flag needed to trigger the fallback path was never set, so
the fallback branch was unreachable and the loop retried the identical
failing connection forever — no emitted event, no error, no timeout,
just a hang. This was caught because
`test_streaming_falls_back_to_batch_on_ws_connect_failure` hung instead
of passing, not because it was reasoned about in advance. Fixed by
restructuring to a single pass over the input channel with lazy
per-utterance connection — confirmed via the same test now passing in
under 2 seconds.

A second, smaller bug (invented a `combine_frames_to_wav` helper that
doesn't exist in the real API) was caught by import/type inspection
before it ever reached a test run — `AudioBuffer` is
`list[rtc.AudioFrame]`, not raw bytes; fixed by buffering real
`AudioFrame` objects instead.

### Verification breakdown, exactly as required

- **SDK/API verified**: `livekit.agents.stt`/`tts` base classes
  (`STT`, `SpeechStream`, `TTS`, `ChunkedStream`, `SynthesizeStream`,
  `AudioEmitter`, `SpeechEvent`, `SpeechData`, `LanguageCode`) inspected
  via `inspect.signature`/`inspect.getsource` against the real installed
  `livekit-agents` package. The installed `deepgram` plugin was read in
  full as the reference pattern for a conforming implementation.
- **Adapter tested locally**: 7 pytest tests, all passing, covering batch
  success + romanized-override application, HTTP error mapping, timeout
  mapping, the WS-connect-failure-falls-back-to-batch path (the one that
  caught the infinite-loop bug), TTS success + audio decoding, TTS
  never-fabricates-on-empty-response, and TTS digit-spelling actually
  reaching the outgoing request body.
- **Live Sarvam call tested**: **NOT DONE.** No `SARVAM_API_KEY` exists in
  this environment and no network path to `api.sarvam.ai` exists from
  this sandbox. Every line of the HTTP/WS protocol logic is extracted
  from real, working source code — not guessed — but zero real network
  calls to Sarvam have been made. This remains a hard blocker exactly
  like the Moss/LiveKit/Gemini live-verification gaps from session 1.

### Static/type checks run

`mypy app/voice_providers/sarvam/stt.py app/voice_providers/sarvam/tts.py
app/agent_entrypoint.py --ignore-missing-imports` — **clean**, after
fixing one real finding (`SpeechData(language=str)` where the field
expects `LanguageCode`; fixed by wrapping explicitly, matching the real
deepgram plugin's own convention rather than relying on the dataclass's
runtime coercion).

Running `mypy app/` more broadly surfaces pre-existing type-looseness in
session 1's files (`moss_provider.py`, `qdrant_provider.py`,
`orchestrator.py`, `tools/definitions.py`) — untouched per this session's
explicit instruction not to rewrite that code. Flagged here, not fixed:
- `moss_provider.py`: `create_index`/`add_docs` take `list[DocumentInfo]`,
  currently passed `list[dict]`; `delete_docs` return type mismatch
- `qdrant_provider.py`: `Filter.must` list-invariance issue; a
  `payload.get(...)` call on a `dict | None` without a null check
- `tools/definitions.py`: each tool handler's args type doesn't
  structurally satisfy the registry's `Callable[[BaseModel, ...]]`
  signature (works at runtime via Pydantic subclassing, but isn't
  provably sound to mypy)
- `orchestrator.py`: `live_api` being `Optional` isn't narrowed before
  `.retrieve()` is called in the `needs_live` branch

None of these caused a test failure and none are new this session — they
predate this task's scope.

### Secret scan performed

- Grepped for hardcoded API-key-shaped strings (`grep -rn -E
  "(api[_-]?key|secret|password|token)\s*[:=]\s*['\"][a-zA-Z0-9_\-]{10,}"`)
  across the whole `agent/` tree — clean.
- Grepped for known key-format patterns (`sk-...`, `AIza...`) — clean.
- Confirmed every real credential reference (`SARVAM_API_KEY`,
  `GEMINI_API_KEY` via the LLM plugin, `MOSS_PROJECT_ID`/`_KEY`) goes
  through `os.environ[...]`, never a literal.
- Confirmed test files only ever use the literal string `"fake-key"`.
- No git repository exists yet for this project, so there's no commit
  history to leak from — but there was also no `.gitignore`, which is
  itself a gap; added one this session covering `.env`/`.env.*` (with
  `.env.example` explicitly allowed) so secrets can't land in a future
  commit by accident.

### Updated component status

| Component | Status | Notes |
|---|---|---|
| Sarvam STT (batch) | **IMPLEMENTED, tested, SDK-verified** | Not live-tested (see above) |
| Sarvam STT (streaming) | **IMPLEMENTED, tested, SDK-verified** | Fallback-on-failure path specifically tested; not live-tested |
| Sarvam TTS (batch) | **IMPLEMENTED, tested, SDK-verified** | Not live-tested |
| Sarvam TTS (streaming) | **IMPLEMENTED, SDK-verified, NOT unit-tested** | No test written yet for `SynthesizeStream._run()` specifically — the 3 TTS tests cover `ChunkedStream` (batch) only. Flagged as a real gap, not silently left implied-covered. |
| `agent_entrypoint.py` | **PARTIAL** | Now wired to real Sarvam plugins instead of Deepgram placeholders; still not run against a live LiveKit room |
| Digit-spelling (`num_to_words.py`) | **IMPLEMENTED** | Verbatim port, exercised indirectly by the digit-spelling test but not exhaustively (only the "65" case is tested; 0, negative, >9999, and non-hi/mr/en languages are untested edge cases) |

## Session 3: closed the TTS streaming test gap

Picked up the single item flagged at the end of session 2 — no other files
touched.

### What was implemented

Two new tests in `tests/test_sarvam_tts.py`, driving the real
`SynthesizeStream` through its actual public interface
(`push_text`/`flush`/`end_input` + async iteration), not by calling `_run`
directly the way the batch `ChunkedStream` tests do — this exercises the
real base-class task lifecycle, not a shortcut around it:

- `test_synthesize_stream_pushes_audio_and_ends_on_final_event` — confirms
  the config message is sent before any text, digit-spelling is applied to
  the actual outgoing text message, decoded audio bytes reach the yielded
  `SynthesizedAudio` frames, and the WS is closed cleanly on the server's
  `final` event.
- `test_synthesize_stream_ws_connect_failure_raises_not_hangs` — confirms
  a WS connect failure raises `APIConnectionError` promptly rather than
  hanging or silently yielding zero audio.

### Two real bugs found while writing this, neither hidden

1. **A genuine race condition, caught by the test failing on first run,
   not reasoned about in advance.** `SynthesizeStream._run()` does
   `sender_task = asyncio.create_task(sender())` and then immediately
   enters `async for msg in ws:` — task creation only *schedules* the
   sender, it doesn't run it. Against a fake WS that yields queued
   messages with no real I/O wait, the receive loop could process and
   break on the `final` event before the sender task ever got a turn to
   send anything. Investigated before concluding this was a test-realism
   problem rather than a production bug: against a **real** Sarvam
   server, the server cannot emit `audio`/`final` before receiving any
   text to synthesize, so the network round-trip itself enforces the
   ordering that the fake WS's instant message availability didn't. Fixed
   by making the fake WS wait for a real "text" message to have been sent
   before yielding any server message — this is a more accurate
   simulation of real request-response causality, not a weakened
   assertion. Documented in the test file itself, not just here.
2. **A trivial but real bug**: the fake WS class used `asyncio.Event()`
   without an `import asyncio` in the test file. Caught immediately by
   the resulting `NameError` on the very next test run — fixed in one
   line.

### Updated verification status

| Component | Before session 3 | After session 3 |
|---|---|---|
| Sarvam TTS (streaming) | SDK-verified, **NOT unit-tested** | **SDK-verified, tested** (2 tests, both passing) |

Live-Sarvam-call status is unchanged: still NOT DONE, same hard blocker
(no `SARVAM_API_KEY`, no network path to `api.sarvam.ai` from this
sandbox) as every other live-service gap in this project.

### Checks run this session

- Full test suite: **9/9 passed** (7 from session 2 + 2 new)
- `mypy` on `stt.py`, `tts.py`, `agent_entrypoint.py`, and the new test
  file: **clean**
- Secret scan (same two grep patterns as session 2): **clean**
- No changes to `moss_provider.py`, `qdrant_provider.py`,
  `orchestrator.py`, `tools/registry.py`, `tools/definitions.py` —
  confirmed by only editing `tests/test_sarvam_tts.py` this session

## Session 4: PostgreSQL schema + Alembic migrations

Picked up the first item from session 3's ordered list. No files under
`app/context/`, `app/tools/`, `app/voice_providers/`, or
`app/agent_entrypoint.py` were touched this session — confirmed by the
diff being limited to `app/db/` (new), `alembic/` (new), `alembic.ini`
(new), `tests/test_db_tenant_isolation.py` (new), and
`requirements.txt`/`requirements-dev.txt` (appended to).

### Starting-state finding, checked rather than assumed

The instruction said to "first inspect the existing SQLAlchemy models."
There were none to inspect — checked, not assumed:
- This project's own `app/memory/` package is still the empty placeholder
  it's been since session 1.
- Grepped the audited `Voice-AI-Agent-master` source for
  `sqlalchemy|asyncpg|psycopg|postgres` across its entire `app/` tree:
  zero matches. `app/store.py` (412 lines) — despite the name — is Qdrant
  collection management, not a relational store. DreamSupport has no
  Postgres layer at all; it's Qdrant + in-memory only.

This is a clean build against the spec's 7 required domain areas, not a
migration from prior code. Stated plainly in `app/db/models.py`'s own
docstring, not just here.

### What was implemented

- `app/db/base.py` — async engine/session setup, `DATABASE_URL` read from
  environment only (never hardcoded, never in `alembic.ini`).
- `app/db/models.py` — `Tenant`, `User`, `VoiceSession`, `ConversationTurn`,
  `ToolCall`, `ToolResult`, `AuditLog`. Every tenant-scoped table carries
  `tenant_id` directly (not just derivable via a join) specifically so
  tenant-isolation queries are a single indexed `WHERE`, not a join
  someone could forget. Tenant delete cascades to all child rows; user
  delete sets `user_id` to `NULL` on their history rather than deleting
  it. `ToolCall.call_id` is a unique column designed to correlate with
  the `call_id` already generated by `app/tools/registry.py`'s
  `execute()` — noted as the join point for future wiring, not wired up
  this session (that would mean touching `registry.py`, out of scope).
- Alembic initialized (async template), `env.py` wired to `Base.metadata`
  and to `DATABASE_URL` from the environment.
- One migration (`994d7e49f196_initial_schema.py`), generated via
  `alembic revision --autogenerate` against a real, freshly-installed
  Postgres 16 instance in this sandbox — not handwritten and not
  generated against sqlite.
- 5 new tests in `tests/test_db_tenant_isolation.py`, run against that
  real Postgres instance.

### Two real bugs found by actually running the migration, not by reading it

1. **Downgrade left 5 orphaned Postgres ENUM types behind.**
   `alembic downgrade base` followed by `alembic upgrade head` failed
   with `DuplicateObjectError: type "tenant_status" already exists`.
   Root cause: SQLAlchemy's `Enum` column type issues `CREATE TYPE` when
   a table using it is created, but `drop_table` does not automatically
   `DROP TYPE` — autogenerate's downgrade only drops tables. Fixed by
   hand-adding 5 explicit `sa.Enum(name=...).drop(op.get_bind(),
   checkfirst=True)` calls at the end of `downgrade()`, and verified by
   actually re-running the full upgrade → downgrade → upgrade cycle
   against the real database afterward — it now succeeds. This would
   have shipped broken if "the migration file exists and upgrade works
   once" had been treated as sufficient, which is exactly why the spec
   asked for the round-trip test specifically.
2. **A test bug, correctly distinguished from a schema bug before fixing
   anything.** `test_user_delete_preserves_history_with_null_user_id`
   failed on first run: after deleting a user, re-querying the
   `ConversationTurn` in the *same* SQLAlchemy session showed the old
   `user_id`, not `NULL`. Before touching anything, checked the actual
   database value with a raw `asyncpg` connection that bypasses the ORM
   entirely — it was correctly `NULL`. The `ON DELETE SET NULL`
   constraint works; the test was reading a stale in-memory object
   because `expire_on_commit=False` (set deliberately in `db/base.py`
   for performance) means SQLAlchemy doesn't know a DB-side cascade
   changed a column under an already-loaded object. Fixed by reading
   back through a **fresh** session — which is also the realistic case,
   since every real request gets its own session via `get_db()`.

### Verification actually performed, per the spec's own checklist

1. `alembic upgrade head` against a clean Postgres — ✅ ran, not just written
2. All 7 tables + `alembic_version` verified via `psql \dt` — ✅ all present
3. All 12 foreign keys verified via `information_schema` query, including
   cascade rules (`CASCADE` vs `SET NULL`) matching the model design exactly — ✅
4. All 6 indexes (`ix_tenants_slug`, `ix_users_tenant_id`,
   `ix_voice_sessions_tenant_id`, `ix_conversation_turns_tenant_session`,
   `ix_tool_calls_tenant_session`, `ix_audit_logs_tenant_id_created_at`)
   verified via `psql \di` — ✅ all present, matching every query path
   the spec listed (tenant→users, tenant→sessions, tenant+session→turns,
   tenant+session→tool_calls, tool_call→tool_result via unique FK,
   tenant→audit_events)
5. 3 unique constraints (`uq_users_tenant_external_id`,
   `tool_calls_call_id_key`, `tool_results_tool_call_id_key`) verified
   via `pg_constraint` — ✅ all present
6. Downgrade → upgrade round trip — ❌ failed first attempt (bug #1
   above), ✅ passed after the fix, re-verified against a genuinely clean
   database state (dropped orphaned types by hand first, confirmed zero
   remained, then ran the full cycle again)
7. Full existing test suite (9 Sarvam tests) re-run alongside the 5 new
   DB tests — **14/14 passed**, confirming zero regression
8. 5 tenant-isolation/cascade tests added and passing — including the
   spec's specific adversarial case (colliding identifiers across two
   tenants) applied to `ConversationTurn`, `ToolCall`, and `AuditLog`
   specifically, since those are the three systems the spec named for
   the repeated cross-tenant test
9. `mypy` on all new files — clean
10. Secret scan — clean; confirmed `alembic.ini` has no committed
    `sqlalchemy.url`, confirmed the local test Postgres password used in
    this session's shell commands never appears in any project file

### What's NOT done — stated, not implied

- `app/tools/registry.py` does **not** write to `tool_calls`/`tool_results`
  yet — the schema is ready to receive that data (via the `call_id`
  correlation column) but the registry itself wasn't touched this
  session, per the explicit instruction not to touch it. Wiring that up
  is real work, not a formality.
- No application code anywhere reads or writes `Tenant`/`User`/
  `VoiceSession`/`ConversationTurn`/`AuditLog` yet — the schema exists
  and is verified correct, but nothing in `agent_entrypoint.py` persists
  a session or a turn to Postgres. That's the natural next slice, not
  done here.
- No `.env`/`DATABASE_URL` example value was added to any env-example
  file (none exists yet in this project, unlike the earlier voice-
  platform gateway which had `.env.example`) — worth adding alongside
  whatever config-loading pattern the project settles on.
- Tests require a real Postgres instance and are not run in any CI
  config (none exists yet) — a real, stated limitation.

## Session 5: wired agent_entrypoint.py to persist VoiceSession/ConversationTurn rows

Picked up the first item from session 4's ordered list. Files touched:
`app/db/session_manager.py` (new), `tests/conftest.py` (new),
`tests/test_session_manager.py` (new), `app/agent_entrypoint.py`
(modified), `tests/test_db_tenant_isolation.py` (modified — see "test
infrastructure fix" below). Nothing under `app/context/`, `app/tools/`, or
`app/voice_providers/` was touched — confirmed by checksumming those files
before and after this session's work.

### A real bug inherited from session 4, found and fixed before doing anything else

Before starting the actual task, restarting Postgres (fresh container
state) surfaced that `alembic upgrade head` silently no-opped against an
empty database — `alembic_version` claimed "already at head" while `\dt`
showed no tables. Root cause: session 4's test fixture ran
`Base.metadata.drop_all()`/`create_all()` directly against `DATABASE_URL`
— the same database Alembic manages — so the last test run's teardown had
dropped the real schema out from under Alembic's bookkeeping. This was a
real gap in session 4's own "verified" claims: the round-trip test proved
upgrade/downgrade worked in isolation, but nothing checked that running the
test suite afterward left the real database in a sane state.

**Fixed properly, not patched around**: `tests/conftest.py` now provisions
a dedicated, disposable `<db>_pytest` database via a maintenance
connection, and every test file that needs real Postgres now uses that —
never `DATABASE_URL` directly. Verified by checking `\dt` and row counts
against the real database immediately before and after a full test run:
identical both times.

A second bug surfaced while building this fix: the dedicated test database
existed (confirmed via `psql -l`) but connecting to it failed with
`InvalidPasswordError`. Cause: SQLAlchemy's `str(URL)` masks the password
as `***` by default (a safe-logging default, not a bug in SQLAlchemy) —
the connection string *looked* right when printed but wasn't connectable.
Fixed by using `URL.render_as_string(hide_password=False)` instead.
Documented in `conftest.py` itself, not just here, since it's exactly the
kind of thing that looks like it should work and doesn't.

### What was implemented

- `app/db/session_manager.py` — `VoiceSessionRecorder`: `ensure_tenant`
  (idempotent lookup-or-create by slug), `start_session`, `record_turn`,
  `end_session`. Explicitly documents the identity-bridging decision: the
  plain-string `tenant_id`/`session_id` that Moss/Qdrant/the tool registry
  already use (sourced from LiveKit room metadata/room name) are kept
  completely separate from the new Postgres UUID primary keys —
  `ensure_tenant` treats the incoming string as a `Tenant.slug` to look up
  the corresponding UUID row, rather than forcing one identity scheme onto
  the other. This was a deliberate choice to honor "don't touch Moss/
  Qdrant/tools unless genuinely required" — it wasn't required, so nothing
  there changed.
- `app/agent_entrypoint.py` updated: bootstraps a `Tenant` and
  `VoiceSession` row at job start, subscribes to the real
  `conversation_item_added` event (verified via `inspect` against the
  installed SDK, not guessed) to persist each `ChatMessage` as a
  `ConversationTurn`, and registers a real `ctx.add_shutdown_callback` to
  mark the session `closed` when the LiveKit job ends.
- 2 new tests (`test_session_manager.py`) plus the 5 existing
  tenant-isolation tests, all now sharing the fixed `conftest.py` fixture.

### Verification breakdown

- **SDK-verified**: `ConversationItemAddedEvent`, `ChatMessage` (`.role`,
  `.text_content`), `JobContext.add_shutdown_callback` — all inspected via
  `inspect.signature`/`getsource` against the real installed
  `livekit-agents` package before being used.
- **Adapter/integration-tested against real Postgres**: `VoiceSessionRecorder`
  — idempotent tenant creation (calling twice doesn't duplicate), and a
  full lifecycle test (bootstrap → start session → record 2 turns → end
  session) verified afterward through a **fresh** session, same discipline
  as session 4's cascade tests, for the same reason (server-side defaults
  aren't visible on stale in-memory objects without a refresh).
- **NOT tested**: the actual `entrypoint()` function has still never run
  against a live LiveKit room — same hard blocker as every session before
  this one (no `LIVEKIT_URL`/`LIVEKIT_API_KEY`/`LIVEKIT_API_SECRET`, no
  network path to LiveKit Cloud from this sandbox). The `conversation_item_added`
  event handler, the shutdown callback, and the `ensure_tenant`/
  `start_session` calls at the top of `entrypoint()` are correct against
  the real SDK and tested in isolation via `VoiceSessionRecorder`, but the
  actual wiring inside `entrypoint()` itself has only been import-checked,
  not executed.

### Explicitly NOT done this session — real gaps, not implied-covered

- **Per-turn language is not captured.** `ConversationTurn.language` is
  always written as `None` from this entrypoint. Sarvam's STT reports a
  language per utterance (see `voice_providers/sarvam/stt.py`), but
  nothing currently correlates that back to the specific `ChatMessage`
  the `conversation_item_added` event carries — they're two different
  event streams with no shared correlation ID wired up yet. Flagged in
  the code itself, not just here.
- **`app/tools/registry.py` still does not write to `tool_calls`/
  `tool_results`.** The schema and the `call_id` correlation column exist
  (since session 4); the registry itself wasn't touched this session
  either, per the explicit instruction not to touch it without genuine
  need. This is next on the list, not done.
- **Retrieval metadata is not populated from real orchestrator output**
  in the entrypoint's turn-recording path — `ConversationTurn.retrieval_metadata`
  is only exercised in the `test_session_manager.py` test with a
  hand-constructed dict, not wired to `ContextBundle.sources_queried`/
  `degraded` from a live `retrieve_context` call.

### Checks run

- Full test suite: **16/16 passed** (9 Sarvam + 5 tenant-isolation + 2
  session-manager)
- Real database confirmed unaffected by the test run both before this
  session's fix and (critically) after it — `\dt` and row counts checked
  immediately before/after, identical both times
- `mypy` on `app/agent_entrypoint.py` and `app/db/session_manager.py` —
  clean
- Secret scan (same patterns as every prior session) — clean; specifically
  checked that the local test Postgres password never appears in any
  project file

## Session 6: wired tools/registry.py::execute() to ToolCall/ToolResult persistence

Picked up the first item from session 5's ordered list. Files touched:
`app/db/models.py` (added `ToolCallStatus.rejected`),
`alembic/versions/a3017d29dc4f_*.py` (new migration for that enum value),
`app/db/session_manager.py` (added `ToolCallRecorder`), `app/tools/registry.py`
(redaction + optional persistence lifecycle), `app/agent_entrypoint.py`
(wired the recorder through). `tests/test_tool_persistence.py` is new.
Nothing under `app/context/`, `app/voice_providers/`, or the LiveKit
session/VAD/STT/TTS wiring in `agent_entrypoint.py` was touched — confirmed
by checksumming `moss_provider.py`, `qdrant_provider.py`, `orchestrator.py`,
and both Sarvam plugin files before and after this session.

### Schema change: `ToolCallStatus.rejected`

The task explicitly asked to "determine deliberately whether validation
failures should create a ToolCall audit record" and to distinguish
"requested but rejected before execution" from "executed and failed,"
permitting a minimal schema change if the existing model couldn't
represent it. It couldn't (only `pending/succeeded/failed/timed_out`
existed) — added `rejected`.

**A real Alembic limitation hit immediately**: `alembic revision
--autogenerate` produced an empty migration — a documented gap where
autogenerate does not detect additions to a Postgres native `ENUM` type's
value set. Hand-wrote `op.execute("ALTER TYPE tool_call_status ADD VALUE
IF NOT EXISTS 'rejected'")`. Downgrade is **not implemented as a revert** —
Postgres has no `ALTER TYPE ... DROP VALUE`, so `downgrade()` raises
`NotImplementedError` with an explicit explanation rather than silently
no-opping and leaving someone to discover later that a "successful"
downgrade didn't actually revert anything. Verified by actually running
`alembic upgrade head` (confirmed via `SELECT unnest(enum_range(NULL::
tool_call_status))` — all 5 values present, including `rejected`) and
`alembic downgrade -1` (confirmed it raises, then re-ran `upgrade head`
to restore state) against the real dev database.

### Domain modeling decision: rejected calls get NO ToolResult row

A `ToolResult` row is only ever created after a handler has actually run
(successfully, with an error, or via timeout). For unknown-tool-name and
schema-validation rejections, only a `ToolCall(status=rejected)` row is
written — no `ToolResult`. The *absence* of a result row is itself the
structural proof the handler never touched anything, rather than a
`ToolResult(success=false)` row that would blur "never ran" into the same
shape as "ran and failed." Verified directly:
`test_invalid_arguments_are_rejected_with_no_toolresult_row` and
`test_unknown_tool_name_is_rejected_and_persisted` both assert
`scalar_one_or_none() is None` for the ToolResult side.

### call_id correlation — used as-is, not replaced

`ToolCall.call_id` (already in the schema since session 4) is exactly the
`call_id` `execute()` already generated via `uuid.uuid4()` before this
session — no second identifier introduced. `ToolResult.tool_call_id` is
the existing FK to `ToolCall.id`. Verified directly in
`test_successful_call_persists_toolcall_and_toolresult_with_correct_correlation`:
looks up by `call_id`, then confirms `ToolResult.tool_call_id ==
ToolCall.id`.

### Architecture: registry.py stays decoupled from the DB layer

`tools/registry.py` does not import SQLAlchemy or `app.db.models`.
`execute()` takes an optional `db_recorder: ToolCallRecorderProtocol`
(a `typing.Protocol` defined in `registry.py` itself) plus optional
tenant/session/user UUIDs. `app.db.session_manager.ToolCallRecorder`
satisfies that Protocol structurally — no inheritance, no import from
`tools/` into `db/`. Every pre-existing call to `execute()` (all 16 tests
from sessions 1-5) omits these params entirely and gets byte-for-byte the
original in-memory-only behavior — verified explicitly by
`test_no_persistence_params_falls_back_to_original_in_memory_only_behavior`
and by re-running the full pre-existing suite unchanged.

### Redaction — applied before both logging and persisting

`_redact()` recursively masks any dict key matching a sensitive-word list
(`password`, `secret`, `api_key`, `token`, `credential`, etc.), applied to
`arguments` before they reach `logger.info()` *and* before they reach
Postgres — this closes a real, pre-existing gap: before this session, the
in-memory `_audit()` path logged raw `args_summary` unredacted, meaning
plain-text secrets could already have been leaking into application logs
even before any Postgres persistence existed. Verified with a deliberately
fake credential (`"hunter2-super-secret"`, never a real secret) in
`test_sensitive_arguments_are_redacted_before_persisting`, asserting the
raw value is absent from the persisted JSONB column.

### Transactional guarantees — documented, not assumed

Stated explicitly in `execute()`'s own docstring, not just here:
- ToolCall(pending) is written and committed **before** the handler runs.
- The handler's real output/exception is always what's computed and
  returned — a persistence failure for the *result* never changes what
  the agent receives. Verified with a recorder subclass whose
  `record_result` always raises
  (`test_toolresult_persistence_failure_does_not_corrupt_returned_result`):
  the handler still succeeded, and `execute()` still returned
  `success=True` with the correct output, despite the write failing.
- This is explicitly **not atomic**: if the post-handler write fails, the
  `ToolCall` row is left at `pending` with no `completed_at` — ambiguous
  between "still running" and "result-persistence failed after real
  completion." No reconciliation/outbox mechanism was built to close this;
  it's real additional work, documented as a limitation, not solved.
- Cancellation (`asyncio.CancelledError`, distinct from timeout) is never
  swallowed — a best-effort failure record is persisted, then the
  cancellation is always re-raised.
- No idempotency/deduplication exists. Verified explicitly, not just
  assumed absent:
  `test_repeated_calls_are_not_deduplicated` confirms two identical calls
  produce two distinct `ToolCall` rows. A real handler would genuinely run
  twice — not solved here, since inventing a dedup scheme without a real
  specification would itself be the "unsafe retry behavior" the task said
  not to add.

### Tenant isolation

Tenant identity was already sourced only from trusted caller-supplied
parameters (never from `raw_args`) before this session; that didn't
change. Verified end-to-end through `registry.execute()` specifically
(session 4's tenant-isolation tests proved this at the raw-model level;
this session's `test_tool_calls_never_leak_across_tenants_via_persistence`
proves it through the actual execution path a real tool call takes).

### Tests added — 10, mapped to the task's 19 categories

Several of the task's 19 listed categories are naturally covered by the
same test (e.g. "correct success status" and "structured result
persistence" are both asserted inside
`test_successful_call_persists_toolcall_and_toolresult_with_correct_correlation`)
rather than needing 19 separate test functions:

1. `test_successful_call_persists_toolcall_and_toolresult_with_correct_correlation` — successful execution, ToolCall creation, ToolResult creation, call_id correlation, correct status, structured result persistence
2. `test_failed_handler_never_persists_fabricated_success` — failed execution, failed statuses, no fabricated success
3. `test_timeout_persists_timed_out_status` — timeout handling
4. `test_invalid_arguments_are_rejected_with_no_toolresult_row` — invalid arguments, validation-vs-execution distinction
5. `test_unknown_tool_name_is_rejected_and_persisted` — invalid tool name
6. `test_sensitive_arguments_are_redacted_before_persisting` — malicious/sensitive arguments, redaction
7. `test_tool_calls_never_leak_across_tenants_via_persistence` — tenant isolation
8. `test_repeated_calls_are_not_deduplicated` — repeated/correlated calls, documented non-guarantee
9. `test_toolresult_persistence_failure_does_not_corrupt_returned_result` — database persistence failure
10. `test_no_persistence_params_falls_back_to_original_in_memory_only_behavior` — backward compatibility / async handler path exercised throughout

Not separately tested: "user/session association" beyond what's implicit
in every test above (no real `User`/auth exists yet — same stated gap as
sessions 4-5, `user_id` is always `None` from `agent_entrypoint.py`).

### Verification performed

- Full test suite: **26/26 passed** (16 pre-existing + 10 new), zero
  regressions
- `mypy` on `models.py`, `session_manager.py`, `registry.py`,
  `agent_entrypoint.py` — **clean** ("Success: no issues found in 4 source
  files")
- Secret scan — clean; one intentional match reviewed and confirmed safe:
  `"hunter2-super-secret"` in `test_tool_persistence.py` is a deliberately
  fake credential used to prove redaction works, not a real secret
- **Independent verification via raw `asyncpg`**, bypassing the SQLAlchemy
  ORM entirely, for the single most safety-critical property: forced a
  handler failure, then queried `tool_calls.status` and `tool_results.success`
  directly — confirmed `status='failed'` and `success=false` in the actual
  database bytes, not just through the ORM layer that wrote them
- Real database (the one `DATABASE_URL` points at) confirmed unaffected by
  the full test run — `\dt` and `SELECT count(*) FROM tool_calls` checked
  immediately after, table list unchanged, count still 0
- Migration applied and enum value verified via
  `SELECT unnest(enum_range(NULL::tool_call_status))` against real Postgres
- Downgrade-refuses-safely behavior verified by actually running
  `alembic downgrade -1` and confirming it raises with a clear message,
  then re-running `upgrade head` to restore state

### Remaining limitations — stated, not implied-solved

- No reconciliation mechanism for the "ToolCall stuck at pending because
  the result write failed after a real completion" case described above.
- No idempotency/deduplication for repeated tool calls.
- `user_id` is always `None` — no real auth/user identity exists in this
  project yet (same gap noted in sessions 4 and 5).
- Live tool execution against a real operational system was never
  claimed and still isn't — every registered handler in
  `tools/definitions.py` remains an honest stub (unchanged this session);
  what's now real is that *attempting* to call one produces an accurate,
  auditable database record of the attempt and its actual (stub) outcome.
- `entrypoint()`'s wiring of `ToolCallRecorder` through to
  `FieldOpsAssistant` is import-checked and mypy-clean but — like every
  other piece of `entrypoint()` — has never run against a live LiveKit
  room. Same hard blocker as every session before this one.

## Session 7: authentication + trusted user identity propagation

Files touched — NEW: `app/security/jwt_auth.py`, `app/security/identity.py`,
`app/security/livekit_identity.py`, `app/security/token_service.py`,
`tests/test_auth_identity.py`. MODIFIED: `app/agent_entrypoint.py`,
`app/db/session_manager.py`, `requirements.txt`. Verified untouched by
checksum: `moss_provider.py`, `qdrant_provider.py`, `orchestrator.py`,
both Sarvam plugins, `tools/registry.py`, `db/models.py`.

### Starting-state finding, checked not assumed

`app/security/` was a 0-byte empty placeholder — there was **no existing
authentication implementation anywhere in this project** to inspect,
preserve, or decide against. The task said "do not assume the old
gateway's auth architecture is automatically correct"; there is no old
gateway in this codebase (that was a separate, earlier project in this
conversation with no `user_id` claim at all). This is a clean build,
informed by that earlier pattern, not a port of it.

### No migration required — verified, not assumed

`User`, `Tenant`, `UserStatus`, `TenantStatus`, `VoiceSession.user_id`,
`ToolCall.user_id` all already existed from session 4's schema. Confirmed
zero schema drift by running `alembic revision --autogenerate` and
checking the generated migration was empty (`pass` in both
`upgrade`/`downgrade`), then deleting the throwaway file. Alembic remains
at `a3017d29dc4f (head)`. **Status: no migration added this session.**

### Architecture

Two identity paths, both converging on the same `AuthenticatedIdentity`
dataclass so `agent_entrypoint.py` doesn't care which one produced it:

1. **App JWT path** (`jwt_auth.py` -> `identity.py::resolve_identity`):
   HS256, claims `sub` (external_id) / `tenant_slug` / `iat` / `exp` /
   `iss`. A valid signature is necessary but **not sufficient** —
   `resolve_identity()` re-validates against Postgres.
2. **LiveKit participant path** (`livekit_identity.py` ->
   `identity.py::resolve_identity_by_ids`): `mint_livekit_token()` puts
   `user_id` in LiveKit's participant `identity` field and
   `tenant_id`/`tenant_slug`/`external_id` in `attributes`. When the
   participant appears in `ctx.room`, LiveKit's own server has **already
   validated** that token's signature — this app doesn't re-verify it,
   because a participant could not be in the room otherwise.

**Deliberate decision — re-validating "still active NOW":** both paths
re-check user/tenant existence and active status against live Postgres,
not just trusting token claims. A token minted an hour ago is proof of
who someone was at mint time, not proof the account hasn't since been
suspended. Tested explicitly
(`test_livekit_participant_for_deactivated_user_is_rejected`).

**Every rejection reason is its own exception type** (`TenantNotFoundError`,
`TenantInactiveError`, `UserNotFoundError`, `UserInactiveError`,
`UserTenantMismatchError`, `ParticipantIdentityError`) rather than one
generic 403 — collapsing them would satisfy the letter of "reject invalid
identities" while destroying the distinctions a security review actually
needs in logs and tests.

### LiveKit identity mapping — SDK-verified against the real installed SDK

Every API used was checked via `inspect.signature` before use:
`AccessToken(api_key, api_secret).with_identity(str).with_attributes(dict)
.with_grants(VideoGrants(...)).with_ttl(timedelta).to_jwt()`,
`rtc.Participant.identity/.attributes`, `JobContext.wait_for_participant()`,
`JobContext.shutdown(reason)`.

**Strongest verification achieved this session**: a token minted by
`mint_livekit_token()` was independently validated by LiveKit's *own*
`TokenVerifier.verify()` — real signature check, confirming `identity`
and `attributes` round-trip exactly as intended. This is meaningfully
stronger than "the code imports and looks right."

### agent_entrypoint.py changes

Replaced `tenant_id = ctx.room.metadata or "unknown-tenant"` — an
unauthenticated, client-influenceable string — with
`await ctx.wait_for_participant()` followed by
`resolve_identity_from_participant()`. On any `IdentityResolutionError`:
logs a warning, writes an `auth_rejected` `AuditLog` row where possible,
closes the DB session, and calls `ctx.shutdown(reason=...)`.

**Deliberate ordering change worth flagging**: the agent now does *no*
Moss/Qdrant/LLM/tool setup until a participant has joined and been
authenticated. That's the correct order — there is no tenant context to
configure any of it with beforehand — but it is a real behavioral change
from the previous code, which set everything up first.

**Realtime cost: none per frame.** Identity resolution happens exactly
once, at the session boundary, and the resulting `AuthenticatedIdentity`
is reused for the session's lifetime. No per-frame or per-token DB query
was introduced.

### Identity propagation results

- `VoiceSession.user_id` — now populated (`start_session(user_id=...)`)
- `ToolCall.user_id` — now populated; `registry.py` needed **no changes**,
  since `user_uuid` was already a parameter from session 6
- `ToolCall.tenant_id` — still from trusted context, unchanged
- `AuditLog` — new `AuditRecorder`, writing `auth_rejected` events

**Deliberate scope decision on audit logging**: `AuditRecorder` is wired
to authentication-boundary rejections only, NOT to every tool call.
`ToolCall`/`ToolResult` already capture a richer, more structured record
of every tool execution (now including `user_id`) than a parallel
`AuditLog` row would — writing both would be duplication, not defense in
depth.

### Known limitation introduced by the schema, stated plainly

`AuditLog.tenant_id` is `NOT NULL` (session 4's schema). A
`TenantNotFoundError` or `ParticipantIdentityError` rejection therefore
**cannot** be durably audited — there's no valid tenant row to attach it
to. Those cases are logged to the application logger only. Not worked
around with a sentinel tenant row, which would be a larger, undiscussed
schema change than this milestone's scope allows.

### Development token endpoint

`POST /v1/dev/token` (`token_service.py`) — a minimal FastAPI app, the
first HTTP surface in this project. Returns both an app JWT and a real
LiveKit token. Returns **404** (not 403 — doesn't confirm the endpoint
exists) whenever `ENVIRONMENT != "development"`. Tested both ways.

**Production token issuance is NOT implemented.** A real deployment needs
this endpoint behind a real login/identity provider that authenticates
the caller before minting anything. That flow does not exist and was not
invented.

### Status labels

| Component | Status |
|---|---|
| App JWT create/decode/validate | **IMPLEMENTED, TESTED** |
| Identity resolution (user+tenant, all rejection paths) | **IMPLEMENTED, TESTED** |
| LiveKit token minting | **IMPLEMENTED, TESTED** (verified via LiveKit's own `TokenVerifier`) |
| LiveKit participant -> app identity mapping | **IMPLEMENTED, TESTED** (against a real-shaped participant fake) |
| `VoiceSession.user_id` propagation | **IMPLEMENTED, TESTED** |
| `ToolCall.user_id` propagation | **IMPLEMENTED, TESTED** (verified via raw asyncpg) |
| Tool args cannot override identity | **IMPLEMENTED, TESTED** (verified via raw asyncpg) |
| Audit identity for auth rejections | **IMPLEMENTED, TESTED** |
| Dev-token gating outside development | **IMPLEMENTED, TESTED** |
| Production token issuance / login flow | **NOT IMPLEMENTED** |
| Live LiveKit room authentication | **BLOCKED** — no credentials, no network path |

### Verification performed

- New auth/security tests: **20/20 passed** (first run, no assertions loosened)
- Full suite: **46/46 passed** (26 pre-existing + 20 new), zero regressions
- `mypy` on all 8 touched files: **clean**
- Secret scan: **clean** (only deliberately fake literals: `fake-key`,
  `fake-lk-*`, `test-secret-at-least-*`, `hunter2-super-secret`)
- **Raw asyncpg verification** (bypassing the ORM entirely) that
  attacker-supplied `tenant_id`/`user_id` in tool arguments do NOT reach
  the persisted `ToolCall` row
- Real `DATABASE_URL` database confirmed untouched: 8 tables intact, all
  row counts still 0
- Alembic: still at `a3017d29dc4f (head)`, zero drift

### Remaining limitations

- **Live LiveKit auth is BLOCKED, not verified.** No token minted here has
  ever joined a real room. `LOCAL AUTH TESTED` ≠ `LIVE LIVEKIT AUTH VERIFIED`.
- Production login/token issuance not implemented (dev endpoint only).
- `TenantNotFoundError`/`ParticipantIdentityError` rejections can't be
  audited to Postgres (NOT NULL constraint, above).
- No authorization beyond tenant/user scoping — no roles/permissions. The
  task said not to add RBAC without a demonstrated need; none was shown.
- Carried over, unchanged: per-turn language still `None`; tool handlers
  still honest stubs; no reconciliation for post-completion result-write
  failure; no idempotency.

## Session 8: session boundary + audit architecture (ADR 001) + production token boundary

Files — NEW: `app/security/session_boundary.py`, `tests/test_session_boundary.py`,
`docs/adr/001-audit-tenant-id.md`, `alembic/versions/ee9499881f43_add_security_events_table.py`.
MODIFIED: `app/security/identity.py`, `app/security/token_service.py`,
`app/db/models.py`, `app/db/session_manager.py`, `app/agent_entrypoint.py`,
`tests/test_auth_identity.py`. Verified untouched by checksum (unchanged
from sessions 6-7): `orchestrator.py`, `tools/definitions.py`,
`tools/registry.py`, both Sarvam plugins, `moss_provider.py`,
`qdrant_provider.py`.

### 1-2. LiveKit session boundary / `wait_for_participant()` decision

**Decision: KEEP `wait_for_participant()`, but wrap it.** Reached by
reading its actual implementation in the installed SDK
(`livekit/agents/utils/participant.py`), not by assuming. What it really
does:

- DOES return immediately for an already-joined participant (reused
  rooms and agent restarts don't hang)
- DOES raise `RuntimeError("room disconnected while waiting for
  participant")` via a real `connection_state_changed` listener
- DOES filter by kind, defaulting to `[5, 3, 0]`, excluding
  `PARTICIPANT_KIND_AGENT` (4) — the agent can't authenticate itself
- **Has NO timeout.** `await fut` waits forever if nobody joins.

**That last point was a real defect in session 7's code**, which called
it bare: a job for a room nobody joins would hold an open Postgres
session and a worker slot indefinitely, silently.
`establish_authenticated_session()` wraps it in `asyncio.wait_for`
(60s default). Fixed and tested.

**Multiple participants — deliberate, enforced, not assumed.** The first
participant is authenticated and the session is bound to them.
`enforce_single_participant()` registers a real `participant_connected`
listener (verified event name) and **fails closed** — shuts the session
down — if a second non-agent participant appears. A per-speaker identity
model would require diarization and per-speaker tool authorization, well
beyond this milestone; making the single-user assumption explicit and
enforced is the honest alternative to leaving it implicit.

**Invariant enforced structurally, not by ordering discipline:** on any
boundary failure `establish_authenticated_session()` raises rather than
returning, so no `AuthenticatedSession` object exists, and
`agent_entrypoint.py` cannot construct `FieldOpsAssistant` (which owns
the tool registry) without one. The tool layer is *unreachable*, not
merely un-called.

**No per-frame DB work**: exactly one identity resolution, at the
boundary; the frozen `AuthenticatedIdentity` is reused for the session.

### 3. Audit `tenant_id` decision — ADR 001 (new)

**Decision: Option 2** — a separate `security_events` table. Full
reasoning in `docs/adr/001-audit-tenant-id.md`.

**Option 3 (nullable `tenant_id`) was explicitly rejected**, and that
rejection is the substance of the decision. It looks smallest but breaks
the invariant's intent: it would force every existing tenant-scoped
query and the `ix_audit_logs_tenant_id_created_at` index to handle NULL;
conflate "tenant X's user acted" with "an unauthenticated party failed to
get in" (different retention, access control, and attacker-controlled
volume); and entangle pre-auth events with `ON DELETE CASCADE` tenant
offboarding.

`security_events` has **no `tenant_id` column and no FK to `tenants`** —
verified against live Postgres: `0` foreign keys on the table. The
invariant "no audit record may claim an unverified tenant" is enforced by
schema shape. `claimed_tenant_slug`/`claimed_tenant_id` are nullable
**strings** recording what an unauthenticated party asserted (forensically
useful) that nothing joins on.

**`AuditLog` is completely unchanged** — still NOT NULL, still FK'd.

**Routing** is encoded once, in `is_tenant_verified()`, as an *allow-list*
so any future exception type defaults to the safe side
(`security_events`). To make this work correctly, tenant-verified
rejections now carry `verified_tenant_id` — the primary key **read from
Postgres**, never an id echoed from client input.

### 4. Production token boundary

Added `POST /v1/livekit/token` — available in **all** environments.
It does not authenticate anyone; it **exchanges** an already-valid
application JWT for a LiveKit token, re-validating the bearer against
Postgres first. Expiry enforced; a deactivated user cannot exchange a
still-valid token (tested). Rejections return one generic 401/403
outward while recording the specific reason internally.

**The explicit external boundary**: whatever issues the application JWT
(password login, OIDC/SAML, enterprise IdP) is **NOT IMPLEMENTED** and
deliberately not invented. The integration contract it must satisfy is
documented in the endpoint's docstring. `/v1/dev/token` remains 404
outside `ENVIRONMENT=development`.

### 5. Canonical identity model

One object, both paths, `source` now explicit:

```
app JWT ──> decode_access_token() ──> resolve_identity() ─────┐
            (signature verified)      (Postgres validated)    ├──> AuthenticatedIdentity
LiveKit ──> participant.identity  ──> resolve_identity_by_ids()┘     (frozen)
token       + .attributes             (Postgres validated)
```

`IdentitySource.APP_JWT` / `LIVEKIT_PARTICIPANT`. Frozen;
`with_session()` returns a new instance rather than mutating.

### 6-8. Status labels, tests, counts

| Item | Status |
|---|---|
| Session-boundary lifecycle (timeout, disconnect, malformed, cross-tenant, no-participant) | **SDK VERIFIED + LOCAL TESTED** |
| `security_events` schema & no-tenant-FK invariant | **DATABASE VERIFIED** |
| Audit routing rule | **LOCAL TESTED** |
| Production token exchange | **LOCAL TESTED** |
| Production identity provider / login | **NOT IMPLEMENTED** (explicit external boundary) |
| Live LiveKit room authentication | **BLOCKED** |

- New tests: **19** (15 session-boundary/audit + 4 production exchange)
- **Total: 65/65 passing** (46 prior + 19 new), zero regressions
- mypy on this session's 10 files: **clean**. Repo-wide: 21 pre-existing
  errors in `orchestrator.py`/`definitions.py`, untouched (checksums
  match sessions 6-7), flagged since session 2
- Secret scan: **clean**
- Real `DATABASE_URL` DB: untouched, all counts 0
- Alembic: `ee9499881f43 (head)`; migration up/down round-trip verified

### Remaining limitations

- **Live LiveKit verification: BLOCKED.** No token minted here has joined
  a real room. `LOCAL TESTED` ≠ `LIVE VERIFIED`.
- Production identity provider not implemented (boundary documented).
- `security_events` has **no retention policy** — attacker-controlled
  volume; needs one before production.
- Multi-participant rooms fail closed rather than being supported.
- Carried over: per-turn language still `None`; tool handlers still
  stubs; no reconciliation for post-completion result-write failure; no
  idempotency; no RBAC.

## Session 9: tool contracts, idempotency, and the first real action

Files — NEW: `app/tools/contracts.py`, `app/tools/idempotency.py`,
`app/tools/ticket_service.py`, `app/tools/create_ticket.py`,
`app/tools/executor.py`, `tests/test_create_ticket.py`, migration
`be1b4ec8805a`. MODIFIED: `app/db/models.py` (+`idempotency_key`),
`app/db/session_manager.py` (optional kwarg).

**Untouched, checksums verified identical to sessions 6-8**:
`tools/registry.py`, `tools/definitions.py`, `agent_entrypoint.py`,
`security/session_boundary.py`, Moss, Qdrant, both Sarvam plugins.

### Phase A — tool contract audit

| TOOL | INPUT | OUTPUT | SIDE EFFECT | AUTH | TENANT SCOPED | IDEMPOTENCY | STATUS |
|---|---|---|---|---|---|---|---|
| create_ticket | site_id, description, priority, equipment_id | success, ticket_id, status, message, deduplicated | EXTERNAL_SIDE_EFFECT | AUTHENTICATED_WRITE | yes (context) | KEYED | **IMPLEMENTED + TESTED** (TEST-ADAPTER behind it) |
| update_ticket | ticket_id, status, notes | — | WRITE | AUTHENTICATED_WRITE | yes | unclassified | **MOCKED** (stub) |
| get_equipment_status | equipment_id | — | READ_ONLY | AUTHENTICATED | yes | NOT_REQUIRED | **MOCKED** (stub) |
| get_customer_details | customer_id | — | READ_ONLY | AUTHENTICATED | yes | NOT_REQUIRED | **MOCKED** (stub) |
| get_worker_location | worker_id | — | READ_ONLY | AUTHENTICATED | yes | NOT_REQUIRED | **MOCKED** (stub) |
| find_nearest_technician | site_id, skill_required | — | READ_ONLY | AUTHENTICATED | yes | NOT_REQUIRED | **MOCKED** (stub) |
| dispatch_worker | worker_id, site_id, ticket_id | — | EXTERNAL_SIDE_EFFECT / high impact | AUTHENTICATED_WRITE | yes | UNSAFE_TO_REPEAT | **MOCKED** (stub) |
| send_notification | recipient_id, message, channel | — | EXTERNAL_SIDE_EFFECT | AUTHENTICATED_WRITE | yes | UNSAFE_TO_REPEAT | **MOCKED** (stub) |
| update_CRM | customer_id, fields | — | EXTERNAL_SIDE_EFFECT | AUTHENTICATED_WRITE | yes | UNSAFE_TO_REPEAT | **MOCKED** (stub) |
| escalate_to_human | session_id, reason | — | EXTERNAL_SIDE_EFFECT | AUTHENTICATED_WRITE | yes | UNSAFE_TO_REPEAT | **MOCKED** (stub) |

Only `create_ticket` was converted this milestone, per scope control. The
other nine remain honest stubs in `definitions.py`, untouched.

### Phases B/C — contract architecture

`ToolContract` (frozen dataclass) declares name, description, input/output
schema, `SideEffect`, `AuthorizationLevel`, `IdempotencyMode`, timeout, and
`idempotency_fields`. Self-validating at import: a KEYED contract naming no
fields, or a READ_ONLY contract demanding idempotency, raises immediately.

Not a policy engine — the task said only if necessary, and three enums plus
a dataclass cover every decision the current tool set needs.

### Phase D — idempotency

**`call_id` alone is insufficient — established by reading the code, not
assumed.** `registry.py` does `call_id = str(uuid.uuid4())` per invocation,
so two identical logical requests get different call_ids. It correlates one
execution to its result; it cannot detect a repeat.

New `tool_calls.idempotency_key` + `uq_tool_calls_idempotency_key`
(migration `be1b4ec8805a`). Key = SHA-256 of
`(tenant_id, session_id, tool_name, declared arg fields)`. tenant/session
come from **trusted context**, so a model cannot influence key scope.
`description` is excluded on purpose: an agent rewording free text on retry
must not defeat suppression (tested).

**A real design gap found by a failing test, not by inspection**: a *failed*
call's row still occupied the unique slot, so `UniqueViolationError` blocked
a legitimate retry — one transient outage would have permanently prevented
that ticket. Fixed by **releasing the key on any non-`succeeded` terminal
state**: it reserves the slot in-flight (making concurrent duplicates
race-safe) and frees it if execution didn't succeed.

### Phase E — timeouts

Every contract declares `timeout_seconds`; the executor enforces it via
`asyncio.wait_for`. Timeout persists `ToolCallStatus.timed_out` and
`success=False` — **never** success. The log explicitly notes that for an
external-side-effect tool the upstream action may still have succeeded.

### Phase F — retry policy

`FailureClass` (validation, authorization, timeout, transient,
provider_error, business_rule, unknown) + `is_retry_safe()` requiring **two**
conditions: the failure class must be retryable AND the tool must be safe to
repeat. This matters most for TIMEOUT on an external write — the most
dangerous retry in the system, since the request may have succeeded with
only the response lost. Retryable only for READ_ONLY or KEYED tools.
UNKNOWN fails closed.

### Phase G — first real tool

**No external ticketing provider exists.** Established by searching all
three repos for `create_ticket|ticketing|servicenow|zendesk|jira|freshdesk`
— two hits, both irrelevant (a JS test fixture in moss-main; a prose
mention of Zendesk in primd-main's market-research doc, a repo we
deliberately excluded). Neither is an implementation.

So: `TicketService` (abstract production adapter interface) +
`InMemoryTicketService` (**TEST-ADAPTER**, clearly labelled, deterministic,
no network). A real provider is one new file and one wiring change.

`ContractToolExecutor` is a **separate path, not a rewrite of
`registry.py`** — deliberately, because registry.py's call_id correlation,
rejected/failed/timed_out states, tenant isolation and user_id propagation
are already covered by 26 passing tests, and rewriting it would risk all of
that for no gain. The executor reuses the same `ToolCallRecorder` lifecycle
and status semantics.

### Status labels

| Item | Status |
|---|---|
| Tool contract layer | **IMPLEMENTED + TESTED** |
| Idempotency (key, constraint, suppression, release-on-failure) | **IMPLEMENTED + TESTED + DATABASE VERIFIED** |
| Timeout behavior | **IMPLEMENTED + TESTED** |
| Retry policy | **IMPLEMENTED + TESTED** (policy only; no auto-retry loop is wired) |
| `create_ticket` tool end-to-end | **IMPLEMENTED + TESTED** |
| Ticketing backend | **TEST-ADAPTER** — production adapter interface only |
| External ticket creation | **BLOCKED** — no provider, no credentials |
| Other nine tools | **MOCKED** (unchanged stubs) |

### Verification

- New tests: **21**. **Total 86/86 passing** (65 prior + 21), zero regressions
- mypy on this session's 8 files: **clean**
- Secret scan: **clean**
- **Independent raw-asyncpg verification**: duplicate request produced
  provider call count **1**, exactly **1** `tool_calls` row, one
  `tool_results` row with the real ticket id — confirmed in database bytes,
  not through the ORM that wrote them
- Real `DATABASE_URL` DB untouched (0 rows); Alembic at `be1b4ec8805a (head)`
- Migration round-trip verified — which surfaced a second real defect:
  autogenerate emitted an unnamed unique constraint whose downgrade failed
  with `CompileError`. Alembic itself warned it "will fail as rendered"; it
  did, and is now explicitly named.

### Remaining limitations

- **No live external ticket has ever been created.** TEST-ADAPTER only.
- Retry policy is **declarative only** — no automatic retry loop is wired.
  Deliberate: wiring auto-retry before a real provider exists would be
  untestable against real failure modes.
- The reconciliation gap is unchanged and now matters more: if the ticket
  is created but `ToolResult` persistence fails, the `ToolCall` may remain
  `pending` — meaning "the ticket may exist but we failed to record it."
  No outbox or reconciliation sweep is built.
- `dispatch_worker`, `send_notification`, `update_CRM`, `escalate_to_human`
  are classified `UNSAFE_TO_REPEAT` and have no key scheme — they must not
  be auto-retried when implemented.
- Carried over: live LiveKit BLOCKED; no external IdP; `security_events` has
  no retention policy; per-turn language `None`; no RBAC.

## FINAL VERIFICATION (full-system audit)

Independent re-verification of every claim above — not a re-statement of
them. Nothing here was trusted from prior sessions without re-running it.

**Test command**: `pytest tests/ --asyncio-mode=auto -q` (run twice,
identical result both times, including after this audit's own fixes)
**Result**: 86 passed, 0 failed, 0 skipped, 0 xfailed, 2 warnings
(both pre-existing Starlette/anyio deprecation notices, not test defects)

**Coverage** (`--cov=app --cov-report=term-missing`): **67% overall**.
Real gap found, not previously stated this precisely: `agent_entrypoint.py`
(0%), `context/orchestrator.py` (0%), `context/qdrant_provider.py` (0%),
`context/moss_provider.py` (0%), `context/embeddings.py` (0%),
`tools/definitions.py` (0%, the 9 remaining stub tools). Checked directly
— no test file imports `ContextOrchestrator`, `QdrantKnowledgeProvider`,
`MossContextProvider`, or `SentenceTransformerEmbedder`. Earlier sessions'
"context orchestrator routing verified" / "tenant isolation verified"
claims for Qdrant were real but were **one-time ad-hoc verification
scripts run during development, never saved as persisted pytest tests** —
so today there is no automated regression protection for those paths.
Stated plainly rather than left implied.

**mypy** (`mypy app/ --ignore-missing-imports`): 21 errors, all in 4
files: `context/moss_provider.py` (3), `context/qdrant_provider.py` (4),
`context/orchestrator.py` (4), `tools/definitions.py` (10). Checksums of
all 4 files confirmed **byte-identical** to every prior session since
first flagged (session 2) — genuinely pre-existing, not regressions.
Correction to prior shorthand: sessions 8-9 described these as "in
orchestrator.py/definitions.py," which dropped `moss_provider.py` and
`qdrant_provider.py` from the explicit list even though they were part of
the same original set. Noted here for accuracy.

**Lint**: no ruff/flake8/pyproject.toml/setup.cfg exists in this project —
nothing configured, nothing to run.

**Secret scan**: whole-repo, clean. Verified `.gitignore` covers
`.env`/`.env.*`; confirmed zero `.env` files exist anywhere; no git
repository exists (never initialized) so there is no commit history to
scan separately.

**Real database safety**: row counts and table list captured before AND
after the full suite run — identical both times (8 tables, 0 rows in
every table). Confirmed the disposable `voiceagent_pytest` database is
what tests actually use (`psql -l` lists it separately). Alembic
unchanged at `be1b4ec8805a (head)` throughout.

**Architecture**: confirmed no `primd` code anywhere under `app/` or
`tests/` (the one source-tree hit is a docstring in `ticket_service.py`
explaining *why* no provider was found, correctly naming the excluded
repo, not integrating it). Confirmed no frontend exists anywhere (no
`.jsx`/`.tsx`/`package.json`) and no Docker configuration exists
(no `Dockerfile`/`docker-compose*`) — both genuinely `NOT IMPLEMENTED`,
not merely unverified.

**Dependency audit — two real defects found and fixed**, both minimal,
zero behavior change:
1. `pyjwt` is directly imported (`import jwt` in `jwt_auth.py`) but was
   **absent from `requirements.txt`** — a fresh install would fail at
   import time. Added.
2. `uvicorn` (or any ASGI server) was never declared, despite
   `token_service.py` being a real FastAPI app with no way to actually
   run it outside of `TestClient`. Added.

Also added `pytest-cov` to `requirements-dev.txt` (used for this audit's
coverage run, previously undeclared).

Full suite re-run after both fixes: still 86/86, confirming the fixes
were inert with respect to behavior.

**Spot-checks re-run fresh, not trusted from history**:
- Romanized LID: `"Mujhe Hyderabad ka weather batao"` still resolves to
  `en-IN` (1 marker hit, threshold 2) — limitation confirmed unchanged,
  not regressed, not silently fixed.
- Sarvam TTS race-condition test (`_text_sent` causality fix from session
  3): file checksum unchanged, fix still present, assertion not weakened.
- `SentenceTransformerEmbedder` confirmed wired into
  `agent_entrypoint.py`'s actual production path (not merely existing in
  isolation) — real and connected, but 0%-covered and blocked on live
  network/credentials for actual execution.
- Tool contract self-validation re-run live: a KEYED contract with no
  declared fields still raises `ValueError` at construction.
- `security_events` re-confirmed to have **zero** foreign keys via a
  fresh `information_schema` query.

## What I'd build next, in order (updated)

1. Correlate Sarvam's per-utterance language detection to
   `ConversationTurn.language` (carried since session 5)
2. Prompt-injection defense: system/user/retrieved-content separation
3. Security audit pass against the spec's full checklist
4. A real frontend (has never existed for this project)
5. Pre-existing mypy findings in session 1's files (low-urgency)

Per this task's explicit scope control, stopping here.

