import "server-only";
import type { components } from "@crimemap/types";
import { serverEnv } from "./env";
import type { Session } from "./session";

/** Server-side call to the CRIMEMAP API with the session's access token. */
export async function apiFetch(
  session: Session,
  path: string,
  init: { method?: string; body?: string; requestId?: string; signal?: AbortSignal } = {},
): Promise<Response> {
  const headers: Record<string, string> = {
    Authorization: `Bearer ${session.accessToken}`,
    Accept: "application/json",
    "X-Request-ID": init.requestId ?? crypto.randomUUID(),
  };
  if (init.body !== undefined) headers["Content-Type"] = "application/json";
  return fetch(new URL(path, serverEnv().CRIMEMAP_API_INTERNAL_URL), {
    method: init.method ?? "GET",
    headers,
    body: init.body,
    cache: "no-store",
    redirect: "error",
    signal: init.signal ?? AbortSignal.timeout(10_000),
  });
}

export type Me = components["schemas"]["MeOut"];
export type IssuePage = components["schemas"]["IssuePage"];
export type Issue = components["schemas"]["IssueOut"];
export type DqSummary = components["schemas"]["SummaryOut"];
export type SourceList = components["schemas"]["SourceList"];
export type ReconciliationList = components["schemas"]["ReconciliationList"];
export type Freshness = components["schemas"]["FreshnessOut"];

/** GET a JSON resource; returns null for 403/404 so pages can show an honest "not available". */
export async function apiGetJson<T>(session: Session, path: string): Promise<T | null> {
  const response = await apiFetch(session, path);
  if (response.status === 403 || response.status === 404) return null;
  if (!response.ok) throw new Error(`API request failed (${response.status})`);
  return (await response.json()) as T;
}
