import { describe, expect, it } from "vitest";
import nextConfig from "../next.config";

describe("web security headers", () => {
  it("uses Referrer-Policy same-origin, not no-referrer", async () => {
    // With no-referrer, browsers send `Origin: null` on same-origin form POSTs, which the CSRF
    // origin check (correctly) rejects — sign-out failed this way in Phase 1 browser testing.
    // same-origin still sends no referrer to any other site.
    const rules = await nextConfig.headers!();
    const policy = rules.flatMap((r) => r.headers).find((h) => h.key === "Referrer-Policy");
    expect(policy?.value).toBe("same-origin");
  });
});
