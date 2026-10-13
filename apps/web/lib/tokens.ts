import "server-only";
import { client, oidcConfig } from "./oidc";
import { destroySession, loadSession, saveSession, withSessionLock, type Session } from "./session";
import { needsRefresh } from "./session-policy";

/**
 * Return a session whose access token is valid for at least a minute, refreshing (with rotation)
 * if needed. A failed refresh ends the session: the user must sign in again.
 */
export async function freshSession(sessionId: string, session: Session): Promise<Session | null> {
  if (!needsRefresh(session.accessExpiresAt, Date.now())) return session;
  const result = await withSessionLock(sessionId, async () => {
    const current = await loadSession(sessionId); // another request may have refreshed already
    if (!current) return null;
    if (!needsRefresh(current.accessExpiresAt, Date.now())) return current;
    if (!current.refreshToken) {
      await destroySession(sessionId);
      return null;
    }
    try {
      const tokens = await client.refreshTokenGrant(await oidcConfig(), current.refreshToken);
      const updated: Session = {
        ...current,
        accessToken: tokens.access_token,
        refreshToken: tokens.refresh_token ?? null,
        idToken: tokens.id_token ?? current.idToken,
        accessExpiresAt: Date.now() + (tokens.expiresIn() ?? 60) * 1000,
      };
      await saveSession(sessionId, updated);
      return updated;
    } catch {
      await destroySession(sessionId);
      return null;
    }
  });
  return result;
}
