import Link from "next/link";
import { History, Notice } from "@/components/workflow/View";
import { apiFetch } from "@/lib/api";
import { requireUser } from "@/lib/current-user";
import { t } from "@/lib/i18n";
import type { HistoryItem } from "@/lib/workflow";

export const dynamic = "force-dynamic";

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const TYPES = { DQ_ISSUE: "dq", OPERATIONAL_RECORD: "ops" } as const;

type SearchParams = Promise<Record<string, string | string[] | undefined>>;

/** History of a data-quality issue or operational record (from the audit log; the API decides access). */
export default async function ItemHistoryPage({ searchParams }: { searchParams: SearchParams }) {
  const params = await searchParams;
  const type = typeof params.type === "string" && params.type in TYPES ? (params.type as keyof typeof TYPES) : null;
  const id = typeof params.id === "string" && UUID.test(params.id) ? params.id : null;
  const { session } = await requireUser();
  if (!type || !id) return <Notice title={t("wf.history")} text={t("wf.error.notFound")} />;
  const response = await apiFetch(session, `/api/v1/workflow-history?object_type=${type}&object_id=${id}`);
  if (!response.ok) {
    return <Notice title={t("wf.history")} text={response.status === 404 || response.status === 403 ? t("wf.error.notFound") : t("wf.unavailable")} />;
  }
  const items = ((await response.json()) as { data: HistoryItem[] }).data;
  return (
    <div className="flex max-w-3xl flex-col gap-4">
      <p><Link href={`/review?tab=${TYPES[type]}`}>{t("wf.back")}</Link></p>
      <h1 id="page-title" className="text-2xl font-semibold text-navy">{t(type === "DQ_ISSUE" ? "wf.historyTitle.DQ_ISSUE" : "wf.historyTitle.OPERATIONAL_RECORD")}</h1>
      <History items={items} />
    </div>
  );
}
