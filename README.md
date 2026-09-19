# Voice Agent Platform

A real-time voice agent for field operations: a LiveKit Agents worker
(Sarvam STT/TTS, Gemini LLM, Moss + Qdrant context retrieval) paired with a
React/TypeScript console for connecting to a live voice session, watching
its state, transcript, and tool activity in real time.

This README is the current, verified entry point. `STATUS_REPORT.md` is the
detailed build log from the original backend build session — useful
history, but superseded by this file and by the test results below where
the two differ.

## Layout

```
agent/       FastAPI token service + LiveKit Agents worker (Python)
frontend/    React + TypeScript + Vite console (livekit-client)
docs/adr/    Architecture decision records
render.yaml  Render Blueprint for the backend (see agent/render.yaml)
```

## Verified state (this session)

- **Backend**: 146/146 tests passing against a real local PostgreSQL 16
  instance. Coverage 83% overall; the 5 modules that previously had 0%
  coverage (`agent_entrypoint.py`, `context/embeddings.py`,
  `context/moss_provider.py`, `context/qdrant_provider.py`,
  `tools/definitions.py`) now have dedicated tests.
- **Frontend**: `npm run lint` and `npm run build` both clean. No automated
  test suite exists yet (no vitest/jest configured).
- **End-to-end locally**: both services started together, real
  `/v1/dev/token` and `/v1/livekit/token` calls made and verified against a
  local Postgres + fake LiveKit credentials (this sandbox has no network
  path to real LiveKit Cloud, Neon, Sarvam, or Moss — see Limitations).
- **CORS**: added and tested this session (`tests/test_cors.py`, 4 tests).
  The token service previously had none, which silently blocks every
  browser request from a different origin than a same-origin CORS-blind
  curl/httpx test could ever catch.

## Local quick start

```bash
# Backend (needs Python 3.12+, a local/remote Postgres)
cd agent
python3 -m venv .venv-linux && source .venv-linux/bin/activate
pip install -r requirements.txt -r requirements-dev.txt
export DATABASE_URL=postgresql+asyncpg://user:pass@localhost:5432/vap
alembic upgrade head
export JWT_SECRET=... JWT_ISSUER=voice-agent-platform ENVIRONMENT=development \
       LIVEKIT_API_KEY=... LIVEKIT_API_SECRET=... ALLOWED_ORIGINS=http://localhost:5173
uvicorn app.security.token_service:app --reload --port 8000    # terminal 1
python -m app.agent_entrypoint dev                              # terminal 2 (needs LIVEKIT_URL, MOSS_*, SARVAM_API_KEY, GOOGLE_API_KEY too)

# Frontend
cd frontend
cp .env.example .env   # set VITE_LIVEKIT_URL
npm install && npm run dev   # http://localhost:5173
```

Run the backend test suite with `pytest tests/` (needs `DATABASE_URL`
pointed at a real Postgres — see `agent/.env.production.example` for the
full variable reference and format gotchas).

### Before trusting a live voice session: run the preflight check

This project was built and tested in a sandbox with no network path to
LiveKit Cloud, Neon, Sarvam, or Moss — every piece of code was verified
correct against these services' real SDKs, but never verified *reachable*.
From a machine with normal internet access:

```bash
cd agent
pip install livekit-api asyncpg aiohttp moss
export $(grep -v '^#' .env | xargs)   # load your real credentials
python preflight_check.py
```

It makes one lightweight, authenticated call per service (Postgres, LiveKit
Cloud, Sarvam, Moss) and checks `GOOGLE_API_KEY` is set, and tells you in
~10 seconds which of the five is actually going to work before you spend
time debugging a live session. Nothing is created or left running.

## Deploying: Vercel (frontend) + Render (backend)

- `agent/render.yaml` — Blueprint defining the token service (web) and
  agent worker (background worker) as two Render services. Every secret is
  `sync: false`; fill real values in Render's dashboard, not in the file.
- Frontend: Vercel auto-detects Vite. Set **Root Directory** to `frontend`
  (the one non-default setting for this monorepo), and set
  `VITE_API_BASE_URL` / `VITE_LIVEKIT_URL` as environment variables.
- Full walkthrough, including two real bugs found and fixed while checking
  an actual `.env` against this code (a bad `DATABASE_URL` format for
  asyncpg, and a missing `GOOGLE_API_KEY`), is in
  `docs/voice-agent-platform-local-start-and-deployment.pdf`.

## Known limitations

- **Production authentication is not implemented.** `/v1/dev/token` mints a
  token for any `tenant_slug`/`external_id` with no login check — fine
  for development, a real gap the moment a deployed URL is reachable.
  `ENVIRONMENT=development` must stay off in any environment you don't
  fully control access to.
- **`QDRANT_LOCATION=:memory:`** loses all upserted knowledge on every
  worker restart and doesn't share state across multiple worker instances.
  Swap for a persistent Qdrant (Cloud or self-hosted) for anything beyond a
  single always-on demo worker.
- **No live verification against real external services** (LiveKit Cloud,
  Neon, Sarvam, Moss) has been possible from this development sandbox — its
  network is allowlisted to package registries only, confirmed via
  `x-deny-reason: host_not_allowed` on every attempt. Everything above the
  "Verified state" line was checked locally or by reading the actual SDK
  source. Run `agent/preflight_check.py` from a machine with real network
  access to get an actual answer for each service before assuming a live
  voice session will work.
- `context/orchestrator.py` is at 31% test coverage — the concurrent
  moss/qdrant/live-api retrieval path is still largely untested.
- `context/live_api.py` defines a `LiveOperationalAPI` class nothing
  imports — dead code (an identical class lives inline in
  `orchestrator.py`, which is what's actually used). Worth deleting.
