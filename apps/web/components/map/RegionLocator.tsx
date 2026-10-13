"use client";

import { useMapStore } from "@/lib/map/store";
import { t } from "@/lib/i18n";

const REGION_ORDER: Record<string, number> = { PDY: 1, KKL: 2, MAH: 3, YAN: 4 };

/** ADR-007: the map shows one region at a time; this switcher doubles as a locator of all four. */
export function RegionLocator({ regions }: { regions: { code: string; name: string }[] }) {
  const { applied, switchRegion } = useMapStore();
  const ordered = [...regions].sort((a, b) => (REGION_ORDER[a.code] ?? 99) - (REGION_ORDER[b.code] ?? 99));
  return (
    <nav
      aria-label={t("map.locator")}
      className="absolute left-4 top-4 z-20 flex flex-wrap items-center gap-1.5 rounded-lg border border-slate-700/80 bg-[#0F1E33]/90 p-1.5 text-xs shadow-xl backdrop-blur-md"
    >
      <span className="px-2 font-semibold uppercase tracking-wider text-teal-300">{t("map.regionSwitcher")}:</span>
      {ordered.map((r) => (
        <button
          key={r.code}
          type="button"
          aria-current={applied.region === r.code ? "true" : undefined}
          onClick={() => switchRegion(r.code)}
          className={`min-h-9 rounded-md px-3 py-1.5 font-semibold transition ${
            applied.region === r.code
              ? "bg-[#2A7B78] text-white shadow-sm"
              : "text-slate-300 hover:bg-slate-800/80 hover:text-white"
          }`}
        >
          {r.name} ({r.code})
        </button>
      ))}
    </nav>
  );
}
