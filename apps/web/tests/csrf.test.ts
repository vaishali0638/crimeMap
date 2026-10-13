import { describe, expect, it } from "vitest";
import { checkCsrf } from "../lib/csrf";

const base = {
  appOrigin: "http://localhost:3000",
  origin: "http://localhost:3000",
  sessionToken: "s3cret-token-value",
  submittedToken: "s3cret-token-value",
};

describe("checkCsrf", () => {
  it.each(["GET", "HEAD", "OPTIONS"])("allows safe method %s without a token", (method) => {
    expect(checkCsrf({ ...base, method, submittedToken: null, origin: null }).ok).toBe(true);
  });

  it("allows a state-changing request with matching origin and token", () => {
    expect(checkCsrf({ ...base, method: "POST" }).ok).toBe(true);
  });

  it("rejects a foreign origin", () => {
    expect(checkCsrf({ ...base, method: "POST", origin: "https://evil.example" })).toEqual({
      ok: false,
      reason: "origin_mismatch",
    });
  });

  it("rejects a missing origin on state-changing requests", () => {
    expect(checkCsrf({ ...base, method: "DELETE", origin: null }).reason).toBe("origin_missing");
  });

  it("rejects a missing token", () => {
    expect(checkCsrf({ ...base, method: "POST", submittedToken: null }).reason).toBe("token_missing");
  });

  it("rejects a wrong token, including one of a different length", () => {
    expect(checkCsrf({ ...base, method: "POST", submittedToken: "s3cret-token-valuf" }).reason).toBe(
      "token_mismatch",
    );
    expect(checkCsrf({ ...base, method: "POST", submittedToken: "short" }).reason).toBe("token_mismatch");
  });
});
