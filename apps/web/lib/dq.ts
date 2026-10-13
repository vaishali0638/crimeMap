import { t } from "@/lib/i18n";
import type { MessageKey } from "@/messages/en";

/**
 * Data-quality queue helpers (pure, unit-tested). The API is the authority on what is allowed;
 * these only decide which controls to offer and which query parameters to forward.
 */

export const STATES = ["OPEN", "ASSIGNED", "RESOLVED", "ACCEPTED_AS_IS"] as const;
export const SEVERITIES = ["HIGH", "MEDIUM", "LOW"] as const;
export const ISSUE_TYPES = [
  "MISSING_LOCATION",
  "LOW_CONFIDENCE_LOCATION",
  "OUTSIDE_JURISDICTION",
  "MISSING_OCCURRENCE_TIME",
  "INVALID_CATEGORY_MAPPING",
  "POSSIBLE_DUPLICATE",
  "STALE_SOURCE",
  "OTHER",
] as const;
export const REGIONS = ["PDY", "KKL", "MAH", "YAN"] as const;

export function issueLabel(type: string): string {
  return (ISSUE_TYPES as readonly string[]).includes(type) ? t(`issue.${type}` as MessageKey) : type;
}

export function stateLabel(state: string): string {
  return (STATES as readonly string[]).includes(state) ? t(`state.${state}` as MessageKey) : state;
}

export function severityLabel(severity: string): string {
  return (SEVERITIES as readonly string[]).includes(severity) ? t(`severity.${severity}` as MessageKey) : severity;
}

const TRANSITIONS: Record<string, string[]> = {
  OPEN: ["ASSIGNED", "RESOLVED", "ACCEPTED_AS_IS"],
  ASSIGNED: ["OPEN", "RESOLVED", "ACCEPTED_AS_IS"],
  RESOLVED: ["OPEN"],
  ACCEPTED_AS_IS: ["OPEN"],
};

export function actionsFor(state: string): string[] {
  return TRANSITIONS[state] ?? [];
}

type Params = Record<string, string | string[] | undefined>;

function first(value: string | string[] | undefined): string | undefined {
  return Array.isArray(value) ? value[0] : value;
}

function pick<T extends readonly string[]>(value: string | undefined, allowed: T): T[number] | undefined {
  return value !== undefined && (allowed as readonly string[]).includes(value) ? value : undefined;
}

/** Whitelist page search parameters into an API query (unknown keys and values are dropped). */
export function queueQuery(params: Params): URLSearchParams {
  const query = new URLSearchParams();
  const state = first(params.state) ?? "OPEN";
  if (state !== "ALL") {
    const s = pick(state, STATES);
    if (s) query.set("state", s);
  }
  const severity = pick(first(params.severity), SEVERITIES);
  if (severity) query.set("severity", severity);
  const issueType = pick(first(params.issue_type), ISSUE_TYPES);
  if (issueType) query.set("issue_type", issueType);
  const region = pick(first(params.region), REGIONS);
  if (region) query.set("region", region);
  if (first(params.mine) === "1") query.set("mine", "true");
  query.set("limit", "25");
  const cursor = first(params.cursor);
  if (cursor && /^[A-Za-z0-9_-]{1,200}$/.test(cursor)) query.set("cursor", cursor);
  return query;
}

/** The page's own filter parameters, whitelisted (for building pagination links). */
export function filterParams(params: Params): URLSearchParams {
  const query = queueQuery(params);
  query.delete("limit");
  query.delete("cursor");
  if (query.get("mine") === "true") query.set("mine", "1");
  const state = first(params.state);
  if (state === "ALL") query.set("state", "ALL");
  return query;
}

const IST = new Intl.DateTimeFormat("en-GB", {
  timeZone: "Asia/Kolkata",
  day: "numeric",
  month: "short",
  year: "numeric",
  hour: "2-digit",
  minute: "2-digit",
  hourCycle: "h23",
});

export function formatIst(value: string | null | undefined): string {
  if (!value) return "—";
  return `${IST.format(new Date(value))} IST`;
}
