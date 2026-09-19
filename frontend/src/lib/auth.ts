import type { StoredSession } from "../types/auth";

const SESSION_STORAGE_KEY = "vap.session.v1";
const THEME_STORAGE_KEY = "vap.theme";

/**
 * The application JWT is intentionally kept in sessionStorage only (never
 * localStorage, never a cookie): it should not outlive the browser tab, and
 * it is never logged -- see lib/api.ts and useAuth.ts, neither of which
 * console.log the token or session object.
 */
export function loadStoredSession(): StoredSession | null {
  try {
    const raw = sessionStorage.getItem(SESSION_STORAGE_KEY);
    if (!raw) return null;
    return JSON.parse(raw) as StoredSession;
  } catch {
    return null;
  }
}

export function saveStoredSession(session: StoredSession): void {
  sessionStorage.setItem(SESSION_STORAGE_KEY, JSON.stringify(session));
}

export function clearStoredSession(): void {
  sessionStorage.removeItem(SESSION_STORAGE_KEY);
}

export { THEME_STORAGE_KEY };

/** Generates a unique dev room name, e.g. "dev-room-a1b2c3d4-9f21". */
export function generateRoomName(): string {
  const id =
    typeof crypto !== "undefined" && "randomUUID" in crypto
      ? crypto.randomUUID()
      : `${Date.now()}-${Math.random().toString(16).slice(2)}`;
  return `dev-room-${id}`;
}

export function generateId(prefix: string): string {
  const rand =
    typeof crypto !== "undefined" && "randomUUID" in crypto
      ? crypto.randomUUID()
      : Math.random().toString(16).slice(2);
  return `${prefix}-${rand}`;
}
