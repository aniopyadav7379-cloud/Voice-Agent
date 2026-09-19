/**
 * Types for the dev token bootstrap and application-JWT token exchange,
 * mirroring the backend's DevTokenResponse / LiveKitTokenResponse models
 * in agent/app/security/token_service.py exactly.
 */

export interface DevTokenRequest {
  tenant_slug: string;
  external_id: string;
  room_name: string;
}

export interface DevTokenResponse {
  access_token: string;
  livekit_token: string;
  tenant_id: string;
  user_id: string;
}

export interface LiveKitTokenRequest {
  room_name: string;
}

export interface LiveKitTokenResponse {
  livekit_token: string;
  tenant_id: string;
  user_id: string;
}

/** What we persist in sessionStorage under SESSION_STORAGE_KEY. */
export interface StoredSession {
  accessToken: string;
  tenantId: string;
  tenantSlug: string;
  userId: string;
  externalId: string;
}

export interface ApiError {
  status: number;
  detail: string;
}
