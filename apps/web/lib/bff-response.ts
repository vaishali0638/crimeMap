import "server-only";
import { NextResponse } from "next/server";

const BFF_HEADERS = {
  "Cache-Control": "no-store",
  "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'",
  "X-Content-Type-Options": "nosniff",
  "Referrer-Policy": "same-origin",
};

const escapeHtml = (value: string) =>
  value.replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c] ?? c);

/** Minimal same-origin HTML page (used for sign-in errors and the post-login hop). */
export function htmlPage(title: string, body: string, status = 200, refreshTo?: string): NextResponse {
  const refresh = refreshTo ? `<meta http-equiv="refresh" content="0;url=${escapeHtml(refreshTo)}">` : "";
  const html = `<!doctype html><html lang="en"><head><meta charset="utf-8"><title>${escapeHtml(title)}</title>${refresh}</head>
<body style="font-family:system-ui,sans-serif;margin:3rem;color:#1B2433;background:#F7F7F5">
<p style="font-weight:600">DEMO / SYNTHETIC DATA · RESTRICTED — Internal Police Use Only</p>
<h1 style="font-size:1.25rem">${escapeHtml(title)}</h1><p>${escapeHtml(body)}</p>
${refreshTo ? `<p><a href="${escapeHtml(refreshTo)}" style="color:#246A67">Continue</a></p>` : `<p><a href="/bff/auth/login" style="color:#246A67">Sign in</a></p>`}
</body></html>`;
  return new NextResponse(html, { status, headers: { ...BFF_HEADERS, "Content-Type": "text/html; charset=utf-8" } });
}

/** RFC 9457 problem response from the BFF itself. */
export function bffProblem(status: number, slug: string, title: string): NextResponse {
  return NextResponse.json(
    { type: `https://crimemap.local/errors/${slug}`, title, status },
    { status, headers: { ...BFF_HEADERS, "Content-Type": "application/problem+json" } },
  );
}

export { BFF_HEADERS };
