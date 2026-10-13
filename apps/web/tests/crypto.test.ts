import { randomBytes } from "node:crypto";
import { describe, expect, it } from "vitest";
import { hashId, randomId, seal, unseal } from "../lib/crypto";

const key = randomBytes(32);

describe("seal / unseal (AES-256-GCM)", () => {
  it("round-trips structured data", () => {
    const data = { sub: "abc", tokens: { access: "x" }, n: 3 };
    expect(unseal(seal(data, key), key)).toEqual(data);
  });

  it("produces different ciphertext each time (random IV)", () => {
    expect(seal({ a: 1 }, key)).not.toEqual(seal({ a: 1 }, key));
  });

  it("rejects tampered ciphertext", () => {
    const sealed = seal({ role: "viewer" }, key);
    const [iv, body, tag] = sealed.split(".") as [string, string, string];
    const flipped = body.slice(0, -2) + (body.endsWith("A") ? "BB" : "AA");
    expect(unseal(`${iv}.${flipped}.${tag}`, key)).toBeNull();
  });

  it("rejects data sealed with another key", () => {
    expect(unseal(seal({ a: 1 }, randomBytes(32)), key)).toBeNull();
  });

  it("rejects a truncated authentication tag", () => {
    const [iv, body, tag] = seal({ a: 1 }, key).split(".") as [string, string, string];
    const short = Buffer.from(tag, "base64url").subarray(0, 4).toString("base64url");
    expect(unseal(`${iv}.${body}.${short}`, key)).toBeNull();
  });

  it("rejects malformed input instead of throwing", () => {
    for (const bad of ["", "abc", "a.b", "a.b.c.d", "...."]) {
      expect(unseal(bad, key)).toBeNull();
    }
  });

  it("refuses keys that are not 32 bytes", () => {
    expect(() => seal({}, randomBytes(16))).toThrow(/32-byte/);
  });
});

describe("identifiers", () => {
  it("randomId has 256 bits of entropy in url-safe form", () => {
    const id = randomId();
    expect(id).toMatch(/^[A-Za-z0-9_-]{43}$/);
    expect(randomId()).not.toEqual(id);
  });

  it("hashId is a stable one-way hex digest", () => {
    expect(hashId("x")).toEqual(hashId("x"));
    expect(hashId("x")).toMatch(/^[0-9a-f]{64}$/);
    expect(hashId("x")).not.toContain("x".repeat(2));
  });
});
