import { createHmac } from "node:crypto";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

const KEYS =
  process.env.CRIMEMAP_E2E_TOTP_KEYS ?? fileURLToPath(new URL("../../../secrets/demo-totp-keys.json", import.meta.url));

function base32(secret: string): Buffer {
  const alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567";
  let bits = "";
  for (const ch of secret.replace(/[\s=]/g, "").toUpperCase()) {
    const v = alphabet.indexOf(ch);
    if (v < 0) throw new Error("invalid base32 key");
    bits += v.toString(2).padStart(5, "0");
  }
  const bytes = [];
  for (let i = 0; i + 8 <= bits.length; i += 8) bytes.push(parseInt(bits.slice(i, i + 8), 2));
  return Buffer.from(bytes);
}

/** RFC 6238 (SHA-1, 6 digits, 30 s), the demo realm's OTP policy. */
export function totp(secret: string, at = Date.now()): string {
  const counter = Buffer.alloc(8);
  counter.writeBigUInt64BE(BigInt(Math.floor(at / 1000 / 30)));
  const digest = createHmac("sha1", base32(secret)).update(counter).digest();
  const offset = digest.readUInt8(digest.length - 1) & 0x0f;
  const code = (digest.readUInt32BE(offset) & 0x7fffffff) % 1_000_000;
  return code.toString().padStart(6, "0");
}

export function keys(): Record<string, string> {
  try {
    return JSON.parse(readFileSync(KEYS, "utf8")) as Record<string, string>;
  } catch {
    return {};
  }
}

export const KEYS_FILE = KEYS;
