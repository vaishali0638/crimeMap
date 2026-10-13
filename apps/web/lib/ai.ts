import type { components } from "@crimemap/types";
import { t } from "@/lib/i18n";

/** Responsible AI assistance (Phase 7): types and pure helpers (unit-tested). */

export type InsightSummary = components["schemas"]["InsightSummary"];
export type InsightCard = components["schemas"]["InsightCard"];
export type ModelOut = components["schemas"]["ModelOut"];
export type ModelList = components["schemas"]["ModelList"];
export type RunOut = components["schemas"]["RunOut"];
export type BriefingOut = components["schemas"]["BriefingOut"];

export const AI_LABEL = "AI-assisted; analyst review required.";
export const ANALYTIC_TYPES = ["EMERGING_PATTERN", "CLUSTER"] as const;
export const DQ_TYPES = ["DQ_ANOMALY", "DUPLICATE", "GEOCODE"] as const;

/** A JSON value as short plain text (evidence and baseline values are numbers, strings or lists). */
export function plainValue(value: unknown): string {
  if (value === null || value === undefined) return "—";
  if (typeof value === "boolean") return value ? t("ai.yes") : t("ai.no");
  if (typeof value === "number") return Number.isInteger(value) ? value.toLocaleString("en-IN") : String(value);
  if (Array.isArray(value)) return value.map(plainValue).join(", ");
  if (typeof value === "object") return Object.entries(value as Record<string, unknown>).map(([k, v]) => `${humanKey(k)}: ${plainValue(v)}`).join("; ");
  return String(value);
}

/** "without_usable_location" → "Without usable location". */
export function humanKey(key: string): string {
  const words = key.replace(/_/g, " ").trim();
  return words ? words.charAt(0).toUpperCase() + words.slice(1) : key;
}

/** Rows for a key/value table, in the API's order, skipping empty notes. */
export function entries(record: Record<string, unknown> | null | undefined): [string, string][] {
  return Object.entries(record ?? {}).map(([k, v]) => [humanKey(k), plainValue(v)]);
}

/** The analytics link for an insight's place and window (Location Trends, 1 km cells). */
export function analyticsHref(card: Pick<InsightCard, "region" | "station" | "window_start" | "window_end" | "insight_type">): string | null {
  if (!(ANALYTIC_TYPES as readonly string[]).includes(card.insight_type)) return null;
  const q = new URLSearchParams({ section: "location", region: card.region, period_from: card.window_start, period_to: card.window_end, unit: "HEX", hex_size_m: "1000" });
  if (card.station) q.set("station", card.station);
  return `/analytics?${q}`;
}

/** Whether the user's roles include place-time AI insights (a data steward's grant covers
 * data-quality insights only; the API decides, this only avoids empty or misleading views). */
export function placeTimeAi(me: { permissions: string[]; roles: { role: string }[] }): boolean {
  return me.permissions.includes("ai.insight.read") && me.roles.some((r) => r.role !== "DATA_STEWARD");
}
