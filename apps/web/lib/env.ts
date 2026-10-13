import "server-only";
import { z } from "zod";

/**
 * Server-side configuration, validated once. Nothing here is exposed to the browser bundle
 * (no NEXT_PUBLIC_ variables are used for secrets).
 */
const schema = z
  .object({
    CRIMEMAP_ENVIRONMENT: z.enum(["DEMO", "SANDBOX", "PRODUCTION"]).default("DEMO"),
    CRIMEMAP_WEB_BASE_URL: z.url().default("http://localhost:3100"),
    CRIMEMAP_API_INTERNAL_URL: z.url().default("http://localhost:8000"),
    CRIMEMAP_OIDC_ISSUER: z.url().default("http://localhost:8080/realms/crimemap-demo"),
    CRIMEMAP_OIDC_CLIENT_ID: z.string().min(1).default("crimemap-web"),
    CRIMEMAP_KC_WEB_CLIENT_SECRET: z.string().min(16),
    CRIMEMAP_SESSION_KEY: z.string().min(40),
    CRIMEMAP_REDIS_URL: z.string().min(1),
  })
  .superRefine((env, ctx) => {
    const insecure = env.CRIMEMAP_OIDC_ISSUER.startsWith("http://") || env.CRIMEMAP_WEB_BASE_URL.startsWith("http://");
    if (env.CRIMEMAP_ENVIRONMENT === "PRODUCTION" && insecure) {
      ctx.addIssue({ code: "custom", message: "PRODUCTION requires https for the web app and identity provider" });
    }
  });

export type ServerEnv = z.infer<typeof schema> & { sessionKey: Buffer; secureCookies: boolean };

let cached: ServerEnv | undefined;

import { existsSync, readFileSync } from "node:fs";
import { resolve } from "node:path";

function loadFallbackEnv(): void {
  try {
    for (const p of [resolve(process.cwd(), ".env"), resolve(process.cwd(), "../../.env")]) {
      if (existsSync(p)) {
        for (const line of readFileSync(p, "utf8").split("\n")) {
          const trimmed = line.trim();
          if (!trimmed || trimmed.startsWith("#")) continue;
          const eq = trimmed.indexOf("=");
          if (eq > 0) {
            const k = trimmed.slice(0, eq).trim();
            const v = trimmed.slice(eq + 1).trim().replace(/^['"]|['"]$/g, "");
            if (!(k in process.env)) process.env[k] = v;
          }
        }
        break;
      }
    }
  } catch {
    // fallback
  }
}

export function serverEnv(): ServerEnv {
  if (cached) return cached;
  loadFallbackEnv();
  const parsed = schema.parse(process.env);
  const sessionKey = Buffer.from(parsed.CRIMEMAP_SESSION_KEY, "base64");
  if (sessionKey.length !== 32) throw new Error("CRIMEMAP_SESSION_KEY must be 32 bytes, base64-encoded");
  cached = { ...parsed, sessionKey, secureCookies: parsed.CRIMEMAP_WEB_BASE_URL.startsWith("https://") };
  return cached;
}
