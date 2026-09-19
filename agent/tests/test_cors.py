"""
Tests for the CORSMiddleware added to app.security.token_service.

Without this, a browser-based frontend on a different origin (Vite dev
server on :5173, or a deployed Vercel origin) cannot call this API at all --
the browser blocks the response regardless of whether the request itself
would have succeeded. curl/httpx-based tests elsewhere in this suite don't
enforce CORS, which is exactly why this had no test before now.
"""
import pytest
from starlette.testclient import TestClient

from app.security.token_service import app as token_app


def make_client(monkeypatch, allowed_origins: str):
    monkeypatch.setenv("ALLOWED_ORIGINS", allowed_origins)
    monkeypatch.setenv("ENVIRONMENT", "development")
    # Middleware is only installed once, at module import time, so re-import
    # the app fresh with ALLOWED_ORIGINS already set for each test case.
    import importlib

    import app.security.token_service as token_service_module

    importlib.reload(token_service_module)
    return TestClient(token_service_module.app)


def test_no_allowed_origins_means_no_cors_headers(monkeypatch):
    client = make_client(monkeypatch, allowed_origins="")

    resp = client.options(
        "/v1/dev/token",
        headers={"Origin": "https://your-app.vercel.app", "Access-Control-Request-Method": "POST"},
    )

    assert "access-control-allow-origin" not in {k.lower() for k in resp.headers}


def test_configured_origin_is_allowed_on_preflight(monkeypatch):
    client = make_client(monkeypatch, allowed_origins="https://your-app.vercel.app,http://localhost:5173")

    resp = client.options(
        "/v1/dev/token",
        headers={
            "Origin": "https://your-app.vercel.app",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "content-type",
        },
    )

    assert resp.status_code == 200
    assert resp.headers["access-control-allow-origin"] == "https://your-app.vercel.app"


def test_unlisted_origin_is_not_allowed_on_preflight(monkeypatch):
    client = make_client(monkeypatch, allowed_origins="https://your-app.vercel.app")

    resp = client.options(
        "/v1/dev/token",
        headers={
            "Origin": "https://some-other-site.example",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "content-type",
        },
    )

    assert "access-control-allow-origin" not in {k.lower() for k in resp.headers}


def test_configured_origin_is_reflected_on_the_real_response(monkeypatch, session_maker):
    monkeypatch.setenv("JWT_SECRET", "test-secret-for-cors-check-only")
    monkeypatch.setenv("LIVEKIT_API_KEY", "fake-lk-key")
    monkeypatch.setenv("LIVEKIT_API_SECRET", "fake-lk-secret-32-bytes-long-enough")
    client = make_client(monkeypatch, allowed_origins="http://localhost:5173")

    # Point the reloaded module's session-maker singleton at the same
    # dedicated test database the `session_maker` fixture already set up,
    # exactly as test_auth_identity.py's dev-token test does.
    import app.db.base as db_base
    from tests.conftest import test_database_url

    db_base._engine = db_base.build_engine(test_database_url())
    db_base._session_maker = db_base.build_session_maker(db_base._engine)

    resp = client.post(
        "/v1/dev/token",
        json={"tenant_slug": "cors-check-tenant", "external_id": "cors-check-user", "room_name": "z"},
        headers={"Origin": "http://localhost:5173"},
    )

    assert resp.status_code == 200, resp.text
    assert resp.headers.get("access-control-allow-origin") == "http://localhost:5173"
