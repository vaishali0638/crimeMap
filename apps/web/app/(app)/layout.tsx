import type { ReactNode } from "react";
import { type QueueBadge, Shell } from "@/components/Shell";
import { apiFetch, type Freshness } from "@/lib/api";
import { requireUser } from "@/lib/current-user";

export const dynamic = "force-dynamic";

async function loadFreshness(session: Parameters<typeof apiFetch>[0]): Promise<Freshness | "unavailable"> {
  try {
    const response = await apiFetch(session, "/api/v1/data-freshness");
    return response.ok ? ((await response.json()) as Freshness) : "unavailable";
  } catch {
    return "unavailable";
  }
}

// Counts only (no record content); the badge is omitted if the API cannot answer.
async function loadQueue(session: Parameters<typeof apiFetch>[0]): Promise<QueueBadge> {
  try {
    const response = await apiFetch(session, "/api/v1/review-queue/summary");
    if (!response.ok) return null;
    const body = (await response.json()) as Record<"data_quality" | "operational_records" | "pattern_reviews" | "actions" | "outcomes" | "ai_insights", number | null> & { more: boolean };
    const counts = [body.data_quality, body.operational_records, body.pattern_reviews, body.actions, body.outcomes, body.ai_insights];
    if (counts.every((c) => c === null || c === undefined)) return null;
    return { count: counts.reduce<number>((sum, c) => sum + (c ?? 0), 0), more: body.more };
  } catch {
    return null;
  }
}

export default async function AppLayout({ children }: { children: ReactNode }) {
  const { me, session } = await requireUser();
  const [freshness, queue] = await Promise.all([loadFreshness(session), loadQueue(session)]);
  return (
    <Shell me={me} csrfToken={session.csrfToken} freshness={freshness} queue={queue}>
      {children}
    </Shell>
  );
}
