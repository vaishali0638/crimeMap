"use client";

import * as Dialog from "@radix-ui/react-dialog";
import { useQuery } from "@tanstack/react-query";
import { type DashboardFilters, type StationDetails as Details, detailsQuery } from "@/lib/dashboard/query";
import { getJson } from "@/lib/map/client";
import { formatIst } from "@/lib/dq";
import { t } from "@/lib/i18n";

const btn = "min-h-11 rounded-control border border-control-border px-4 text-sm font-medium";
const n = (v: number) => v.toLocaleString("en-IN");

/**
 * "View Details": station, region, jurisdiction, selected-case counts, case status where permitted,
 * category counts; "View Station on Map" and "Back". Counts come from the same selection as the map
 * (the API checks territory and audits the view).
 */
export function StationDetails({
  station,
  filters,
  onClose,
  onViewOnMap,
}: {
  station: string | null;
  filters: DashboardFilters;
  onClose: () => void;
  onViewOnMap: (station: string) => void;
}) {
  const query = station ? `dashboard/stations/${encodeURIComponent(station)}?${detailsQuery(filters)}` : null;
  const details = useQuery({
    queryKey: ["station-details", query],
    queryFn: ({ signal }) => getJson<Details>(query as string, signal),
    enabled: query !== null,
    retry: false,
  });
  const d = details.data;
  return (
    <Dialog.Root open={station !== null} onOpenChange={(open) => !open && onClose()}>
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 bg-ink/40" />
        <Dialog.Content className="fixed inset-y-0 right-0 flex w-[min(480px,95vw)] flex-col gap-4 overflow-y-auto bg-surface p-6 shadow-lg">
          <Dialog.Title className="text-xl font-semibold text-navy">{d ? d.station.name : t("details.title")}</Dialog.Title>
          <Dialog.Description className="text-xs font-semibold uppercase tracking-wide text-saffron-strong">{d?.data_label ?? "DEMO / SYNTHETIC DATA"}</Dialog.Description>
          {details.isPending ? <p role="status">{t("dash.loading")}</p> : null}
          {details.isError ? <p role="alert">{t("dash.error")}</p> : null}
          {d ? (
            <>
              <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 text-sm">
                <dt className="font-medium">{t("details.region")}</dt>
                <dd>{d.region.name}</dd>
                <dt className="font-medium">{t("details.jurisdiction")}</dt>
                <dd>
                  {t("details.jurisdictionText", {
                    label: d.jurisdiction.label,
                    beats: String(d.jurisdiction.beats),
                    area: d.jurisdiction.area_km2 === null ? "—" : String(d.jurisdiction.area_km2),
                    version: String(d.jurisdiction.boundary_version ?? "—"),
                    status: d.jurisdiction.approval_status ?? "—",
                  })}
                </dd>
                <dt className="font-medium">{t("dash.category")}</dt>
                <dd>{d.category}</dd>
                <dt className="font-medium">{t("trends.period", { from: d.period.period_from, to: d.period.period_to })}</dt>
                <dd>{t(`map.basis.${d.period.basis}` as "map.basis.OCCURRENCE")}</dd>
              </dl>
              <p className="text-lg font-semibold">
                {t("details.selected")}: <span className="tabular-nums">{n(d.selected)}</span>
              </p>
              <section aria-labelledby="cases-title">
                <h3 id="cases-title" className="font-semibold text-navy">{t("details.cases")}</h3>
                {d.cases ? (
                  <dl className="mt-1 grid grid-cols-3 gap-2 text-sm">
                    {(["active", "detected", "closed"] as const).map((k) => (
                      <div key={k} className="rounded-control bg-surface-2 p-2">
                        <dt>{t(`details.${k}`)}</dt>
                        <dd className="text-lg font-semibold tabular-nums">{n(d.cases?.[k] ?? 0)}</dd>
                      </div>
                    ))}
                  </dl>
                ) : null}
                <p className="mt-1 text-xs">{d.cases_note}</p>
              </section>
              <section aria-labelledby="cats-title">
                <h3 id="cats-title" className="font-semibold text-navy">{t("details.categories")}</h3>
                <ul className="mt-1 flex flex-col gap-1 text-sm">
                  {d.categories.map((c) => (
                    <li key={c.code} className="flex justify-between gap-2">
                      <span>{c.label}</span>
                      <span className="tabular-nums">{n(c.count)}</span>
                    </li>
                  ))}
                </ul>
                {d.sensitive ? (
                  <>
                    <p className="mt-1 text-sm">{t("details.sensitive", { label: d.sensitive.label })}</p>
                    <p className="text-xs">{t("details.sensitiveNote")}</p>
                  </>
                ) : null}
              </section>
              {d.last_refreshed_at ? <p className="text-xs">{t("details.refreshed", { time: formatIst(d.last_refreshed_at) })}</p> : null}
            </>
          ) : null}
          <div className="mt-auto flex gap-2">
            <button type="button" className={`${btn} bg-navy text-on-navy`} disabled={!station} onClick={() => station && onViewOnMap(station)}>
              {t("details.viewOnMap")}
            </button>
            <Dialog.Close className={btn}>{t("details.back")}</Dialog.Close>
          </div>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
