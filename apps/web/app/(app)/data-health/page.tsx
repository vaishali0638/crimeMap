import { apiGetJson, type DqSummary, type ReconciliationList, type SourceList } from "@/lib/api";
import { requireUser } from "@/lib/current-user";
import { formatIst, issueLabel, severityLabel } from "@/lib/dq";
import { t } from "@/lib/i18n";
import type { MessageKey } from "@/messages/en";

const HEALTH = new Set(["OK", "STALE", "FAILING", "NEVER_RUN", "DISABLED"]);

function healthLabel(status: string | null): string {
  return status && HEALTH.has(status) ? t(`health.status.${status}` as MessageKey) : (status ?? "—");
}

export const dynamic = "force-dynamic";

const th = "p-2";
const td = "p-2";

export default async function DataHealthPage() {
  const { me, session } = await requireUser();
  if (!me.permissions.includes("lineage.read")) {
    return (
      <section aria-labelledby="page-title" className="max-w-3xl">
        <h1 id="page-title" className="text-2xl font-semibold text-navy">
          {t("health.title")}
        </h1>
        <p className="mt-2">
          {me.mfa_blocked_permissions.includes("lineage.read") ? t("access.mfaRequired") : t("health.noAccess")}
        </p>
      </section>
    );
  }

  const [sources, reconciliation, summary] = await Promise.all([
    apiGetJson<SourceList>(session, "/api/v1/integrations/sources").catch(() => null),
    apiGetJson<ReconciliationList>(session, "/api/v1/integrations/reconciliation?days=30").catch(() => null),
    me.permissions.includes("dq.manage")
      ? apiGetJson<DqSummary>(session, "/api/v1/dq/summary?state=OPEN").catch(() => null)
      : null,
  ]);

  return (
    <section aria-labelledby="page-title" className="flex max-w-6xl flex-col gap-8">
      <div>
        <h1 id="page-title" className="text-2xl font-semibold text-navy">
          {t("health.title")}
        </h1>
        <p className="mt-2">{t("health.intro")}</p>
        {sources && reconciliation ? null : (
          <p role="alert" className="mt-2 font-semibold text-saffron-strong">
            {t("health.unavailable")}
          </p>
        )}
      </div>

      <div className="overflow-x-auto">
        <table className="w-full min-w-[880px] border-collapse text-left text-sm">
          <caption className="mb-2 text-left text-lg font-semibold text-navy">{t("health.sources")}</caption>
          <thead>
            <tr className="border-b border-control-border">
              <th scope="col" className={th}>{t("health.source")}</th>
              <th scope="col" className={th}>{t("health.approval")}</th>
              <th scope="col" className={th}>{t("health.status")}</th>
              <th scope="col" className={th}>{t("health.lastBatch")}</th>
              <th scope="col" className={th}>{t("health.counts")}</th>
              <th scope="col" className={th}>{t("health.deadLetters")}</th>
            </tr>
          </thead>
          <tbody>
            {(sources?.data ?? []).map((s) => (
              <tr key={s.code} className="border-b border-divider align-top">
                <th scope="row" className={`${td} font-medium`}>
                  {s.name}
                  <span className="block font-mono text-xs font-normal">{s.code}</span>
                </th>
                <td className={td}>{s.is_connected ? s.approved_status : t("health.notConnected")}</td>
                <td className={td}>
                  <span className={s.last_health_status === "STALE" ? "font-semibold text-saffron-strong" : undefined}>
                    {healthLabel(s.last_health_status)}
                  </span>
                  {s.stale_after_hours ? <span className="block text-xs">{t("health.staleAfter", { hours: s.stale_after_hours })}</span> : null}
                </td>
                <td className={td}>{formatIst(s.last_batch_at)}</td>
                <td className={td}>
                  {s.last_batch
                    ? t("health.batchCounts", {
                        accepted: s.last_batch.accepted,
                        duplicates: s.last_batch.duplicates,
                        rejected: s.last_batch.rejected,
                        total: s.last_batch.record_count,
                      })
                    : "—"}
                </td>
                <td className={td}>{s.is_connected ? s.open_dead_letters : "—"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <div className="overflow-x-auto">
        <table className="w-full min-w-[760px] border-collapse text-left text-sm">
          <caption className="mb-2 text-left text-lg font-semibold text-navy">{t("health.reconciliation")}</caption>
          <thead>
            <tr className="border-b border-control-border">
              <th scope="col" className={th}>{t("health.day")}</th>
              <th scope="col" className={th}>{t("health.source")}</th>
              <th scope="col" className={th}>{t("health.declared")}</th>
              <th scope="col" className={th}>{t("health.accepted")}</th>
              <th scope="col" className={th}>{t("health.duplicates")}</th>
              <th scope="col" className={th}>{t("health.rejected")}</th>
              <th scope="col" className={th}>{t("health.unexplained")}</th>
              <th scope="col" className={th}>{t("health.badBatches")}</th>
            </tr>
          </thead>
          <tbody>
            {(reconciliation?.data ?? []).length === 0 ? (
              <tr>
                <td colSpan={8} className={td}>
                  {t("health.noBatches")}
                </td>
              </tr>
            ) : (
              (reconciliation?.data ?? []).map((r) => (
                <tr key={`${r.received_on}-${r.source_code}`} className="border-b border-divider">
                  <td className={td}>{r.received_on}</td>
                  <td className={`${td} font-mono text-xs`}>{r.source_code}</td>
                  <td className={td}>{r.declared}</td>
                  <td className={td}>{r.accepted}</td>
                  <td className={td}>{r.duplicates}</td>
                  <td className={td}>{r.rejected}</td>
                  <td className={`${td} ${r.unexplained !== 0 ? "font-semibold text-saffron-strong" : ""}`}>{r.unexplained}</td>
                  <td className={td}>{r.rejected_batches}</td>
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>

      {summary ? (
        <div className="overflow-x-auto">
          <table className="w-full max-w-2xl border-collapse text-left text-sm">
            <caption className="mb-2 text-left text-lg font-semibold text-navy">{t("health.dq")}</caption>
            <thead>
              <tr className="border-b border-control-border">
                <th scope="col" className={th}>{t("review.type")}</th>
                <th scope="col" className={th}>{t("review.severity")}</th>
                <th scope="col" className={th}>{t("health.count")}</th>
              </tr>
            </thead>
            <tbody>
              {summary.by_type.map((row) => (
                <tr key={`${row.issue_type}-${row.severity}`} className="border-b border-divider">
                  <td className={td}>{issueLabel(row.issue_type)}</td>
                  <td className={td}>{severityLabel(row.severity)}</td>
                  <td className={td}>{row.count}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : null}
    </section>
  );
}
