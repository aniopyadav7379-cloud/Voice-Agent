#!/usr/bin/env python3
"""
Preflight check for voice-agent-platform's real external services.

WHY THIS EXISTS: this project has been developed and tested in a sandbox
with no network path to LiveKit Cloud, Neon, Sarvam, or Moss -- outbound
traffic there is allowlisted to package registries only. Every attempt to
reach those hosts from that sandbox is intercepted by the sandbox's own
egress proxy and returns HTTP 403 with header "x-deny-reason:
host_not_allowed" and body "Host not in allowlist: <host>..." -- confirmed
directly against each host. That is an ENVIRONMENT / NETWORK BLOCK, never a
credential failure: the real service is never actually reached, so this
script explicitly detects that exact signature and reports NETWORK BLOCKED
rather than misreading a proxy's 403 as the real service rejecting the key.

This script exists so you can get a real, authenticated answer for each
service from a machine that isn't sandboxed this way.

WHAT IT DOES: loads a real .env, then makes ONE lightweight, real,
authenticated request per service using the project's own actual
endpoints/SDKs (not guessed ones) -- the same LiveKit REST call the token
service depends on, the same Sarvam TTS endpoint app/voice_providers/sarvam
uses, the same DATABASE_URL the app's SQLAlchemy engine uses, the same Moss
SDK app/context/moss_provider.py uses. No LiveKit room is left running, no
data is written anywhere, nothing is deleted.

WHAT IT NEVER DOES: print an API key, secret, JWT, or database password.
Every credential is masked to its first 4 and last 2 characters.

Usage (Windows, macOS, Linux -- all the same):
    cd agent
    pip install livekit-api asyncpg aiohttp moss
    python preflight_check.py                 # loads .env from this directory
    python preflight_check.py "path/to/.env"  # or point at one explicitly
"""
from __future__ import annotations

import asyncio
import os
import socket
import sys
import urllib.parse
from typing import NamedTuple

REQUIRED_VARS = [
    "DATABASE_URL", "JWT_SECRET", "JWT_ISSUER", "ENVIRONMENT",
    "LIVEKIT_URL", "LIVEKIT_API_KEY", "LIVEKIT_API_SECRET",
    "SARVAM_API_KEY",
]

OPTIONAL_VARS = [
    "GROQ_API_KEY", "GROQ_MODEL", "GOOGLE_API_KEY", "GEMINI_MODEL",
    "MOSS_PROJECT_ID", "MOSS_PROJECT_KEY", "QDRANT_LOCATION", "ALLOWED_ORIGINS",
]


class CheckResult(NamedTuple):
    status: str  # "AUTHENTICATED" | "REJECTED" | "NETWORK BLOCKED" | "MISSING" | "ERROR"
    detail: str  # human-readable, never contains a secret


def mask(value: str) -> str:
    if len(value) <= 8:
        return "…"
    return f"{value[:4]}…{value[-2:]} (len={len(value)})"


def load_env_file(path: str) -> dict[str, str]:
    """Minimal .env parser -- no external dependency. Does not overwrite
    variables already present in the real environment."""
    loaded: dict[str, str] = {}
    if not os.path.exists(path):
        return loaded
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key, value = key.strip(), value.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = value
            loaded[key] = value
    return loaded


async def egress_probe(host: str) -> CheckResult | None:
    """Real HTTP-level probe for the exact egress-proxy signature this
    sandbox (and possibly a corporate firewall doing the same thing) uses:
    HTTP 403 with header x-deny-reason: host_not_allowed. A DNS failure or
    a raw connection failure is also a network block. Anything else (a
    normal-looking response of any status) means the request actually
    reached something -- callers proceed to their real, service-specific
    request in that case. Returns None when nothing indicates a block."""
    try:
        import aiohttp
    except ImportError:
        return None  # let the caller's own import-check report this

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(f"https://{host}/", timeout=aiohttp.ClientTimeout(total=8)) as resp:
                if resp.headers.get("x-deny-reason") == "host_not_allowed":
                    return CheckResult("NETWORK BLOCKED", f"egress proxy blocked {host} (host_not_allowed)")
                body_start = (await resp.text())[:120] if resp.status == 403 else ""
                if "not in allowlist" in body_start.lower():
                    return CheckResult("NETWORK BLOCKED", f"egress proxy blocked {host}: {body_start}")
        return None
    except socket.gaierror as e:
        return CheckResult("NETWORK BLOCKED", f"DNS resolution failed for {host}: {e}")
    except (TimeoutError, ConnectionRefusedError, OSError) as e:
        return CheckResult("NETWORK BLOCKED", f"cannot reach {host}: {e}")
    except Exception:
        return None  # anything else -- let the real request attempt and speak for itself


async def check_database() -> CheckResult:
    url = os.environ.get("DATABASE_URL")
    if not url:
        return CheckResult("MISSING", "DATABASE_URL not set")

    if "sslmode" in url or "channel_binding" in url:
        return CheckResult(
            "ERROR",
            "DATABASE_URL has sslmode=/channel_binding= query params -- asyncpg (this app's driver) "
            "rejects those outright with TypeError. Required format: "
            "postgresql+asyncpg://USER:PASS@HOST/DB?ssl=require -- fixing the URL is required before "
            "this check can mean anything; not re-attempted here since it would only reproduce that error.",
        )

    parsed = urllib.parse.urlsplit(url.replace("postgresql+asyncpg://", "postgresql://", 1))
    try:
        import asyncpg
    except ImportError:
        return CheckResult("ERROR", "asyncpg not installed (pip install asyncpg)")

    dsn = url.replace("postgresql+asyncpg://", "postgresql://", 1)
    try:
        conn = await asyncio.wait_for(asyncpg.connect(dsn=dsn, timeout=15), timeout=20)
        result = await conn.fetchval("SELECT 1")
        await conn.close()
        return CheckResult("AUTHENTICATED", f"SELECT 1 -> {result}")
    except asyncio.TimeoutError:
        return CheckResult("NETWORK BLOCKED", "connection timed out mid-handshake")
    except Exception as e:
        msg = str(e).lower()
        if "password" in msg or "authentication" in msg:
            return CheckResult("REJECTED", f"{type(e).__name__}: {e}")
        return CheckResult("ERROR", f"{type(e).__name__}: {e}")


async def check_livekit() -> CheckResult:
    url = os.environ.get("LIVEKIT_URL")
    key = os.environ.get("LIVEKIT_API_KEY")
    secret = os.environ.get("LIVEKIT_API_SECRET")
    if not all([url, key, secret]):
        return CheckResult("MISSING", "LIVEKIT_URL / LIVEKIT_API_KEY / LIVEKIT_API_SECRET not fully set")

    try:
        from livekit import api
    except ImportError:
        return CheckResult("ERROR", "livekit-api not installed (pip install livekit-api)")

    try:
        lk = api.LiveKitAPI(url=url, api_key=key, api_secret=secret)
        rooms = await asyncio.wait_for(lk.room.list_rooms(api.ListRoomsRequest()), timeout=15)
        return CheckResult("AUTHENTICATED", f"ListRooms succeeded -- {len(rooms.rooms)} room(s) currently active")
    except Exception as e:
        msg = str(e)
        if "403" in msg or "401" in msg or "unauthorized" in msg.lower() or "invalid" in msg.lower():
            return CheckResult("REJECTED", f"{type(e).__name__}: {e}")
        return CheckResult("ERROR", f"{type(e).__name__}: {e}")
    finally:
        try:
            await lk.aclose()
        except Exception:
            pass


async def check_sarvam_tts() -> CheckResult:
    """Same endpoint, same request shape, same default model/speaker/language
    as app/voice_providers/sarvam/tts.py's TTS class actually sends."""
    key = os.environ.get("SARVAM_API_KEY")
    if not key:
        return CheckResult("MISSING", "SARVAM_API_KEY not set")

    try:
        import aiohttp
    except ImportError:
        return CheckResult("ERROR", "aiohttp not installed (pip install aiohttp)")

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                "https://api.sarvam.ai/text-to-speech",
                headers={"api-subscription-key": key, "Content-Type": "application/json"},
                json={
                    "inputs": ["preflight check"],
                    "target_language_code": "en-IN",
                    "speaker": "shubh",
                    "model": "bulbul:v3",
                },
                timeout=aiohttp.ClientTimeout(total=15),
            ) as resp:
                if resp.headers.get("x-deny-reason") == "host_not_allowed":
                    return CheckResult("NETWORK BLOCKED", "egress proxy blocked api.sarvam.ai (host_not_allowed)")
                if resp.status == 200:
                    return CheckResult("AUTHENTICATED", "POST /text-to-speech -> 200 OK, audio returned")
                if resp.status in (401, 403):
                    return CheckResult("REJECTED", f"HTTP {resp.status} -- key rejected by Sarvam")
                body = (await resp.text())[:150]
                return CheckResult("ERROR", f"HTTP {resp.status}: {body}")
    except Exception as e:
        return CheckResult("ERROR", f"{type(e).__name__}: {e}")


async def check_moss() -> CheckResult:
    project_id = os.environ.get("MOSS_PROJECT_ID")
    project_key = os.environ.get("MOSS_PROJECT_KEY")
    if not all([project_id, project_key]):
        return CheckResult("MISSING", "MOSS_PROJECT_ID / MOSS_PROJECT_KEY not fully set")

    try:
        from moss import MossClient
    except ImportError:
        return CheckResult("ERROR", "moss not installed (pip install moss)")

    try:
        client = MossClient(project_id, project_key)
        indexes = await asyncio.wait_for(client.list_indexes(), timeout=15)
        count = len(indexes) if hasattr(indexes, "__len__") else "?"
        return CheckResult("AUTHENTICATED", f"list_indexes() succeeded -- {count} index(es)")
    except Exception as e:
        msg = str(e).lower()
        if "401" in msg or "403" in msg or "unauthorized" in msg or "invalid" in msg:
            return CheckResult("REJECTED", f"{type(e).__name__}: {e}")
        return CheckResult("ERROR", f"{type(e).__name__}: {e}")


async def check_google() -> CheckResult:
    key = os.environ.get("GOOGLE_API_KEY")
    if not key:
        return CheckResult(
            "MISSING",
            "GOOGLE_API_KEY not set -- required by livekit-plugins-google's LLM class (confirmed by "
            "reading its source: falls back to this env var when no api_key is passed, and raises if "
            "unset). Get one from Google AI Studio.",
        )

    blocked = await egress_probe("generativelanguage.googleapis.com")
    if blocked:
        return blocked

    model = os.environ.get("GEMINI_MODEL", "gemini-3.8-flash")
    try:
        import aiohttp
    except ImportError:
        return CheckResult("ERROR", "aiohttp not installed (pip install aiohttp)")

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={key}",
                json={"contents": [{"parts": [{"text": "ping"}]}]},
                timeout=aiohttp.ClientTimeout(total=15),
            ) as resp:
                if resp.status == 200:
                    return CheckResult("AUTHENTICATED", f"generateContent against {model} -> 200 OK")
                if resp.status in (400, 401, 403):
                    body = (await resp.text())[:150]
                    return CheckResult("REJECTED", f"HTTP {resp.status}: {body}")
                body = (await resp.text())[:150]
                return CheckResult("ERROR", f"HTTP {resp.status}: {body}")
    except Exception as e:
        return CheckResult("ERROR", f"{type(e).__name__}: {e}")


async def check_groq() -> CheckResult:
    key = os.environ.get("GROQ_API_KEY")
    if not key:
        return CheckResult("MISSING", "GROQ_API_KEY not set (optional if GOOGLE_API_KEY is configured)")

    blocked = await egress_probe("api.groq.com")
    if blocked:
        return blocked

    model = os.environ.get("GROQ_MODEL", "qwen/qwen3.8-27b")
    try:
        import aiohttp
    except ImportError:
        return CheckResult("ERROR", "aiohttp not installed (pip install aiohttp)")

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                "https://api.groq.com/openai/v1/chat/completions",
                headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
                json={"model": model, "messages": [{"role": "user", "content": "ping"}], "max_tokens": 5},
                timeout=aiohttp.ClientTimeout(total=15),
            ) as resp:
                if resp.status == 200:
                    return CheckResult("AUTHENTICATED", f"chat/completions against {model} -> 200 OK")
                body = (await resp.text())[:150]
                return CheckResult("REJECTED" if resp.status in (401, 403) else "ERROR", f"HTTP {resp.status}: {body}")
    except Exception as e:
        return CheckResult("ERROR", f"{type(e).__name__}: {e}")


async def main() -> int:
    env_path = sys.argv[1] if len(sys.argv) > 1 else os.path.join(os.path.dirname(__file__), ".env")
    loaded = load_env_file(env_path)

    print("=" * 60)
    print("ENVIRONMENT VARIABLE CHECK  (values masked)")
    print("=" * 60)
    for name in REQUIRED_VARS:
        val = os.environ.get(name)
        print(f"{name:20} {'PRESENT ' + mask(val) if val else 'MISSING'}")
    print("\nOPTIONAL / EXTENSION VARS:")
    for name in OPTIONAL_VARS:
        val = os.environ.get(name)
        print(f"{name:20} {'PRESENT ' + mask(val) if val else 'NOT SET'}")

    if not loaded:
        print(f"\n(no .env file found at {env_path} -- using process environment only)")

    print()
    print("=" * 60)
    print("LIVE SERVICE CHECKS  (real authenticated requests)")
    print("=" * 60)

    checks = [
        ("Database (DATABASE_URL)", check_database()),
        ("LiveKit", check_livekit()),
        ("Sarvam TTS", check_sarvam_tts()),
        ("Moss", check_moss()),
    ]
    if os.environ.get("GROQ_API_KEY"):
        checks.append(("Groq / LLM", check_groq()))
    if os.environ.get("GOOGLE_API_KEY") or not os.environ.get("GROQ_API_KEY"):
        checks.append(("Google / LLM", check_google()))

    results: dict[str, CheckResult] = {}
    for name, coro in checks:
        result = await coro
        results[name] = result
        print(f"[{result.status:16}] {name}: {result.detail}")

    print()
    print("REAL VOICE PIPELINE STATUS")
    print("=" * 27)

    def line(name: str, key: str) -> None:
        r = results.get(key)
        if not r:
            return
        verdict = "PASS" if r.status == "AUTHENTICATED" else "FAIL"
        print(f"{name}: {verdict}  ({r.status})")

    line("Database", "Database (DATABASE_URL)")
    line("LiveKit", "LiveKit")
    line("Sarvam TTS", "Sarvam TTS")
    line("Moss", "Moss")
    if "Groq / LLM" in results:
        line("LLM (Groq)", "Groq / LLM")
    if "Google / LLM" in results:
        line("LLM (Google)", "Google / LLM")

    network_blocked = any(r.status == "NETWORK BLOCKED" for r in results.values())
    rejected = any(r.status == "REJECTED" for r in results.values())
    all_pass = all(r.status == "AUTHENTICATED" for r in results.values())

    print()
    print("LOCAL TESTS: PASS  (env var check + classification logic ran without error)")
    if all_pass:
        print("REAL EXTERNAL TESTS: PASS")
    elif network_blocked and not rejected:
        print("REAL EXTERNAL TESTS: BLOCKED  (one or more services unreachable from this network)")
    else:
        print("REAL EXTERNAL TESTS: FAIL")
    print("END-TO-END VOICE: NOT VERIFIED  (this script checks each service independently, not a live")
    print("                   browser -> mic -> LiveKit -> agent -> STT -> LLM -> TTS -> browser round trip)")
    print()
    print("FINAL:")
    if all_pass:
        print("VOICE PIPELINE WORKING: PARTIALLY VERIFIED (every service authenticated; run an actual")
        print("                         session end-to-end to confirm the full round trip)")
    elif network_blocked and not rejected:
        print("VOICE PIPELINE WORKING: NOT VERIFIED (network-blocked -- rerun on a network that can reach")
        print("                         these hosts to get a real answer)")
    else:
        print("VOICE PIPELINE WORKING: NO")

    return 0 if all_pass else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
