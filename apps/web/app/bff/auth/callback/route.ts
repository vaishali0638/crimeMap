import type { NextRequest } from "next/server";
import { apiFetch } from "@/lib/api";
import { htmlPage } from "@/lib/bff-response";
import { unseal } from "@/lib/crypto";
import { serverEnv } from "@/lib/env";
import { callbackUrl, client, oidcConfig } from "@/lib/oidc";
import { CSRF_COOKIE, FLOW_COOKIE, SESSION_COOKIE, cookieOptions, createSession, destroySession } from "@/lib/session";
import { ABSOLUTE_LIFETIME_MS } from "@/lib/session-policy";
import type { LoginFlow } from "../login/route";

export const dynamic = "force-dynamic";

const FLOW_MAX_AGE_MS = 10 * 60 * 1000;

/** Finish sign-in: exchange the code (PKCE, state, nonce checked), create the server-side session,
 *  and record LOGIN in the CRIMEMAP audit log. If the audit write fails, sign-in fails (fail closed). */
export async function GET(request: NextRequest) {
  const env = serverEnv();
  const raw = request.cookies.get(FLOW_COOKIE)?.value;
  const flow = raw ? unseal<LoginFlow>(raw, env.sessionKey) : null;
  if (!flow || Date.now() - flow.createdAt > FLOW_MAX_AGE_MS) {
    return htmlPage("Sign-in expired", "Your sign-in attempt expired or was started in another browser. Please try again.", 400);
  }

  let tokens;
  try {
    const currentUrl = new URL(callbackUrl() + request.nextUrl.search);
    tokens = await client.authorizationCodeGrant(await oidcConfig(), currentUrl, {
      pkceCodeVerifier: flow.verifier,
      expectedState: flow.state,
      expectedNonce: flow.nonce,
      idTokenExpected: true,
    });
  } catch {
    return htmlPage("Sign-in failed", "Sign-in could not be completed. Please try again.", 401);
  }

  const claims = tokens.claims();
  if (!claims?.sub) return htmlPage("Sign-in failed", "The identity provider returned no subject.", 401);

  const { sessionId, session } = await createSession({
    sub: claims.sub,
    accessToken: tokens.access_token,
    refreshToken: tokens.refresh_token ?? null,
    idToken: tokens.id_token ?? null,
    accessExpiresAt: Date.now() + (tokens.expiresIn() ?? 60) * 1000,
    authTime: typeof claims.auth_time === "number" ? claims.auth_time * 1000 : null,
  });

  let audited = false;
  try {
    const result = await apiFetch(session, "/api/v1/auth/login-events", { method: "POST" });
    audited = result.status === 204;
  } catch {
    audited = false;
  }
  if (!audited) {
    await destroySession(sessionId);
    return htmlPage("Sign-in not recorded", "Your sign-in could not be recorded in the audit log, so it was cancelled. Please try again or contact the administrator.", 503);
  }

  // A same-origin hop page (not a 302) so the SameSite=Strict session cookie is sent on the next navigation.
  const response = htmlPage("Signed in", "Opening CRIMEMAP…", 200, flow.returnTo);
  const maxAge = Math.floor(ABSOLUTE_LIFETIME_MS / 1000);
  response.cookies.set(SESSION_COOKIE, sessionId, cookieOptions(maxAge));
  response.cookies.set(CSRF_COOKIE, session.csrfToken, cookieOptions(maxAge, false));
  response.cookies.set(FLOW_COOKIE, "", { ...cookieOptions(0), sameSite: "lax" });
  return response;
}
