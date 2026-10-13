import { describe, expect, it } from "vitest";
import { toApiPath } from "../lib/proxy-path";

describe("toApiPath maps BFF paths onto /api/v1 only", () => {
  it("maps simple segments", () => {
    expect(toApiPath(["auth", "me"])).toBe("/api/v1/auth/me");
    expect(toApiPath(["users", "3f0c1c9e-8a7b-4c39-9d5e-2a1b3c4d5e6f", "role-grants"])).toBe(
      "/api/v1/users/3f0c1c9e-8a7b-4c39-9d5e-2a1b3c4d5e6f/role-grants",
    );
  });

  it.each([
    [[]],
    [[".."]],
    [["users", "..", "..", "admin"]],
    [["users%2F.."]],
    [["a/b"]],
    [["a\\b"]],
    [["%2e%2e"]],
    [["health?x=1"]],
    [["auth", "login-events"]],
  ])("rejects %j", (segments) => expect(toApiPath(segments as string[])).toBeNull());
});
