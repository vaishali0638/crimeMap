"use client";

import type { components } from "@crimemap/types";

/** Browser → BFF calls for the Home Map. The BFF attaches the token; writes carry the CSRF token. */

export type MapResponse = components["schemas"]["MapOut"];
export type FilterMetadata = components["schemas"]["FilterMetadata"];
export type SearchResponse = components["schemas"]["SearchOut"];
export type IncidentSummary = components["schemas"]["IncidentSummaryOut"];
export type IncidentDetails = components["schemas"]["DetailOut"];
export type NoteList = components["schemas"]["NoteList"];
export type OperationalRecord = components["schemas"]["RecordOut"];

/** A problem response (RFC 7807). `title`/`detail` are the API's plain-text explanation, safe to show. */
export class ApiError extends Error {
  constructor(
    readonly status: number,
    readonly slug: string | null,
    readonly title: string | null = null,
    readonly detail: string | null = null,
  ) {
    super(`API request failed (${status})`);
  }

  /** The API's own explanation, if it gave one. */
  get explanation(): string | null {
    const text = [this.title, this.detail].filter((v): v is string => typeof v === "string" && v.length > 0).join(": ");
    return text ? text.slice(0, 300) : null;
  }
}

async function parse<T>(response: Response): Promise<T> {
  if (!response.ok) {
    const problem = (await response.json().catch(() => null)) as { type?: unknown; title?: unknown; detail?: unknown } | null;
    const str = (v: unknown) => (typeof v === "string" ? v : null);
    throw new ApiError(response.status, str(problem?.type)?.split("/").pop() ?? null, str(problem?.title), str(problem?.detail));
  }
  return (await response.json()) as T;
}

export async function getJson<T>(path: string, signal?: AbortSignal): Promise<T> {
  return parse<T>(await fetch(`/bff/api/${path}`, { headers: { Accept: "application/json" }, signal }));
}

export async function postJson<T>(path: string, body: unknown, csrfToken: string): Promise<T> {
  return parse<T>(
    await fetch(`/bff/api/${path}`, {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-CSRF-Token": csrfToken },
      body: JSON.stringify(body),
    }),
  );
}
