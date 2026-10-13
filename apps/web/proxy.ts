import { NextResponse, type NextRequest } from "next/server";
import { buildCsp } from "./lib/csp";
import { mapConfig } from "./lib/map-config";

/**
 * Next.js 16 proxy (formerly middleware). For every page request it:
 *  - creates a per-request CSP nonce (Next applies it to its own scripts),
 *  - sends unauthenticated visitors to sign-in (the session itself is validated server-side),
 *  - passes the current path to server components for a safe post-login return.
 * /bff/* routes and static assets are excluded; BFF handlers set their own headers.
 */
const PUBLIC_PATHS = new Set(["/signed-out", "/login"]);

const DEFAULT_MAP_ORIGINS = [
  "https://tiles.openfreemap.org",
  "https://tile.openstreetmap.org",
  "https://a.tile.openstreetmap.org",
  "https://b.tile.openstreetmap.org",
  "https://c.tile.openstreetmap.org",
  "https://a.basemaps.cartocdn.com",
  "https://b.basemaps.cartocdn.com",
  "https://c.basemaps.cartocdn.com",
  "https://d.basemaps.cartocdn.com",
  "https://demotiles.maplibre.org",
];

export function proxy(request: NextRequest) {
  const nonce = Buffer.from(crypto.randomUUID()).toString("base64");
  const csp = buildCsp({
    nonce,
    dev: process.env.NODE_ENV === "development",
    https: (process.env.CRIMEMAP_WEB_BASE_URL ?? "").startsWith("https://"),
    identityProviderOrigin: new URL(process.env.CRIMEMAP_OIDC_ISSUER ?? "http://localhost:8080").origin,
    // Validated: includes default OpenStreetMap / glyph origins plus any configured custom origins.
    mapOrigins: Array.from(new Set([...DEFAULT_MAP_ORIGINS, ...mapConfig(process.env).origins])),
  });

  const { pathname, search } = request.nextUrl;
  if (!PUBLIC_PATHS.has(pathname) && !request.cookies.has("crimemap_session")) {
    const login = new URL("/login", request.url);
    login.searchParams.set("returnTo", pathname + search);
    return NextResponse.redirect(login, 303);
  }

  const requestHeaders = new Headers(request.headers);
  requestHeaders.set("x-nonce", nonce);
  requestHeaders.set("x-crimemap-path", pathname + search);
  requestHeaders.set("Content-Security-Policy", csp);
  const response = NextResponse.next({ request: { headers: requestHeaders } });
  response.headers.set("Content-Security-Policy", csp);
  response.headers.set("Cache-Control", "no-store");
  return response;
}

export const config = {
  matcher: [
    {
      source: "/((?!bff/|_next/static|_next/image|favicon.ico|maplibre/).*)",
      missing: [{ type: "header", key: "next-router-prefetch" }],
    },
  ],
};
