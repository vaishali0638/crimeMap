import "server-only";
import { createClient, type RedisClientType } from "redis";
import { hashId, randomId, seal, unseal } from "./crypto";
import { serverEnv } from "./env";
import { ABSOLUTE_LIFETIME_MS, IDLE_TIMEOUT_MS, sessionState } from "./session-policy";

/**
 * Server-side sessions (ADR-003). The browser holds only a random session id in an HttpOnly
 * cookie. Redis stores the session under a hash of that id, sealed with AES-256-GCM, so neither a
 * Redis dump nor a log line reveals usable tokens or cookie values.
 */
export type Session = {
  sub: string;
  accessToken: string;
  refreshToken: string | null;
  idToken: string | null;
  accessExpiresAt: number;
  authTime: number | null;
  csrfToken: string;
  createdAt: number;
  lastSeenAt: number;
};

export const SESSION_COOKIE = "crimemap_session";
export const CSRF_COOKIE = "crimemap_csrf";
export const FLOW_COOKIE = "crimemap_oidc_flow";

let redisClient: RedisClientType | undefined;
let connecting: Promise<unknown> | undefined;

async function redis(): Promise<RedisClientType> {
  if (!redisClient) {
    redisClient = createClient({ url: serverEnv().CRIMEMAP_REDIS_URL });
    redisClient.on("error", (error: Error) => console.error("session_store_error", error.name));
  }
  if (!redisClient.isOpen) {
    connecting ??= redisClient.connect().finally(() => (connecting = undefined));
    await connecting;
  }
  return redisClient;
}

const key = (sessionId: string) => `crimemap:sess:${hashId(sessionId)}`;
const ttlSeconds = Math.ceil(IDLE_TIMEOUT_MS / 1000);

export async function createSession(
  data: Omit<Session, "csrfToken" | "createdAt" | "lastSeenAt">,
): Promise<{ sessionId: string; session: Session }> {
  const now = Date.now();
  const session: Session = { ...data, csrfToken: randomId(), createdAt: now, lastSeenAt: now };
  const sessionId = randomId();
  await (await redis()).set(key(sessionId), seal(session, serverEnv().sessionKey), { EX: ttlSeconds });
  return { sessionId, session };
}

/** Load a session, enforcing idle and absolute lifetimes; touches lastSeenAt. */
export async function loadSession(sessionId: string | undefined): Promise<Session | null> {
  if (!sessionId || sessionId.length > 64) return null;
  const store = await redis();
  const raw = await store.get(key(sessionId));
  if (!raw) return null;
  const session = unseal<Session>(raw, serverEnv().sessionKey);
  const now = Date.now();
  if (!session || sessionState(session, now) !== "active") {
    await store.del(key(sessionId));
    return null;
  }
  const touched = { ...session, lastSeenAt: now };
  const remainingAbsolute = Math.ceil((session.createdAt + ABSOLUTE_LIFETIME_MS - now) / 1000);
  await store.set(key(sessionId), seal(touched, serverEnv().sessionKey), {
    EX: Math.max(1, Math.min(ttlSeconds, remainingAbsolute)),
  });
  return touched;
}

export async function saveSession(sessionId: string, session: Session): Promise<void> {
  await (await redis()).set(key(sessionId), seal(session, serverEnv().sessionKey), { EX: ttlSeconds });
}

export async function destroySession(sessionId: string | undefined): Promise<void> {
  if (sessionId) await (await redis()).del(key(sessionId));
}

/** Short per-session lock so concurrent requests never present a rotated refresh token twice. */
export async function withSessionLock<T>(sessionId: string, fn: () => Promise<T>): Promise<T | null> {
  const store = await redis();
  const lockKey = `crimemap:sess-lock:${hashId(sessionId)}`;
  for (let attempt = 0; attempt < 25; attempt++) {
    const acquired = await store.set(lockKey, "1", { NX: true, PX: 10_000 });
    if (acquired) {
      try {
        return await fn();
      } finally {
        await store.del(lockKey);
      }
    }
    await new Promise((resolve) => setTimeout(resolve, 200));
  }
  return null;
}

export function cookieOptions(maxAgeSeconds: number, httpOnly = true) {
  return {
    httpOnly,
    secure: serverEnv().secureCookies,
    sameSite: "strict" as const,
    path: "/",
    maxAge: maxAgeSeconds,
  };
}
