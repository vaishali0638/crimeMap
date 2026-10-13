import "server-only";

/**
 * Structured security events from the BFF (one JSON line on stderr), for requests rejected before
 * they reach the API — e.g. CSRF failures — and therefore absent from the API's audit log.
 * Never include tokens, cookies, or request bodies.
 */
export function securityEvent(event: string, fields: Record<string, string | number | null>): void {
  console.warn(JSON.stringify({ ts: new Date().toISOString(), level: "WARNING", logger: "crimemap.bff.security", event, ...fields }));
}
