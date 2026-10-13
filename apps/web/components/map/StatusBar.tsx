"use client";

import type { MapResponse } from "@/lib/map/client";
import { formatIst } from "@/lib/dq";
import { t } from "@/lib/i18n";
import type { MessageKey } from "@/messages/en";

const n = (v: number) => v.toLocaleString("en-IN");

/** Bottom status bar: selected / usable / excluded (with reasons) / precision / refresh. */
export function StatusBar({ data, loading, error }: { data: MapResponse | undefined; loading: boolean; error: string | null }) {
  if (error) return <p role="alert" className="border-t border-divider bg-surface px-4 py-2 text-sm text-saffron-strong">{error}</p>;
  if (!data) return <p className="border-t border-divider bg-surface px-4 py-2 text-sm">{t("map.status.loading")}</p>;
  const s = data.summary;
  const x = s.excluded;
  // Everything selected but not drawn; the breakdown below names each reason. Records the caller may
  // not count (small sensitive groups) are in no total at all, so they are not in this one either.
  const excluded = s.selected - s.shown;
  return (
    <div role="status" aria-live="polite" className="flex flex-wrap items-center gap-x-4 gap-y-1 border-t border-divider bg-surface px-4 py-2 text-sm">
      <span className="font-semibold">{t("map.status.selected", { count: n(s.selected) })}</span>
      <span>{t("map.status.usable", { count: n(s.usable_locations) })}</span>
      <details className="relative">
        <summary className="cursor-pointer underline">{t("map.status.excluded", { count: n(excluded) })} — {t("map.status.why")}</summary>
        <ul className="absolute bottom-8 left-0 z-10 w-80 rounded-panel border border-divider bg-surface p-3 shadow-md">
          <li>{t("map.status.noLocation", { count: n(x.no_location) })}</li>
          <li>{t("map.status.outside", { count: n(x.outside_jurisdiction) })}</li>
          <li>{t("map.status.residential", { count: n(x.aggregate_only_residential) })}</li>
          <li>{t("map.status.sensitive", { count: n(x.aggregate_only_sensitive) })}</li>
          <li>{t("map.status.outsideView", { count: n(x.outside_view) })}</li>
          {x.suppressed_small_cells.count === null ? <li>{t("map.status.suppressed", { label: x.suppressed_small_cells.label })}</li> : null}
        </ul>
      </details>
      <span>{t(`map.status.precision.${data.location_precision}` as MessageKey)}</span>
      {data.aggregation.reason ? <span className="text-saffron-strong">{t("map.status.aggregated", { reason: data.aggregation.reason })}</span> : null}
      <span>{t("map.status.synthetic")}</span>
      <span className="ml-auto">{loading ? t("map.status.loading") : t("map.status.refreshed", { time: formatIst(data.last_refreshed_at) })}</span>
    </div>
  );
}
