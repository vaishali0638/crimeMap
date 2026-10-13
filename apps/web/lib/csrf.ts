import { timingSafeEqual } from "node:crypto";

/**
 * CSRF defence for cookie-authenticated, state-changing requests: the request must come from the
 * application's own origin AND carry the session's CSRF token (double submit, constant-time compare).
 */
const SAFE_METHODS = new Set(["GET", "HEAD", "OPTIONS"]);

export type CsrfInput = {
  method: string;
  origin: string | null;
  appOrigin: string;
  sessionToken: string;
  submittedToken: string | null;
};

export type CsrfResult = { ok: true; reason?: undefined } | { ok: false; reason: string };

export function checkCsrf(input: CsrfInput): CsrfResult {
  if (SAFE_METHODS.has(input.method.toUpperCase())) return { ok: true };
  if (!input.origin) return { ok: false, reason: "origin_missing" };
  if (input.origin !== input.appOrigin) return { ok: false, reason: "origin_mismatch" };
  if (!input.submittedToken) return { ok: false, reason: "token_missing" };
  const expected = Buffer.from(input.sessionToken, "utf8");
  const submitted = Buffer.from(input.submittedToken, "utf8");
  if (expected.length !== submitted.length || !timingSafeEqual(expected, submitted)) {
    return { ok: false, reason: "token_mismatch" };
  }
  return { ok: true };
}
