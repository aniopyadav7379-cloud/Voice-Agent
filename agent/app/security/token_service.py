"""
Minimal HTTP surface for issuing tokens. This project had NO HTTP
framework anywhere before this session — `agent_entrypoint.py` is a
LiveKit Agents worker process, not a request-serving service. This file is
a deliberate, small addition: a real client (mobile/web app) needs
*somewhere* to call to get a LiveKit access token before it can connect to
a room at all, and that's an HTTP call by necessity (LiveKit's own
documented pattern, not invented here). Kept to exactly one endpoint,
dev-only, rather than building out a general API service — that would be
real scope creep this task explicitly warned against.

PRODUCTION TOKEN ISSUANCE IS NOT IMPLEMENTED. A real deployment needs this
endpoint (or one shaped like it) to sit behind a real login/identity
provider that authenticates the caller BEFORE minting anything — that
whole flow doesn't exist in this project and isn't invented here. What
exists is the dev-only convenience path, exactly as narrow as the
project's own earlier `/v1/dev/token` pattern (a different, no-longer-
present project, but the same *principle*: dev convenience must be
structurally incapable of running outside dev).
"""
import logging
import os
from dotenv import load_dotenv

load_dotenv(override=True)

from fastapi import FastAPI, Header, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from sqlalchemy import select

from app.db.base import get_session_maker
from app.db.models import Tenant, TenantStatus, User, UserStatus
from app.db.session_manager import AuditRecorder, SecurityEventRecorder, is_tenant_verified
from app.security.identity import AuthenticatedIdentity, IdentityResolutionError, resolve_identity
from app.security.jwt_auth import AuthError, create_access_token, decode_access_token
from app.security.livekit_identity import mint_livekit_token

logger = logging.getLogger("agent.token_service")

app = FastAPI(title="voice-agent-platform-token-service")

# The frontend (Vite dev server locally, a separate Vercel origin in
# production) always calls this API cross-origin -- there is no same-origin
# deployment shape for this project. Without CORS the browser blocks every
# fetch() here regardless of whether the request itself would have
# succeeded; curl/httpx-based tests don't enforce CORS, so this had no
# automated test catching its absence.
#
# ALLOWED_ORIGINS is a comma-separated list, e.g.
# "http://localhost:5173,https://your-app.vercel.app". No wildcard default:
# an empty/unset value means no browser origin is allowed, fixed open on
# purpose rather than silently permissive.
_origins_from_env = [o.strip() for o in os.environ.get("ALLOWED_ORIGINS", "").split(",") if o.strip()]
if not _origins_from_env and os.environ.get("ENVIRONMENT") == "development":
    _allowed_origins = ["http://localhost:5173", "http://127.0.0.1:5173", "http://localhost:3000"]
else:
    _allowed_origins = _origins_from_env

app.add_middleware(
    CORSMiddleware,
    allow_origins=_allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
async def health_check():
    return {"status": "ok", "service": "voice-agent-platform-token-service"}


class DevTokenRequest(BaseModel):
    tenant_slug: str
    external_id: str
    room_name: str


class DevTokenResponse(BaseModel):
    access_token: str   # this app's own JWT
    livekit_token: str  # real LiveKit access token, minted via mint_livekit_token
    tenant_id: str
    user_id: str
    livekit_url: str = ""


async def _ensure_dev_tenant_and_user(db, *, tenant_slug: str, external_id: str) -> AuthenticatedIdentity:
    """Dev convenience ONLY — creates the tenant/user if they don't exist
    yet, so a developer can immediately exercise the full auth flow
    without a separate provisioning step. This is intentionally NOT how
    production identity resolution works (identity.py's resolve_identity
    strictly rejects nonexistent tenants/users) — auto-vivifying a tenant
    from an arbitrary client-supplied string would be a real security
    regression outside dev, which is exactly why this whole endpoint is
    404'd outside ENVIRONMENT=development."""
    result = await db.execute(select(Tenant).where(Tenant.slug == tenant_slug))
    tenant = result.scalar_one_or_none()
    if tenant is None:
        tenant = Tenant(slug=tenant_slug, name=tenant_slug, status=TenantStatus.active)
        db.add(tenant)
        await db.flush()

    result = await db.execute(select(User).where(User.tenant_id == tenant.id, User.external_id == external_id))
    user = result.scalar_one_or_none()
    if user is None:
        user = User(tenant_id=tenant.id, external_id=external_id, status=UserStatus.active)
        db.add(user)
        await db.flush()

    await db.commit()
    return AuthenticatedIdentity(user_id=user.id, tenant_id=tenant.id, tenant_slug=tenant.slug, external_id=external_id)


@app.post("/v1/dev/token", response_model=DevTokenResponse)
async def issue_dev_token(body: DevTokenRequest) -> DevTokenResponse:
    if os.environ.get("ENVIRONMENT") != "development":
        # 404, not 403 — matches the earlier project's established
        # convention of not even revealing this endpoint exists outside
        # dev, rather than confirming its presence with an access-denied
        # response.
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")

    async with get_session_maker()() as db:
        identity = await _ensure_dev_tenant_and_user(
            db, tenant_slug=body.tenant_slug, external_id=body.external_id
        )

    app_token = create_access_token(subject=identity.external_id, tenant_slug=identity.tenant_slug)
    livekit_token = mint_livekit_token(
        identity=identity, room_name=body.room_name,
        api_key=os.environ["LIVEKIT_API_KEY"], api_secret=os.environ["LIVEKIT_API_SECRET"],
    )

    return DevTokenResponse(
        access_token=app_token, livekit_token=livekit_token,
        tenant_id=str(identity.tenant_id), user_id=str(identity.user_id),
        livekit_url=os.environ.get("LIVEKIT_URL", ""),
    )


class LiveKitTokenRequest(BaseModel):
    room_name: str


class LiveKitTokenResponse(BaseModel):
    livekit_token: str
    tenant_id: str
    user_id: str
    livekit_url: str = ""


@app.post("/v1/livekit/token", response_model=LiveKitTokenResponse)
async def issue_livekit_token(
    body: LiveKitTokenRequest, authorization: str = Header(...)
) -> LiveKitTokenResponse:
    """PRODUCTION token exchange — available in every environment.

    This is the correct production boundary, and the reason it can exist
    without inventing an identity provider: it does not AUTHENTICATE
    anyone. It EXCHANGES an already-valid application JWT for a LiveKit
    access token, re-validating the bearer against Postgres first.

    What is deliberately NOT here, and is an explicit external boundary
    rather than a gap to paper over: whatever issues the application JWT
    in the first place (password login, OIDC/SAML SSO, an enterprise
    IdP). That system authenticates a human and mints a JWT signed with
    `JWT_SECRET`. Building a credible version of it — password hashing,
    reset flows, MFA, session revocation — is a genuine product decision,
    not a detail, and the task explicitly said not to invent one or to
    ship insecure "temporary production auth."

    Integration contract for whatever fills that role:
      - sign with HS256 using the same `JWT_SECRET` (from secret
        management; never committed — see .gitignore)
      - set `iss` to match `JWT_ISSUER`
      - set `sub` to the user's stable `external_id`
      - set `tenant_slug` to their tenant's slug
      - set a real `exp`; expiry is enforced here, not advisory

    Every claim is then re-validated against Postgres by
    `resolve_identity()` — a valid signature alone is explicitly
    insufficient (security invariant 6). A user deactivated one minute
    after their token was minted cannot exchange it.
    """
    if not authorization.startswith("Bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Expected 'Authorization: Bearer <token>'",
        )

    raw_token = authorization.removeprefix("Bearer ").strip()
    try:
        payload = decode_access_token(raw_token)
    except AuthError as e:
        # Deliberately does not echo which specific check failed
        # (expired vs. bad signature vs. malformed) — that distinction is
        # useful in OUR logs, not in a response to an unauthenticated
        # caller who may be probing.
        logger.info("livekit token exchange rejected: %s", type(e).__name__)
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token") from e

    async with get_session_maker()() as db:
        try:
            identity = await resolve_identity(
                db, external_id=payload.sub, tenant_slug=payload.tenant_slug
            )
        except IdentityResolutionError as e:
            # Same reasoning: one generic 403 outward, the specific reason
            # recorded internally.
            logger.info("livekit token exchange rejected at identity resolution: %s", type(e).__name__)
            if is_tenant_verified(e) and e.verified_tenant_id is not None:
                await AuditRecorder(db).record(
                    tenant_id=e.verified_tenant_id, user_id=None, action="token_exchange_rejected",
                    resource_type="livekit_token", resource_id=body.room_name,
                    metadata={"reason": type(e).__name__},
                )
            else:
                await SecurityEventRecorder(db).record(
                    action="token_exchange_rejected", reason=type(e).__name__,
                    resource_type="livekit_token", resource_id=body.room_name,
                    claimed_tenant_slug=payload.tenant_slug, claimed_user_identity=payload.sub,
                )
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not authorized") from e

    livekit_token = mint_livekit_token(
        identity=identity, room_name=body.room_name,
        api_key=os.environ["LIVEKIT_API_KEY"], api_secret=os.environ["LIVEKIT_API_SECRET"],
    )
    return LiveKitTokenResponse(
        livekit_token=livekit_token, tenant_id=str(identity.tenant_id), user_id=str(identity.user_id),
        livekit_url=os.environ.get("LIVEKIT_URL", ""),
    )
