import "server-only";
import { createHmac, createHash, randomBytes } from "node:crypto";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { apiFetch } from "./api";
import { callbackUrl, client, oidcConfig } from "./oidc";
import { ABSOLUTE_LIFETIME_MS } from "./session-policy";
import { createSession } from "./session";

const FALLBACK_TOTP_KEYS: Record<string, string> = {
  "demo.analyst": "NVRW OT3C MVRV MODL MNDW QZ2V I5YE EUTH",
  "demo.steward": "KUYTCM2OKJSWQULEK4YES4DIJJZGYMRT",
  "demo.supervisor": "I5EXURCKKFHEK5KMGNUFGTJUKZZGM3BR",
  "demo.auditor": "NNLG2TDYMV4TM4KFNI4DO23KG5CTMOLP",
  "demo.admin": "HBGFOMSJGVJHU43OJB4EYRDTNRDDGRKG",
  "demo.officer": "HA3HAMDDJRXWIRSKMRHEO42QKZGGMN3J",
  "demo.sho": "JY3HG3LGJVTXO53GIRUHU4ZSJMZXO23Q",
};

export const USER_ALIASES: Record<string, string> = {
  admin: "demo.admin",
  analyst: "demo.analyst",
  supervisor: "demo.supervisor",
  officer: "demo.officer",
  sho: "demo.sho",
  steward: "demo.steward",
  auditor: "demo.auditor",
  leadership: "demo.leadership",
  viewer: "demo.viewer",
};

function base32(secret: string): Buffer {
  const alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567";
  let bits = "";
  for (const ch of secret.replace(/[\s=]/g, "").toUpperCase()) {
    const v = alphabet.indexOf(ch);
    if (v < 0) throw new Error("invalid base32 key");
    bits += v.toString(2).padStart(5, "0");
  }
  const bytes: number[] = [];
  for (let i = 0; i + 8 <= bits.length; i += 8) bytes.push(parseInt(bits.slice(i, i + 8), 2));
  return Buffer.from(bytes);
}

function calculateTotp(secret: string, at = Date.now()): string {
  const counter = Buffer.alloc(8);
  counter.writeBigUInt64BE(BigInt(Math.floor(at / 1000 / 30)));
  const digest = createHmac("sha1", base32(secret)).update(counter).digest();
  const offset = digest.readUInt8(digest.length - 1) & 0x0f;
  const code = (digest.readUInt32BE(offset) & 0x7fffffff) % 1_000_000;
  return code.toString().padStart(6, "0");
}

function base64url(buf: Buffer): string {
  return buf.toString("base64").replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

function getTotpKey(username: string): string {
  try {
    const p = fileURLToPath(new URL("../../../secrets/demo-totp-keys.json", import.meta.url));
    const all = JSON.parse(readFileSync(p, "utf8")) as Record<string, string>;
    if (all[username]) return all[username];
  } catch {
    // ignore, fall back to embedded keys
  }
  return FALLBACK_TOTP_KEYS[username] ?? FALLBACK_TOTP_KEYS["demo.analyst"] ?? "";
}

export type DemoLoginResult = {
  sessionId: string;
  csrfToken: string;
  maxAge: number;
};

/** Programmatically sign in a demo user through Keycloak with automatic TOTP generation. */
export async function authenticateDemoUser(requestedUser: string): Promise<DemoLoginResult> {
  const normalized = requestedUser.trim().toLowerCase();
  const username = USER_ALIASES[normalized] ?? (normalized.startsWith("demo.") ? normalized : `demo.${normalized}`);
  
  const config = await oidcConfig();
  
  const verifier = base64url(randomBytes(32));
  const challenge = base64url(createHash("sha256").update(verifier).digest());
  const state = client.randomState();
  const nonce = client.randomNonce();
  
  const cb = callbackUrl();
  const authUrl = client.buildAuthorizationUrl(config, {
    redirect_uri: cb,
    scope: "openid profile email",
    code_challenge: challenge,
    code_challenge_method: "S256",
    state,
    nonce,
    prompt: "login",
    max_age: "0",
  });
  
  // Step 1: GET authorization URL
  let resp = await fetch(authUrl, { redirect: "manual" });
  let cookies: string[] = [];
  if (resp.headers.getSetCookie) {
    cookies = resp.headers.getSetCookie();
  }
  let cookieHeader = cookies.map((c) => c.split(";")[0]).join("; ");
  
  let html = await resp.text();
  let actionMatch = html.match(/action="([^"]+)"/);
  if (!actionMatch || !actionMatch[1]) {
    throw new Error("Could not find login form in identity provider response");
  }
  const loginActionUrl = actionMatch[1].replace(/&amp;/g, "&");
  
  // Step 2: POST credentials
  const demoPassword = process.env.CRIMEMAP_DEMO_PASSWORD || "OZ9BJrhvl2V9cIWsf6xC";
  const loginFormData = new URLSearchParams({
    username,
    password: demoPassword,
    credentialId: "",
  });
  
  resp = await fetch(loginActionUrl, {
    method: "POST",
    headers: {
      "Content-Type": "application/x-www-form-urlencoded",
      Cookie: cookieHeader,
    },
    body: loginFormData.toString(),
    redirect: "manual",
  });
  
  if (resp.headers.getSetCookie) {
    for (const c of resp.headers.getSetCookie()) cookies.push(c);
  }
  cookieHeader = cookies.map((c) => c.split(";")[0]).join("; ");
  
  let redirectLocation = resp.headers.get("location");
  
  // Step 3: Handle OTP if prompted
  if (!redirectLocation) {
    html = await resp.text();
    actionMatch = html.match(/action="([^"]+)"/);
    if (!actionMatch || !actionMatch[1]) {
      throw new Error("Could not find OTP form in identity provider response");
    }
    const otpActionUrl = actionMatch[1].replace(/&amp;/g, "&");
    const totpCode = calculateTotp(getTotpKey(username));
    
    resp = await fetch(otpActionUrl, {
      method: "POST",
      headers: {
        "Content-Type": "application/x-www-form-urlencoded",
        Cookie: cookieHeader,
      },
      body: new URLSearchParams({ otp: totpCode }).toString(),
      redirect: "manual",
    });
    
    redirectLocation = resp.headers.get("location");
  }
  
  if (!redirectLocation) {
    throw new Error("Identity provider did not redirect after authentication");
  }
  
  const returnUrl = new URL(redirectLocation);
  const code = returnUrl.searchParams.get("code");
  if (!code) {
    const error = returnUrl.searchParams.get("error_description") ?? returnUrl.searchParams.get("error") ?? "unknown";
    throw new Error(`Authentication refused: ${error}`);
  }
  
  // Step 4: Complete OIDC grant
  const tokens = await client.authorizationCodeGrant(config, returnUrl, {
    pkceCodeVerifier: verifier,
    expectedState: state,
    expectedNonce: nonce,
    idTokenExpected: true,
  });
  
  const claims = tokens.claims();
  if (!claims?.sub) {
    throw new Error("Identity provider returned no subject");
  }
  
  // Step 5: Create session
  const { sessionId, session } = await createSession({
    sub: claims.sub,
    accessToken: tokens.access_token,
    refreshToken: tokens.refresh_token ?? null,
    idToken: tokens.id_token ?? null,
    accessExpiresAt: Date.now() + (tokens.expiresIn() ?? 60) * 1000,
    authTime: typeof claims.auth_time === "number" ? claims.auth_time * 1000 : Date.now(),
  });
  
  // Audit log write
  try {
    await apiFetch(session, "/api/v1/auth/login-events", { method: "POST" });
  } catch {
    // continue in demo
  }
  
  return {
    sessionId,
    csrfToken: session.csrfToken,
    maxAge: Math.floor(ABSOLUTE_LIFETIME_MS / 1000),
  };
}
