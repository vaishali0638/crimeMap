"use client";

import type { MapResponse } from "@/lib/map/client";
import { MAP_COLORS, categoryColor } from "@/lib/map/style";
import { t } from "@/lib/i18n";

/** Legend: symbol meanings plus the categories actually on the map, with their counts. */
export function Legend({ data, categoryCodes }: { data: MapResponse | undefined; categoryCodes: string[] }) {
  const points = data?.aggregation.applied === "POINTS";
  return (
    <section aria-labelledby="legend-title" className="flex flex-col gap-3 p-4 text-sm">
      <h2 id="legend-title" className="font-semibold text-navy">{t("map.legend")}</h2>
      <ul className="flex flex-col gap-2">
        <li className="flex items-center gap-2"><span aria-hidden className="inline-block size-3 rounded-full" style={{ background: MAP_COLORS.point }} />{t("map.legend.case")}</li>
        <li className="flex items-center gap-2"><span aria-hidden className="inline-block h-3 w-4" style={{ background: `linear-gradient(90deg, ${MAP_COLORS.cellLow}, ${MAP_COLORS.cellHigh})` }} />{t("map.legend.cell")}</li>
        <li className="flex items-center gap-2"><span aria-hidden className="inline-block size-3 rounded-full border-2" style={{ borderColor: MAP_COLORS.dqOutline }} />{t("map.legend.dq")}</li>
        <li className="flex items-center gap-2"><span aria-hidden className="inline-block size-3 rounded-full border" style={{ background: MAP_COLORS.record, borderColor: MAP_COLORS.regionLine }} />{t("map.legend.action")}</li>
        <li className="flex items-center gap-2 text-ink/70"><span aria-hidden className="inline-block size-3 rotate-45 border border-ink/50" />{t("map.legend.pattern")}</li>
      </ul>
      {data && data.legend.length ? (
        <>
          <h3 className="font-semibold text-navy">{t("map.legend.categories")}</h3>
          <ul className="flex flex-col gap-1">
            {data.legend.map((item) => (
              <li key={item.code} className="flex items-center justify-between gap-2">
                <span className="flex items-center gap-2">
                  {points ? <span aria-hidden className="inline-block size-3 rounded-full" style={{ background: categoryColor(item.code, categoryCodes) }} /> : null}
                  {item.label}
                </span>
                <span className="tabular-nums">{item.count.toLocaleString("en-IN")}</span>
              </li>
            ))}
          </ul>
        </>
      ) : null}
    </section>
  );
}
