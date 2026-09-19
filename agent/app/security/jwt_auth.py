"""
Application-level JWT — authenticates a user to THIS backend, separate from
and prior to the LiveKit access token issued afterward (see
livekit_identity.py). This is a clean build: `app/security/` was a 0-byte
empty placeholder before this session, confirmed by checking it directly
rather than assuming. No existing JWT implementation exists anywhere in
this project to inspect or preserve.

Design (HS256, `sub`/`tenant_slug`/`exp`/`iss` claims, explicit exception
hierarchy) is informed by a proven, separately-tested pattern from an
earlier, distinct FastAPI gateway project built earlier in this
conversation — adapted here, not copy-pasted, since that project doesn't
exist in this codebase and had no `user_id` claim at all (this one does,
because that's this milestone's actual requirement).

This JWT answers "who is this, and which tenant are they claiming to
belong to" at the authentication boundary. It does NOT by itself grant
access to anything — `identity.py`'s `resolve_identity()` is what turns
these claims into a validated, DB-backed `AuthenticatedIdentity`, checking
the user and tenant actually exist and are active. A merely-valid JWT
signature is necessary but not sufficient.
"""
import os
from datetime import datetime, timedelta, timezone

import jwt
from pydantic import BaseModel


class TokenPayload(BaseModel):
    sub: str            # the user's external_id (stable identifier from whatever identity source authenticated them)
    tenant_slug: str     # which tenant they're claiming to belong to — re-validated against Postgres downstream
    iat: int
    exp: int
    iss: str


class AuthError(Exception):
    """Base for all token problems. Callers that just need a yes/no can
    catch this; callers that need to distinguish reasons (e.g. to decide
    whether re-auth vs. re-login is appropriate) can catch the specific
    subclasses below."""


class ExpiredTokenError(AuthError):
    pass


class InvalidTokenError(AuthError):
    """Bad signature, wrong issuer, or otherwise fails cryptographic/claim
    validation — as opposed to being merely expired."""


class MalformedTokenError(AuthError):
    """Decodes and validates cryptographically, but is missing/has the
    wrong shape for required claims."""


def _get_secret() -> str:
    # Read at call time, not at import time — matters for tests that set
    # JWT_SECRET via monkeypatch/os.environ after this module is imported.
    return os.environ["JWT_SECRET"]


def _get_issuer() -> str:
    return os.environ.get("JWT_ISSUER", "voice-agent-platform")


def create_access_token(*, subject: str, tenant_slug: str, ttl_seconds: int = 3600) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "sub": subject,
        "tenant_slug": tenant_slug,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(seconds=ttl_seconds)).timestamp()),
        "iss": _get_issuer(),
    }
    return jwt.encode(payload, _get_secret(), algorithm="HS256")


def decode_access_token(token: str) -> TokenPayload:
    try:
        raw = jwt.decode(token, _get_secret(), algorithms=["HS256"], issuer=_get_issuer())
    except jwt.ExpiredSignatureError as e:
        raise ExpiredTokenError("token has expired") from e
    except jwt.InvalidTokenError as e:
        # Covers bad signature, wrong issuer, malformed JWT structure, etc.
        # — anything PyJWT itself rejects before we even see claims.
        raise InvalidTokenError(str(e)) from e

    try:
        return TokenPayload(**raw)
    except Exception as e:
        raise MalformedTokenError(f"token claims do not match expected shape: {e}") from e
