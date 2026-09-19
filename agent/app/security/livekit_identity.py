"""
Maps between this application's authenticated identity and LiveKit's own
participant identity/attributes. Every LiveKit API used below was verified
against the actually-installed SDK via `inspect.signature` before being
used — not assumed from documentation or memory:

  AccessToken(api_key, api_secret).with_identity(str)
                                   .with_attributes(dict[str, str])
                                   .with_grants(VideoGrants(...))
                                   .with_ttl(timedelta)
                                   .to_jwt() -> str

  rtc.Participant.identity: str
  rtc.Participant.attributes: dict[str, str]

  JobContext.wait_for_participant(identity: str | None = None) -> rtc.RemoteParticipant

Relationship this file establishes, exactly as the task asked for:

  application user (Postgres User row)
        |
  AuthenticatedIdentity (resolved via identity.py from a validated app JWT)
        |
  mint_livekit_token(): identity.user_id -> LiveKit participant `identity`
                         identity.tenant_id/tenant_slug/external_id -> `attributes`
        |
  client connects to LiveKit using that token; LIVEKIT'S OWN SERVER
  validates the signature before allowing the join — this app never
  re-verifies the JWT signature itself on this path, because LiveKit
  already did, and a participant that exists in ctx.room could only have
  gotten there with a token this backend minted
        |
  resolve_identity_from_participant(): reads participant.identity/
  attributes and re-validates the referenced user/tenant are STILL active
  in Postgres right now (see identity.py's resolve_identity_by_ids
  docstring for why "still active now" matters even though the token
  itself was already trustworthy)
        |
  AuthenticatedIdentity, same type as the app-JWT path produces — agent_entrypoint.py
  doesn't need to care which path an identity came from

NOT LIVE-VERIFIED: this sandbox has no LIVEKIT_URL/LIVEKIT_API_KEY/
LIVEKIT_API_SECRET and no network path to LiveKit Cloud. `mint_livekit_token`
has been verified to produce a real, well-formed JWT using the real SDK
(decoded and checked below in tests), and
`resolve_identity_from_participant` has been tested against a real
`rtc.Participant`-shaped fake carrying the exact attributes
`mint_livekit_token` would produce — but no token minted here has ever
actually been used to join a real LiveKit room. That gap closes only with
real credentials and a real LiveKit environment.
"""
import uuid
from datetime import timedelta

from livekit import api, rtc
from sqlalchemy.ext.asyncio import AsyncSession

from app.security.identity import AuthenticatedIdentity, IdentityResolutionError, resolve_identity_by_ids


def mint_livekit_token(
    *, identity: AuthenticatedIdentity, room_name: str, api_key: str, api_secret: str, ttl_seconds: int = 3600
) -> str:
    """Participant `identity` is the user's UUID as a string — not the
    external_id, not the tenant slug — so `resolve_identity_from_participant`
    can parse it directly as a primary-key lookup rather than a second
    string-matching query. Tenant/external_id travel as `attributes`
    instead, since LiveKit's `identity` field is a single string and this
    needs to carry more than one piece of information."""
    grants = api.VideoGrants(room_join=True, room=room_name, can_publish=True, can_subscribe=True)
    token = (
        api.AccessToken(api_key, api_secret)
        .with_identity(str(identity.user_id))
        .with_attributes({
            "tenant_id": str(identity.tenant_id),
            "tenant_slug": identity.tenant_slug,
            "external_id": identity.external_id,
        })
        .with_grants(grants)
        .with_ttl(timedelta(seconds=ttl_seconds))
    )
    return token.to_jwt()


class ParticipantIdentityError(IdentityResolutionError):
    """The participant joined (so LiveKit itself validated their token),
    but the identity/attributes it carries aren't well-formed enough to
    resolve — e.g. a non-UUID identity string. Distinct from the
    Tenant/User-not-found errors in identity.py: those mean 'this identity
    doesn't exist in our system'; this means 'this identity doesn't even
    parse', which shouldn't be reachable via a token this backend minted
    and is a stronger signal something is wrong."""


async def resolve_identity_from_participant(db: AsyncSession, participant: rtc.Participant) -> AuthenticatedIdentity:
    try:
        user_id = uuid.UUID(participant.identity)
    except (ValueError, TypeError) as e:
        raise ParticipantIdentityError(f"participant identity '{participant.identity}' is not a valid user UUID") from e

    tenant_id_raw = participant.attributes.get("tenant_id")
    if not tenant_id_raw:
        raise ParticipantIdentityError("participant attributes missing required 'tenant_id'")
    try:
        tenant_id = uuid.UUID(tenant_id_raw)
    except ValueError as e:
        raise ParticipantIdentityError(f"participant attribute tenant_id '{tenant_id_raw}' is not a valid UUID") from e

    return await resolve_identity_by_ids(db, user_id=user_id, tenant_id=tenant_id)
