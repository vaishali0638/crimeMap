import { describe, expect, it } from "vitest";
import { ABSOLUTE_LIFETIME_MS, IDLE_TIMEOUT_MS, needsRefresh, sessionState } from "../lib/session-policy";

const t0 = Date.UTC(2026, 9, 11, 10, 0, 0);

describe("session lifetime policy", () => {
  it("is active within the idle window", () => {
    expect(sessionState({ createdAt: t0, lastSeenAt: t0 }, t0 + IDLE_TIMEOUT_MS - 1)).toBe("active");
  });

  it("expires after 15 idle minutes", () => {
    expect(IDLE_TIMEOUT_MS).toBe(15 * 60 * 1000);
    expect(sessionState({ createdAt: t0, lastSeenAt: t0 }, t0 + IDLE_TIMEOUT_MS)).toBe("idle_expired");
  });

  it("expires after 8 hours regardless of activity", () => {
    expect(ABSOLUTE_LIFETIME_MS).toBe(8 * 3600 * 1000);
    const now = t0 + ABSOLUTE_LIFETIME_MS;
    expect(sessionState({ createdAt: t0, lastSeenAt: now - 1000 }, now)).toBe("absolute_expired");
  });

  it("refreshes access tokens shortly before they expire", () => {
    expect(needsRefresh(t0 + 120_000, t0)).toBe(false);
    expect(needsRefresh(t0 + 30_000, t0)).toBe(true);
    expect(needsRefresh(t0 - 1, t0)).toBe(true);
  });
});
