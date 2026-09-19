import { useCallback, useState } from "react";
import { ApiRequestError, fetchDevToken, fetchLiveKitToken } from "../lib/api";
import { clearStoredSession, generateRoomName, loadStoredSession, saveStoredSession } from "../lib/auth";
import type { StoredSession } from "../types/auth";

interface ConnectResult {
  livekitToken: string;
  roomName: string;
}

export function useAuth() {
  const [session, setSession] = useState<StoredSession | null>(() => loadStoredSession());
  const [isAuthenticating, setIsAuthenticating] = useState(false);
  const [authError, setAuthError] = useState<string | null>(null);

  /** First-time bootstrap via the dev-only endpoint. Mints both the app JWT and a LiveKit token. */
  const bootstrap = useCallback(
    async (tenantSlug: string, externalId: string): Promise<ConnectResult> => {
      setIsAuthenticating(true);
      setAuthError(null);
      const roomName = generateRoomName();
      try {
        const response = await fetchDevToken({
          tenant_slug: tenantSlug,
          external_id: externalId,
          room_name: roomName,
        });
        const next: StoredSession = {
          accessToken: response.access_token,
          tenantId: response.tenant_id,
          tenantSlug,
          userId: response.user_id,
          externalId,
        };
        saveStoredSession(next);
        setSession(next);
        return { livekitToken: response.livekit_token, roomName };
      } catch (err) {
        const message =
          err instanceof ApiRequestError
            ? err.status === 404
              ? "Dev token endpoint isn't available. Is ENVIRONMENT=development set on the backend?"
              : err.detail
            : "Could not reach the backend. Is it running at the configured API base URL?";
        setAuthError(message);
        throw err;
      } finally {
        setIsAuthenticating(false);
      }
    },
    [],
  );

  /** Reuses the stored app JWT to mint a token for a new room, without repeating the dev bootstrap. */
  const startNewSession = useCallback(async (): Promise<ConnectResult> => {
    if (!session) throw new Error("No stored session to reuse.");
    setIsAuthenticating(true);
    setAuthError(null);
    const roomName = generateRoomName();
    try {
      const response = await fetchLiveKitToken(session.accessToken, { room_name: roomName });
      return { livekitToken: response.livekit_token, roomName };
    } catch (err) {
      const message =
        err instanceof ApiRequestError
          ? err.status === 401
            ? "Session expired. Please sign in again."
            : err.detail
          : "Could not reach the backend to exchange the token.";
      setAuthError(message);
      if (err instanceof ApiRequestError && err.status === 401) {
        clearStoredSession();
        setSession(null);
      }
      throw err;
    } finally {
      setIsAuthenticating(false);
    }
  }, [session]);

  const signOut = useCallback(() => {
    clearStoredSession();
    setSession(null);
  }, []);

  return { session, isAuthenticating, authError, bootstrap, startNewSession, signOut };
}
