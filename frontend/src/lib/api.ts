import type {
  DevTokenRequest,
  DevTokenResponse,
  LiveKitTokenRequest,
  LiveKitTokenResponse,
} from "../types/auth";

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL ?? "http://localhost:8000";

export class ApiRequestError extends Error {
  status: number;
  detail: string;

  constructor(status: number, detail: string) {
    super(detail);
    this.name = "ApiRequestError";
    this.status = status;
    this.detail = detail;
  }
}

async function parseErrorDetail(response: Response): Promise<string> {
  try {
    const body = (await response.json()) as { detail?: string };
    if (body && typeof body.detail === "string") return body.detail;
  } catch {
    // response wasn't JSON; fall through to the status text below
  }
  return response.statusText || `Request failed with status ${response.status}`;
}

/**
 * Dev-only bootstrap: POST /v1/dev/token. Only reachable when the backend is
 * running with ENVIRONMENT=development (see token_service.py) -- a 404 here
 * means the backend has that endpoint disabled, not that the URL is wrong.
 */
export async function fetchDevToken(body: DevTokenRequest): Promise<DevTokenResponse> {
  const response = await fetch(`${API_BASE_URL}/v1/dev/token`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });

  if (!response.ok) {
    throw new ApiRequestError(response.status, await parseErrorDetail(response));
  }

  return (await response.json()) as DevTokenResponse;
}

/**
 * Production token exchange: POST /v1/livekit/token with the application JWT
 * as a bearer token. Used to mint a fresh LiveKit token for a new room
 * without re-running the dev bootstrap.
 */
export async function fetchLiveKitToken(
  accessToken: string,
  body: LiveKitTokenRequest,
): Promise<LiveKitTokenResponse> {
  const response = await fetch(`${API_BASE_URL}/v1/livekit/token`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Authorization: `Bearer ${accessToken}`,
    },
    body: JSON.stringify(body),
  });

  if (!response.ok) {
    throw new ApiRequestError(response.status, await parseErrorDetail(response));
  }

  return (await response.json()) as LiveKitTokenResponse;
}

export const LIVEKIT_URL = import.meta.env.VITE_LIVEKIT_URL ?? "";
