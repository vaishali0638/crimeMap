import Link from "next/link";
import type { components } from "@crimemap/types";
import { Facts, Notice } from "@/components/workflow/View";
import { apiFetch } from "@/lib/api";
import { requireUser } from "@/lib/current-user";
import { formatIst } from "@/lib/dq";
import { t } from "@/lib/i18n";
import type { MessageKey } from "@/messages/en";

export const dynamic = "force-dynamic";

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

type Snapshot = components["schemas"]["SnapshotOut"];

function text(value: unknown): string {
  if (value === null || value === undefined || value === "") return "—";
  if (Array.isArray(value)) return value.map(text).join("\n");
  if (typeof value === "object") return Object.entries(value as Record<string, unknown>).map(([k, v]) => `${k}: ${text(v)}`).join("\n");
  return String(value);
}

/** An evidence snapshot's identity and method (the API decides who may read it; ADR-035). */
export default async function EvidencePage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  const { session } = await requireUser();
  if (!UUID.test(id)) return <Notice title={t("wf.evidence.title")} text={t("wf.error.notFound")} />;
  const response = await apiFetch(session, `/api/v1/evidence-snapshots/${id}`);
  if (!response.ok) {
    return <Notice title={t("wf.evidence.title")} text={response.status === 404 || response.status === 403 ? t("wf.evidence.notVisible") : t("wf.unavailable")} />;
  }
  const snap = (await response.json()) as Snapshot;
  const meta = snap.meta as Record<string, unknown>;
  return (
    <div className="flex max-w-5xl flex-col gap-4">
      <p><Link href="/review">{t("wf.back")}</Link></p>
      <h1 id="page-title" className="text-2xl font-semibold text-navy">{t("wf.evidence.title")}</h1>
      <p className="text-sm">{t("wf.evidence.intro")}</p>
      <Facts
        rows={[
          [t("wf.evidence.analysis"), text(meta.title)],
          [t("wf.evidence.section"), t(`wf.section.${snap.section}` as MessageKey)],
          [t("wf.evidence.filters"), text(meta.filters_applied)],
          [t("wf.evidence.records"), text(meta.record_count)],
          [t("wf.evidence.excluded"), text(meta.excluded_count)],
          [t("wf.evidence.method"), text(meta.method)],
          [t("wf.evidence.freshness"), text(meta.data_freshness)],
          [t("wf.evidence.caveats"), text(meta.caveats)],
          [t("wf.evidence.created"), formatIst(String(snap.created_at))],
          [t("wf.evidence.id"), <span key="i" className="font-mono text-xs">{snap.id}</span>],
          [t("wf.evidence.hash"), <span key="h" className="break-all font-mono text-xs">{snap.content_sha256}</span>],
          [t("wf.evidence.classification"), text(meta.classification)],
        ]}
      />
    </div>
  );
}
