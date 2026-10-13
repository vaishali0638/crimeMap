import { createCipheriv, createDecipheriv, createHash, randomBytes } from "node:crypto";

/**
 * Authenticated encryption (AES-256-GCM) for server-side session data and short-lived flow
 * cookies. Format: base64url(iv).base64url(ciphertext).base64url(tag). Any tampering, wrong key,
 * or malformed input yields null — never an exception and never partial data.
 */
const IV_BYTES = 12;
const TAG_BYTES = 16;

function checkKey(key: Buffer): void {
  if (key.length !== 32) throw new Error("seal requires a 32-byte key");
}

export function seal(data: unknown, key: Buffer): string {
  checkKey(key);
  const iv = randomBytes(IV_BYTES);
  const cipher = createCipheriv("aes-256-gcm", key, iv, { authTagLength: TAG_BYTES });
  const body = Buffer.concat([cipher.update(JSON.stringify(data), "utf8"), cipher.final()]);
  return [iv, body, cipher.getAuthTag()].map((b) => b.toString("base64url")).join(".");
}

export function unseal<T>(sealed: string, key: Buffer): T | null {
  checkKey(key);
  const parts = sealed.split(".");
  if (parts.length !== 3 || parts.some((p) => p.length === 0)) return null;
  try {
    const [iv, body, tag] = parts.map((p) => Buffer.from(p, "base64url")) as [Buffer, Buffer, Buffer];
    if (iv.length !== IV_BYTES || tag.length !== TAG_BYTES) return null;
    // authTagLength makes Node itself refuse truncated tags (defence in depth; semgrep gcm-no-tag-length).
    const decipher = createDecipheriv("aes-256-gcm", key, iv, { authTagLength: TAG_BYTES });
    decipher.setAuthTag(tag);
    const plain = Buffer.concat([decipher.update(body), decipher.final()]).toString("utf8");
    return JSON.parse(plain) as T;
  } catch {
    return null;
  }
}

/** 256-bit random identifier, URL-safe. */
export function randomId(bytes = 32): string {
  return randomBytes(bytes).toString("base64url");
}

/** One-way digest used as a storage key so stored keys never equal cookie values. */
export function hashId(id: string): string {
  return createHash("sha256").update(id, "utf8").digest("hex");
}
