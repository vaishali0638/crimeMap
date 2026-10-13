import { NextResponse, type NextRequest } from "next/server";
import { bffProblem } from "@/lib/bff-response";
import { checkCsrf } from "@/lib/csrf";
import { serverEnv } from "@/lib/env";
import { client, oidcConfig } from "@/lib/oidc";
import { securityEvent } from "@/lib/security-log";
import { CSRF_COOKIE, SESSION_COOKIE, cookieOptions, destroySession, loadSession } from "@/lib/session";

export const dynamic = "force-dynamic";

/** Sign out (POST only, CSRF-protected): end the CRIMEMAP session, then the identity-provider session. */
export async function POST(request: NextRequest) {
  const env = serverEnv();
  const sessionId = request.cookies.get(SESSION_COOKIE)?.value;
  const session = await loadSession(sessionId);
  const signedOut = new URL("/signed-out", env.CRIMEMAP_WEB_BASE_URL);
  let target: URL = signedOut;

  if (session) {
    const form = await request.formData().catch(() => null);
    const submitted = form?.get("csrf");
    const csrf = checkCsrf({
      method: "POST",
      origin: request.headers.get("origin"),
      appOrigin: new URL(env.CRIMEMAP_WEB_BASE_URL).origin,
      sessionToken: session.csrfToken,
      submittedToken: typeof submitted === "string" ? submitted : null,
    });
    if (!csrf.ok) {
      securityEvent("csrf_rejected", { reason: csrf.reason, method: "POST", path: "/bff/auth/logout", sub: session.sub });
      return bffProblem(403, "csrf-failed", "Request could not be verified");
    }
    await destroySession(sessionId);
    try {
      target = client.buildEndSessionUrl(await oidcConfig(), {
        post_logout_redirect_uri: signedOut.toString(),
        ...(session.idToken ? { id_token_hint: session.idToken } : {}),
      });
    } catch {
      target = signedOut;
    }
  }

  const response = NextResponse.redirect(target, 303);
  response.cookies.set(SESSION_COOKIE, "", cookieOptions(0));
  response.cookies.set(CSRF_COOKIE, "", cookieOptions(0, false));
  response.headers.set("Cache-Control", "no-store");
  return response;
}
