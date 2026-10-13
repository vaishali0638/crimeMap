"use client";

import { useQuery } from "@tanstack/react-query";
import Link from "next/link";
import { AI_LABEL, type InsightSummary } from "@/lib/ai";
import { getJson } from "@/lib/map/client";
import { t } from "@/lib/i18n";
import type { MessageKey } from "@/messages/en";

type Listing = { data: InsightSummary[]; ai_enabled: boolean };

function List({ region, type }: { region: string; type: "EMERGING_PATTERN" | "CLUSTER" }) {
  const result = useQuery({
    queryKey: ["ai-insights", region, type],
    queryFn: ({ signal }) => getJson<Listing>(`ai/insights?state=PENDING&region=${encodeURIComponent(region)}&type=${type}&limit=50`, signal),
    staleTime: 60_000,
  });
  const titleId = `ai-${type}`;
  return (
    <section aria-labelledby={titleId} className="flex flex-col gap-2">
      <h3 id={titleId} className="text-lg font-semibold text-navy">{t(`ai.type.${type}` as MessageKey)}</h3>
      {result.isPending ? <p role="status">{t("dash.loading")}</p> : null}
      {result.isError ? <p role="alert">{t("wf.unavailable")}</p> : null}
      {result.data && !result.data.ai_enabled ? <p className="font-semibold">{t("ai.queue.off")}</p> : null}
      {result.data && result.data.data.length === 0 ? <p className="text-sm">{t("ai.analysis.none")}</p> : null}
      {result.data?.data.length ? (
        <ul className="flex flex-col gap-2">
          {result.data.data.map((i) => (
            <li key={i.id} className="rounded-panel border border-divider p-3 text-sm">
              <p>{i.explanation}</p>
              <p className="mt-1 text-xs">
                {t("ai.card.confidence")}: {t(`wf.priority.${i.confidence_label}` as MessageKey)} · {i.model} ·{" "}
                <Link href={`/review/insights/${encodeURIComponent(i.id)}`}>{t("ai.queue.open")}</Link>
              </p>
            </li>
          ))}
        </ul>
      ) : null}
    </section>
  );
}

/** Analytics Workbench "AI Analysis": pending place-time insights for the region (no forecasts). */
export function AiAnalysis({ region, canRegistry }: { region: string; canRegistry: boolean }) {
  return (
    <section aria-labelledby="ai-analysis-title" className="flex flex-col gap-4 rounded-panel border border-divider p-4">
      <h2 id="ai-analysis-title" className="text-xl font-semibold text-navy">{t("ai.analysis.title")}</h2>
      <p className="w-fit rounded-control border-2 border-saffron-strong px-2 py-1 text-sm font-semibold">{AI_LABEL}</p>
      <p className="text-sm">{t("ai.analysis.intro")}</p>
      <p className="text-xs">{t("ai.analysis.filtersNote", { region })}</p>
      <List region={region} type="EMERGING_PATTERN" />
      <List region={region} type="CLUSTER" />
      <p className="text-sm">
        <Link href="/review?tab=ai">{t("ai.models.toQueue")}</Link>
        {canRegistry ? <> · <Link href="/ai">{t("ai.models.title")}</Link></> : null}
      </p>
    </section>
  );
}
