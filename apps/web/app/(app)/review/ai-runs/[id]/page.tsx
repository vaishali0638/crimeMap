import Link from "next/link";
import { Facts, Notice, day } from "@/components/workflow/View";
import { AI_LABEL, type RunOut, entries } from "@/lib/ai";
import { apiFetch } from "@/lib/api";
import { requireUser } from "@/lib/current-user";
import { formatIst } from "@/lib/dq";
import { t } from "@/lib/i18n";
import type { MessageKey } from "@/messages/en";

export const dynamic = "force-dynamic";

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

/** An insight's input data snapshot: the detector run's parameters, inputs, exclusions and hash. */
export default async function RunPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  const { session } = await requireUser();
  if (!UUID.test(id)) return <Notice title={t("ai.run.title")} text={t("wf.error.notFound")} />;
  let response: Response;
  try {
    response = await apiFetch(session, `/api/v1/ai/runs/${id}`);
  } catch {
    return <Notice title={t("ai.run.title")} text={t("wf.unavailable")} />;
  }
  if (!response.ok) return <Notice title={t("ai.run.title")} text={response.status === 404 || response.status === 403 ? t("wf.error.notFound") : t("wf.unavailable")} />;
  const run = (await response.json()) as RunOut;
  return (
    <div className="flex max-w-4xl flex-col gap-4">
      <p><Link href="/review?tab=ai">{t("wf.back")}</Link></p>
      <p className="w-fit rounded-control border-2 border-saffron-strong px-2 py-1 text-sm font-semibold">{AI_LABEL}</p>
      <h1 id="page-title" className="text-2xl font-semibold text-navy">{t("ai.run.title")}</h1>
      <p className="text-sm">{t("ai.run.intro")}</p>
      <Facts
        rows={[
          [t("ai.card.model"), `${run.model_name} (${run.model})`],
          [t("review.region"), run.region],
          [t("ai.run.asOf"), day(run.as_of)],
          [t("ai.run.status"), t(`ai.runStatus.${run.status}` as MessageKey)],
          [t("ai.run.insights"), String(run.insight_count)],
          [t("ai.run.parameters"), entries(run.parameters).map(([k, v]) => `${k}: ${v}`).join("\n")],
          [t("ai.run.inputs"), entries(run.input_summary).map(([k, v]) => `${k}: ${v}`).join("\n") || "—"],
          [t("ai.run.sha"), <span key="h" className="break-all font-mono text-xs">{run.input_sha256 ?? "—"}</span>],
          [t("ai.run.time"), `${formatIst(run.started_at)} – ${run.finished_at ? formatIst(run.finished_at) : "—"}`],
          [t("ai.run.id"), <span key="i" className="font-mono text-xs">{run.id}</span>],
        ]}
      />
    </div>
  );
}
