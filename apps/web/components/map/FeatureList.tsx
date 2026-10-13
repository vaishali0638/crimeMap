"use client";

import type { MapResponse } from "@/lib/map/client";
import { type Selection, useMapStore } from "@/lib/map/store";
import { t } from "@/lib/i18n";

const LIMIT = 50;

type Item = { key: string; label: string; count: number; selection: NonNullable<Selection> };

function items(data: MapResponse): Item[] {
  const out: Item[] = [];
  for (const f of data.features as { properties?: Record<string, unknown> }[]) {
    const p = f.properties ?? {};
    const count = Number(p.count ?? 1);
    if (p.kind === "incident") {
      out.push({ key: `i:${String(p.id)}`, label: t("map.featureRecord", { category: String(p.category_label ?? "") }), count, selection: { kind: "incident", id: String(p.id) } });
    } else if (p.kind === "cell") {
      out.push({ key: `c:${String(p.key)}`, label: t("map.featureCell", { count: count.toLocaleString("en-IN") }), count, selection: { kind: "cell", key: String(p.key), properties: p } });
    } else if (p.kind === "beat") {
      out.push({ key: `b:${String(p.code)}`, label: t("map.featureBeat", { code: String(p.code), count: count.toLocaleString("en-IN") }), count, selection: { kind: "beat", code: String(p.code), properties: p } });
    }
  }
  return out.sort((a, b) => b.count - a.count);
}

/**
 * The map's features as a keyboard- and screen-reader-usable list (WCAG 2.1.1): choosing an item
 * opens the same summary drawer as clicking it on the map. Largest cells first.
 */
export function FeatureList({ data }: { data: MapResponse | undefined }) {
  const { selection, select } = useMapStore();
  if (!data) return null;
  const all = items(data);
  const shown = all.slice(0, LIMIT);
  const current = selection ? `${selection.kind[0]}:${selection.kind === "incident" || selection.kind === "record" ? selection.id : selection.kind === "cell" ? selection.key : selection.code}` : null;
  return (
    <details className="border-t border-divider p-4 text-sm">
      <summary className="min-h-11 cursor-pointer content-center font-semibold text-navy">{t("map.featureList")}</summary>
      <p className="mt-1 text-xs">{t("map.featureListHelp")}</p>
      {shown.length === 0 ? <p className="mt-2">{t("map.featureListEmpty")}</p> : null}
      <ul className="mt-2 flex flex-col gap-1">
        {shown.map((item) => (
          <li key={item.key}>
            <button
              type="button"
              aria-pressed={current === item.key}
              onClick={() => select(item.selection)}
              className="flex min-h-11 w-full items-center rounded-control px-2 text-left hover:bg-surface-2 aria-pressed:bg-surface-2 aria-pressed:font-semibold"
            >
              {item.label}
            </button>
          </li>
        ))}
      </ul>
      {all.length > LIMIT ? <p className="mt-2 text-xs">{t("map.featureListMore", { shown: String(LIMIT), total: all.length.toLocaleString("en-IN") })}</p> : null}
    </details>
  );
}
