import Link from "next/link";
import { BriefingBuilder } from "@/components/workflow/Forms";
import { Notice, day } from "@/components/workflow/View";
import { AI_LABEL, type InsightSummary } from "@/lib/ai";
import { apiFetch } from "@/lib/api";
import { requireUser } from "@/lib/current-user";
import { t } from "@/lib/i18n";
import type { MessageKey } from "@/messages/en";

export const dynamic = "force-dynamic";

/** Template briefing from validated insights (cited; a draft the analyst owns). */
export default async function BriefingPage() {
  const { me, session } = await requireUser();
  if (!me.permissions.includes("ai.insight.review")) return <Notice title={t("ai.briefing.title")} text={t("ai.briefing.noAccess")} />;
  let response: Response;
  try {
    response = await apiFetch(session, "/api/v1/ai/insights?state=VALIDATED&limit=200");
  } catch {
    return <Notice title={t("ai.briefing.title")} text={t("wf.unavailable")} />;
  }
  if (!response.ok) return <Notice title={t("ai.briefing.title")} text={t("wf.unavailable")} />;
  const items = ((await response.json()) as { data: InsightSummary[] }).data;
  return (
    <div className="flex max-w-4xl flex-col gap-4">
      {me.permissions.includes("ai.model.read") ? <p><Link href="/ai">{t("ai.models.title")}</Link></p> : null}
      <p className="w-fit rounded-control border-2 border-saffron-strong px-2 py-1 text-sm font-semibold">{AI_LABEL}</p>
      <h1 id="page-title" className="text-2xl font-semibold text-navy">{t("ai.briefing.title")}</h1>
      <BriefingBuilder
        insights={items.map((i) => ({
          id: i.id,
          text: `${t(`ai.type.${i.insight_type}` as MessageKey)} · ${i.region}${i.station ? ` · ${i.station}` : ""} · ${day(i.window_start)} – ${day(i.window_end)}: ${i.explanation}`,
        }))}
        csrfToken={session.csrfToken}
      />
    </div>
  );
}
