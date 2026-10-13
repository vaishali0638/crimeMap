import type { components } from "@crimemap/types";

/** Review Queue workflow (Phase 6): shared types and pure helpers (unit-tested). */

export type QueueItem = components["schemas"]["QueueItem"];
export type PatternReview = components["schemas"]["ReviewDetail"];
export type Action = components["schemas"]["ActionOut"];
export type Outcome = components["schemas"]["OutcomeOut"];
export type HistoryItem = components["schemas"]["HistoryItem"];

export type TabKey = "dq" | "duplicates" | "ops" | "patterns" | "actions" | "outcomes" | "ai";
export type WorkflowQueue = "patterns" | "actions" | "outcomes";

type Tab = { key: TabKey; permissions: readonly string[]; disabled?: boolean };

/** Tab order follows the spec's queue list; each tab is shown to roles that can work it. */
export const TABS: readonly Tab[] = [
  { key: "dq", permissions: ["dq.manage"] },
  { key: "duplicates", permissions: ["dq.manage"] },
  { key: "ops", permissions: ["opsrecord.approve"] },
  { key: "patterns", permissions: ["pattern_review.create", "pattern_review.decide"] },
  { key: "actions", permissions: ["action.approve", "action.complete"] },
  { key: "outcomes", permissions: ["outcome.review"] },
  { key: "ai", permissions: ["ai.insight.read"] },
];

export function visibleTabs(permissions: readonly string[]): Tab[] {
  return TABS.filter((tab) => tab.permissions.some((p) => permissions.includes(p)));
}

/** The requested tab if the caller may see it, else their first enabled tab (null: none). */
export function chooseTab(requested: string | undefined, permissions: readonly string[]): TabKey | null {
  const tabs = visibleTabs(permissions);
  const match = tabs.find((tab) => tab.key === requested);
  if (match) return match.key;
  return tabs.find((tab) => !tab.disabled)?.key ?? null;
}

export function isWorkflowQueue(tab: TabKey): tab is WorkflowQueue {
  return tab === "patterns" || tab === "actions" || tab === "outcomes";
}

/** Detail page for a queue item (outcome items are the completed action). */
export function itemHref(item: Pick<QueueItem, "type" | "id">): string {
  const id = encodeURIComponent(item.id);
  return item.type === "PATTERN_REVIEW" ? `/review/patterns/${id}` : `/review/actions/${id}`;
}

const DAY_MS = 86_400_000;

/** Whole days from `today` (YYYY-MM-DD, IST) to `due`; negative when overdue. */
export function daysUntil(due: string, today: string): number {
  return Math.round((Date.parse(`${due}T00:00:00Z`) - Date.parse(`${today}T00:00:00Z`)) / DAY_MS);
}

export function todayIst(now: Date = new Date()): string {
  return new Intl.DateTimeFormat("en-CA", { timeZone: "Asia/Kolkata", year: "numeric", month: "2-digit", day: "2-digit" }).format(now);
}

/** Outcome periods suggested from the action dates: the action period, and the same length before it. */
export function suggestedPeriods(startAt: string, endAt: string): { baseline_start: string; baseline_end: string; after_start: string; after_end: string } {
  const start = Date.parse(`${startAt}T00:00:00Z`);
  const end = Date.parse(`${endAt}T00:00:00Z`);
  const days = Math.round((end - start) / DAY_MS) + 1;
  const iso = (ms: number) => new Date(ms).toISOString().slice(0, 10);
  return {
    baseline_start: iso(start - days * DAY_MS),
    baseline_end: iso(start - DAY_MS),
    after_start: startAt,
    after_end: endAt,
  };
}

type Periods = { baseline_start: string; baseline_end: string; after_start: string; after_end: string };
const ISO = /^\d{4}-\d{2}-\d{2}$/;
const days = (from: string, to: string) => Math.round((Date.parse(`${to}T00:00:00Z`) - Date.parse(`${from}T00:00:00Z`)) / DAY_MS);

/**
 * Client-side mirror of the API's outcome period rules (the API decides): the baseline ends before
 * the action starts, the after period starts with the action or later and ends today or earlier,
 * each at most 366 days. Returns the message key of the first problem, or null.
 */
export function periodsError(p: Periods, actionStart: string, today: string = todayIst()): "wf.period.format" | "wf.period.order" | "wf.period.baseline" | "wf.period.after" | "wf.period.future" | "wf.period.length" | null {
  const values = [p.baseline_start, p.baseline_end, p.after_start, p.after_end];
  if (!values.every((d) => ISO.test(d))) return "wf.period.format";
  if (!(p.baseline_start <= p.baseline_end && p.baseline_end < p.after_start && p.after_start <= p.after_end)) return "wf.period.order";
  if (p.baseline_end >= actionStart) return "wf.period.baseline";
  if (p.after_start < actionStart) return "wf.period.after";
  if (p.after_end > today) return "wf.period.future";
  if (days(p.baseline_start, p.baseline_end) > 366 || days(p.after_start, p.after_end) > 366) return "wf.period.length";
  return null;
}

/** Action dates: both given, end not before start, at most 366 days (the API's rule). */
export function actionDatesError(start: string, end: string): "wf.hint.dates" | "wf.hint.datesLength" | null {
  if (!ISO.test(start) || !ISO.test(end) || end < start) return "wf.hint.dates";
  if (days(start, end) > 366) return "wf.hint.datesLength";
  return null;
}

/**
 * Plain-text explanation from an API problem: field messages for validation errors (`errors[]`,
 * without pydantic's "Value error, " prefix), else `detail`. Capped; never HTML.
 */
export function problemExplanation(problem: unknown): string | null {
  if (!problem || typeof problem !== "object") return null;
  const p = problem as { detail?: unknown; errors?: unknown };
  if (Array.isArray(p.errors)) {
    const messages = p.errors
      .map((e) => (e && typeof e === "object" && typeof (e as { message?: unknown }).message === "string" ? (e as { message: string }).message : null))
      .filter((m): m is string => m !== null && m.length > 0)
      .map((m) => m.replace(/^Value error, /, ""));
    if (messages.length) return [...new Set(messages)].join("; ").slice(0, 300);
  }
  return typeof p.detail === "string" ? p.detail.slice(0, 300) : null;
}

/** Outcomes announced after a change (`?done=`); anything else is ignored. */
export const FLASH_KEYS = ["revised", "submitted", "decided", "proposed", "approved", "rejected", "completed", "outcomeStarted", "outcomeSaved", "finalised", "insightReviewed", "noteAdded", "switched"] as const;
export type FlashKey = (typeof FLASH_KEYS)[number];

/** The nonce of the last change (`?n=`), used as the Flash key so each change is announced. */
export function flashNonce(value: string | string[] | undefined): string {
  const v = Array.isArray(value) ? value[0] : value;
  return v && /^[a-z0-9]{1,16}$/.test(v) ? v : "0";
}

export function flashKey(value: string | string[] | undefined): FlashKey | null {
  const v = Array.isArray(value) ? value[0] : value;
  return (FLASH_KEYS as readonly string[]).includes(v ?? "") ? (v as FlashKey) : null;
}

/** Queue filters from the page URL, validated (unknown values dropped). */
export function queueFilters(params: Record<string, string | string[] | undefined>): URLSearchParams {
  const one = (k: string) => (Array.isArray(params[k]) ? params[k][0] : params[k]) as string | undefined;
  const out = new URLSearchParams();
  const priority = one("priority");
  if (priority && ["HIGH", "MEDIUM", "LOW"].includes(priority)) out.set("priority", priority);
  const region = one("region");
  if (region && /^[A-Z]{2,5}$/.test(region)) out.set("region", region);
  if (one("mine") === "1") out.set("mine", "true");
  if (one("overdue") === "1") out.set("overdue", "true");
  return out;
}

const CELL_KEY = /^[A-Z]{2,5}:(250|500|1000|2000):-?\d{1,7}:-?\d{1,7}$/;

/** Parse comma/space/newline-separated cell keys; null if any is malformed or repeated. */
export function parseCellKeys(text: string): string[] | null {
  const keys = text.split(/[\s,]+/).filter(Boolean);
  if (!keys.every((k) => CELL_KEY.test(k)) || new Set(keys).size !== keys.length) return null;
  return keys;
}
