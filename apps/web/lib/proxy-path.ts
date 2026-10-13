/**
 * Map BFF proxy path segments onto the API. Only plain segments are allowed (no traversal, no
 * encoded separators). Endpoints the browser must never call directly are blocked here: login
 * events are reported only by the BFF's own callback handler.
 */
const SEGMENT = /^[A-Za-z0-9._~-]{1,128}$/;
const BLOCKED = new Set(["auth/login-events"]);

export function toApiPath(segments: string[]): string | null {
  if (segments.length === 0 || segments.length > 12) return null;
  for (const segment of segments) {
    if (!SEGMENT.test(segment) || segment === "." || segment === "..") return null;
  }
  const joined = segments.join("/");
  if (BLOCKED.has(joined)) return null;
  return `/api/v1/${joined}`;
}
