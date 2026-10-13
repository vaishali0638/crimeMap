import "server-only";
import { cache } from "react";
import { cookies, headers } from "next/headers";
import { redirect } from "next/navigation";
import { apiFetch, type Me } from "./api";
import { safeReturnTo } from "./redirect";
import { SESSION_COOKIE, loadSession, type Session } from "./session";
import { freshSession } from "./tokens";

/**
 * The signed-in user (from the API, never from the browser), or a redirect to sign-in. Cached for
 * the duration of one request, so the layout and the page share a single /auth/me call.
 */
export const requireUser = cache(async (): Promise<{ me: Me; session: Session }> => {
  const sessionId = (await cookies()).get(SESSION_COOKIE)?.value;
  const returnTo = safeReturnTo((await headers()).get("x-crimemap-path"));
  const toLogin = `/login?returnTo=${encodeURIComponent(returnTo)}`;
  const loaded = sessionId ? await loadSession(sessionId) : null;
  const session = loaded && sessionId ? await freshSession(sessionId, loaded) : null;
  if (!session) redirect(toLogin);
  const response = await apiFetch(session, "/api/v1/auth/me");
  if (response.status === 401) redirect(toLogin);
  if (!response.ok) throw new Error(`identity lookup failed (${response.status})`);
  return { me: (await response.json()) as Me, session };
});
