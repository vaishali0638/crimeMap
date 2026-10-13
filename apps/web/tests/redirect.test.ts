import { describe, expect, it } from "vitest";
import { safeReturnTo } from "../lib/redirect";

describe("safeReturnTo prevents open redirects", () => {
  it.each(["/", "/dashboard", "/analytics/overview?region=PDY", "/review#queue"])(
    "keeps same-site path %s",
    (path) => expect(safeReturnTo(path)).toBe(path),
  );

  it.each([
    null,
    "",
    "https://evil.example/",
    "//evil.example/path",
    "/\\evil.example",
    "\\\\evil.example",
    "javascript:alert(1)",
    "dashboard",
    "/%2F%2Fevil.example",
    "/bff/auth/login",
    "/x\r\nSet-Cookie: a=b",
    "/" + "a".repeat(600),
  ])("rejects %j", (value) => expect(safeReturnTo(value)).toBe("/"));
});
