/**
 * Content-Security-Policy for app pages (nonce-based, per Next.js 16 guidance). `form-action`
 * allows the identity provider's origin because sign-out POSTs to the BFF, which redirects to the
 * provider's end-session endpoint, and browsers apply form-action to that redirect. A basemap origin
 * is allowed only when one is configured (ADR-033).
 */
export type CspOptions = {
  nonce: string;
  dev: boolean;
  https: boolean;
  identityProviderOrigin: string;
  /** Approved basemap tile/style origins (none by default: the map then loads nothing third-party). */
  mapOrigins?: string[];
};

function bareOrigin(value: string, name: string): string {
  const url = new URL(value);
  if (url.origin !== value) throw new Error(`${name} must be a bare origin`);
  return url.origin;
}

export function buildCsp({ nonce, dev, https, identityProviderOrigin, mapOrigins = [] }: CspOptions): string {
  const idp = new URL(bareOrigin(identityProviderOrigin, "identityProviderOrigin"));
  const maps = mapOrigins.map((o) => bareOrigin(o, "mapOrigins"));
  const extra = maps.length ? ` ${maps.join(" ")}` : "";
  return [
    "default-src 'self'",
    `script-src 'self' 'nonce-${nonce}' 'strict-dynamic'${dev ? " 'unsafe-eval'" : ""}`,
    dev ? "style-src 'self' 'unsafe-inline'" : `style-src 'self' 'nonce-${nonce}'`,
    `img-src 'self' data: blob:${extra}`,
    "font-src 'self'",
    `connect-src 'self'${extra}${dev ? " ws:" : ""}`,
    // MapLibre's worker is served from /maplibre (same origin), so no blob: workers are needed.
    "worker-src 'self'",
    "object-src 'none'",
    "base-uri 'none'",
    `form-action 'self' ${idp.origin}`,
    "frame-ancestors 'none'",
    ...(https ? ["upgrade-insecure-requests"] : []),
  ].join("; ");
}
