/**
 * Basemap configuration (ADR-033), validated in one place for the CSP (proxy.ts) and the page.
 *
 * - CRIMEMAP_MAP_STYLE_URL: an approved MapLibre style. Absolute https URL; plain http only for a
 *   localhost style outside PRODUCTION.
 * - CRIMEMAP_MAP_ASSET_ORIGINS: optional comma-separated bare https origins the style loads tiles,
 *   glyphs or sprites from (a style usually names other hosts than its own).
 *
 * An invalid value turns the basemap off (no third-party requests) instead of failing every page;
 * the reason is returned so the caller can log it.
 */
export type MapConfig = { styleUrl: string | null; origins: string[]; problem: string | null };

type Env = Record<string, string | undefined>;

const LOCAL = new Set(["localhost", "127.0.0.1", "[::1]"]);

function allowedUrl(value: string, production: boolean): URL | null {
  let url: URL;
  try {
    url = new URL(value);
  } catch {
    return null;
  }
  if (url.username || url.password) return null;
  if (url.protocol === "https:") return url;
  if (url.protocol === "http:" && !production && LOCAL.has(url.hostname)) return url;
  return null;
}

export function mapConfig(env: Env): MapConfig {
  const production = env.CRIMEMAP_ENVIRONMENT === "PRODUCTION";
  const raw = env.CRIMEMAP_MAP_STYLE_URL?.trim();
  if (!raw) return { styleUrl: null, origins: [], problem: null };
  const style = allowedUrl(raw, production);
  if (!style) return { styleUrl: null, origins: [], problem: "CRIMEMAP_MAP_STYLE_URL must be an absolute https URL" };
  const origins = [style.origin];
  for (const item of (env.CRIMEMAP_MAP_ASSET_ORIGINS ?? "").split(",").map((v) => v.trim()).filter(Boolean)) {
    const url = allowedUrl(item, production);
    if (!url || url.origin !== item) {
      return { styleUrl: null, origins: [], problem: "CRIMEMAP_MAP_ASSET_ORIGINS must list bare https origins" };
    }
    if (!origins.includes(url.origin)) origins.push(url.origin);
  }
  return { styleUrl: style.toString(), origins, problem: null };
}
