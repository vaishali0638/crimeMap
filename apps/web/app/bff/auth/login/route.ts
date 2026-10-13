import { NextResponse, type NextRequest } from "next/server";
import { seal } from "@/lib/crypto";
import { serverEnv } from "@/lib/env";
import { callbackUrl, client, oidcConfig } from "@/lib/oidc";
import { safeReturnTo } from "@/lib/redirect";
import { cookieOptions, FLOW_COOKIE } from "@/lib/session";
import { htmlPage } from "@/lib/bff-response";

export const dynamic = "force-dynamic";

export type LoginFlow = { verifier: string; state: string; nonce: string; returnTo: string; createdAt: number };

/** Start the OIDC authorization-code flow with PKCE. `?reauth=1` forces a fresh login (step-up). */
export async function GET(request: NextRequest) {
  let config;
  try {
    config = await oidcConfig();
  } catch {
    return htmlPage("Sign-in unavailable", "The identity provider could not be reached. Try again shortly.", 503);
  }
  const returnTo = safeReturnTo(request.nextUrl.searchParams.get("returnTo"));
  const reauth = request.nextUrl.searchParams.get("reauth") === "1";
  const verifier = client.randomPKCECodeVerifier();
  const flow: LoginFlow = {
    verifier,
    state: client.randomState(),
    nonce: client.randomNonce(),
    returnTo,
    createdAt: Date.now(),
  };
  const url = client.buildAuthorizationUrl(config, {
    redirect_uri: callbackUrl(),
    scope: "openid profile email",
    code_challenge: await client.calculatePKCECodeChallenge(verifier),
    code_challenge_method: "S256",
    state: flow.state,
    nonce: flow.nonce,
    ...(reauth ? { prompt: "login", max_age: "0" } : {}),
  });
  const response = NextResponse.redirect(url, 303);
  // Lax (not Strict) so the cookie returns with the identity provider's top-level redirect.
  response.cookies.set(FLOW_COOKIE, seal(flow, serverEnv().sessionKey), { ...cookieOptions(600), sameSite: "lax" });
  response.headers.set("Cache-Control", "no-store");
  return response;
}
