/** Session lifetime rules (data-classification-and-access.md §7). */
export const IDLE_TIMEOUT_MS = 15 * 60 * 1000;
export const ABSOLUTE_LIFETIME_MS = 8 * 60 * 60 * 1000;
const REFRESH_MARGIN_MS = 60 * 1000;

export type SessionState = "active" | "idle_expired" | "absolute_expired";

export function sessionState(session: { createdAt: number; lastSeenAt: number }, now: number): SessionState {
  if (now - session.createdAt >= ABSOLUTE_LIFETIME_MS) return "absolute_expired";
  if (now - session.lastSeenAt >= IDLE_TIMEOUT_MS) return "idle_expired";
  return "active";
}

/** True when the access token expires within a minute (or already has). */
export function needsRefresh(accessExpiresAt: number, now: number): boolean {
  return accessExpiresAt - now <= REFRESH_MARGIN_MS;
}
