import type { NextRequest } from "next/server";
import { NextResponse } from "next/server";
import { apiFetch } from "@/lib/api";
import { BFF_HEADERS, bffProblem } from "@/lib/bff-response";
import { checkCsrf } from "@/lib/csrf";
import { serverEnv } from "@/lib/env";
import { toApiPath } from "@/lib/proxy-path";
import { SESSION_COOKIE, loadSession } from "@/lib/session";
import { securityEvent } from "@/lib/security-log";
import { freshSession } from "@/lib/tokens";

export const dynamic = "force-dynamic";

const MAX_BODY_BYTES = 64 * 1024;
const MAX_QUERY_LENGTH = 2048;

type RouteContext = { params: Promise<{ path: string[] }> };

/**
 * Browser → API proxy. The browser never sees tokens: it sends its session cookie, this handler
 * attaches the access token. It forwards only /api/v1 paths, JSON bodies, and no cookies, and it
 * makes no authorisation decisions of its own — the API decides everything.
 */
async function forward(request: NextRequest, { params }: RouteContext) {
  const env = serverEnv();
  const apiPath = toApiPath((await params).path);
  if (!apiPath) return bffProblem(404, "not-found", "Not found");
  if (request.nextUrl.search.length > MAX_QUERY_LENGTH) return bffProblem(414, "uri-too-long", "Query too long");

  const sessionId = request.cookies.get(SESSION_COOKIE)?.value;
  const loaded = await loadSession(sessionId);
  if (!sessionId || !loaded) return bffProblem(401, "unauthenticated", "Sign in required");

  const csrf = checkCsrf({
    method: request.method,
    origin: request.headers.get("origin"),
    appOrigin: new URL(env.CRIMEMAP_WEB_BASE_URL).origin,
    sessionToken: loaded.csrfToken,
    submittedToken: request.headers.get("x-csrf-token"),
  });
  if (!csrf.ok) {
    securityEvent("csrf_rejected", { reason: csrf.reason, method: request.method, path: apiPath, sub: loaded.sub });
    return bffProblem(403, "csrf-failed", "Request could not be verified");
  }

  const session = await freshSession(sessionId, loaded);
  if (!session) return bffProblem(401, "unauthenticated", "Session expired");

  let body: string | undefined;
  if (!["GET", "HEAD"].includes(request.method)) {
    if (!(request.headers.get("content-type") ?? "").startsWith("application/json")) {
      return bffProblem(415, "unsupported-media-type", "JSON required");
    }
    body = await request.text();
    if (Buffer.byteLength(body) > MAX_BODY_BYTES) return bffProblem(413, "payload-too-large", "Request too large");
  }

  const upstream = await apiFetch(session, apiPath + request.nextUrl.search, { method: request.method, body });
  const headers = new Headers(BFF_HEADERS);
  for (const name of ["content-type", "content-disposition", "x-request-id", "retry-after", "www-authenticate"]) {
    const value = upstream.headers.get(name);
    if (value) headers.set(name, value);
  }
  return new NextResponse(upstream.status === 204 ? null : await upstream.arrayBuffer(), {
    status: upstream.status,
    headers,
  });
}

export { forward as GET, forward as POST, forward as PUT, forward as PATCH, forward as DELETE };
