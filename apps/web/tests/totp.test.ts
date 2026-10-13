import { describe, expect, it } from "vitest";
import { totp } from "../e2e/totp";

// RFC 6238 appendix B: the published SHA-1 test secret is the ASCII string "12345678901234567890"
// (base32-encoded here at run time); expected codes are the last 6 of the 8-digit values.
function base32(bytes: Buffer): string {
  const alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567";
  const bits = [...bytes].map((b) => b.toString(2).padStart(8, "0")).join("");
  return (bits.match(/.{1,5}/g) ?? []).map((c) => alphabet[parseInt(c.padEnd(5, "0"), 2)]).join("");
}
const SECRET = base32(Buffer.from("1234567890".repeat(2)));

describe("e2e TOTP helper", () => {
  it.each([
    [59, "287082"],
    [1111111109, "081804"],
    [1234567890, "005924"],
    [2000000000, "279037"],
  ])("matches the RFC 6238 vector at %i s", (seconds, code) => {
    expect(totp(SECRET, seconds * 1000)).toBe(code);
  });

  it("refuses a key that is not base32", () => {
    expect(() => totp("not-base32!", 0)).toThrow();
  });
});
