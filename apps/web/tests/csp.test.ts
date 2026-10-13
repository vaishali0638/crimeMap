import { describe, expect, it } from "vitest";
import { buildCsp } from "../lib/csp";

const base = { nonce: "abc123", dev: false, https: false, identityProviderOrigin: "http://localhost:8080" };
const directive = (csp: string, name: string) => csp.split("; ").find((d) => d.startsWith(`${name} `)) ?? "";

describe("buildCsp", () => {
  it("allows form submissions to redirect only to self and the identity provider", () => {
    // Sign-out POSTs to the BFF, which 303-redirects to the IdP end-session endpoint; browsers apply
    // form-action to that redirect (found in Phase 1 browser testing).
    expect(directive(buildCsp(base), "form-action")).toBe("form-action 'self' http://localhost:8080");
  });

  it("uses a nonce and strict-dynamic for scripts, without unsafe-eval in production", () => {
    const scripts = directive(buildCsp(base), "script-src");
    expect(scripts).toBe("script-src 'self' 'nonce-abc123' 'strict-dynamic'");
  });

  it("adds unsafe-eval and websocket connections only in development", () => {
    const csp = buildCsp({ ...base, dev: true });
    expect(directive(csp, "script-src")).toContain("'unsafe-eval'");
    expect(directive(csp, "connect-src")).toContain("ws:");
  });

  it("upgrades insecure requests only when served over https", () => {
    expect(buildCsp(base)).not.toContain("upgrade-insecure-requests");
    expect(buildCsp({ ...base, https: true })).toContain("upgrade-insecure-requests");
  });

  it.each(["frame-ancestors 'none'", "object-src 'none'", "base-uri 'none'", "default-src 'self'"])(
    "always includes %s",
    (expected) => expect(buildCsp(base)).toContain(expected),
  );

  it("rejects an identity-provider value that is not a bare origin", () => {
    expect(() => buildCsp({ ...base, identityProviderOrigin: "http://localhost:8080; script-src *" })).toThrow();
  });
});

describe("buildCsp map support", () => {
  it("allows map workers from the app's own origin only", () => {
    expect(directive(buildCsp(base), "worker-src")).toBe("worker-src 'self'");
  });

  it("adds a configured basemap origin to connect-src and img-src, and nothing by default", () => {
    expect(directive(buildCsp(base), "connect-src")).toBe("connect-src 'self'");
    const csp = buildCsp({ ...base, mapOrigins: ["https://tiles.example.gov.in"] });
    expect(directive(csp, "connect-src")).toBe("connect-src 'self' https://tiles.example.gov.in");
    expect(directive(csp, "img-src")).toContain("https://tiles.example.gov.in");
  });

  it("rejects a basemap value that is not a bare origin", () => {
    expect(() => buildCsp({ ...base, mapOrigins: ["https://tiles.example; script-src *"] })).toThrow();
  });
});
