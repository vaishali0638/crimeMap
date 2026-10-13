import Link from "next/link";
import type { components } from "@crimemap/types";
import { apiGetJson } from "@/lib/api";
import { requireUser } from "@/lib/current-user";
import { formatIst } from "@/lib/dq";
import { t } from "@/lib/i18n";

export const dynamic = "force-dynamic";

type ReportList = components["schemas"]["ReportList"];

/** Reports (Phase 5): the caller's own exports from the last 24 hours. Exports are created from the
 * Analytics Workbench ("Export Report"); each download is audited. */
export default async function ReportsPage() {
  const { session } = await requireUser();
  const list = await apiGetJson<ReportList>(session, "/api/v1/reports");
  if (list === null) {
    return (
      <section aria-labelledby="reports-title" className="flex max-w-5xl flex-col gap-4">
        <h1 id="reports-title" className="text-2xl font-semibold text-navy">{t("reports.title")}</h1>
        <p role="alert">{t("reports.error")}</p>
      </section>
    );
  }
  const rows = list.data;
  return (
    <section aria-labelledby="reports-title" className="flex max-w-5xl flex-col gap-4">
      <h1 id="reports-title" className="text-2xl font-semibold text-navy">{t("reports.title")}</h1>
      <p className="text-sm">{t("reports.intro")}</p>
      <p className="text-sm"><Link className="underline" href="/analytics">{t("reports.create")}</Link></p>
      {rows.length === 0 ? (
        <p>{t("reports.none")}</p>
      ) : (
        <table className="w-full border-collapse text-sm">
          <caption className="sr-only">{t("reports.title")}</caption>
          <thead>
            <tr className="border-b border-divider text-left">
              <th scope="col" className="p-2">{t("reports.file")}</th>
              <th scope="col" className="p-2">{t("reports.format")}</th>
              <th scope="col" className="p-2">{t("reports.created")}</th>
              <th scope="col" className="p-2">{t("reports.expires")}</th>
              <th scope="col" className="p-2">{t("reports.classification")}</th>
              <th scope="col" className="p-2">{t("reports.downloads")}</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.id} className="border-b border-divider">
                <th scope="row" className="p-2 text-left font-normal">
                  <a className="underline" href={`/bff/api/reports/${encodeURIComponent(r.id)}/download`}>{r.filename}</a>
                </th>
                <td className="p-2">{r.format}</td>
                <td className="p-2">{r.created_at ? formatIst(String(r.created_at)) : "—"}</td>
                <td className="p-2">{r.expires_at ? formatIst(String(r.expires_at)) : "—"}</td>
                <td className="p-2">{r.classification}</td>
                <td className="p-2 tabular-nums">{r.downloads}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </section>
  );
}
