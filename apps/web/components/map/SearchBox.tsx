"use client";

import { useQuery } from "@tanstack/react-query";
import { useEffect, useId, useRef, useState } from "react";
import { type SearchResponse, getJson } from "@/lib/map/client";
import { useMapStore } from "@/lib/map/store";
import { t } from "@/lib/i18n";

const item = "flex min-h-11 w-full items-center justify-between gap-2 rounded-control px-2 text-left text-sm hover:bg-surface-2";

/** Global search: permitted references, regions/stations/beats, localities — all scope-filtered by the API. */
export function SearchBox({ initial = "" }: { initial?: string }) {
  const [text, setText] = useState(initial);
  const [query, setQuery] = useState(initial.trim());
  const [open, setOpen] = useState(Boolean(initial));
  const listId = useId();
  const box = useRef<HTMLDivElement>(null);
  const { select, switchRegion, setDraft, apply, applied } = useMapStore();
  // Close the results when focus or a click moves elsewhere.
  useEffect(() => {
    if (!open) return;
    const away = (e: Event) => {
      if (box.current && e.target instanceof Node && !box.current.contains(e.target)) setOpen(false);
    };
    document.addEventListener("pointerdown", away);
    document.addEventListener("focusin", away);
    return () => {
      document.removeEventListener("pointerdown", away);
      document.removeEventListener("focusin", away);
    };
  }, [open]);
  useEffect(() => {
    const handle = setTimeout(() => setQuery(text.trim()), 300);
    return () => clearTimeout(handle);
  }, [text]);
  const results = useQuery({
    queryKey: ["search", query],
    queryFn: ({ signal }) => getJson<SearchResponse>(`search?q=${encodeURIComponent(query)}`, signal),
    enabled: query.length >= 2,
  });
  // Applies the search result on top of the *applied* filters, so unrelated unapplied edits in the
  // filter bar are not applied by accident.
  const focus = (region: string, patch: Partial<typeof applied>) => {
    const moving = Boolean(region) && region !== applied.region;
    if (moving) switchRegion(region);
    setDraft(moving ? patch : { ...applied, ...patch });
    apply();
    setOpen(false);
  };
  const data = results.data;
  const empty = data && !data.incidents.length && !data.units.length && !data.localities.length && !data.categories.length && !data.period;
  return (
    <div ref={box} className="relative w-full max-w-xl">
      <label htmlFor={`${listId}-input`} className="sr-only">{t("map.searchLabel")}</label>
      <input
        id={`${listId}-input`}
        type="search"
        value={text}
        onChange={(e) => {
          setText(e.target.value);
          setOpen(true);
        }}
        onKeyDown={(e) => {
          if (e.key === "Escape") setOpen(false);
        }}
        placeholder={t("map.searchPlaceholder")}
        // Only while the results panel exists: a reference to a missing element is invalid ARIA
        // (axe aria-valid-attr-value, Phase 8 audit).
        aria-controls={open && data ? listId : undefined}
        className="min-h-11 w-full rounded-control border border-control-border bg-surface px-3 text-sm"
      />
      {open && data ? (
        <div id={listId} className="absolute left-0 right-0 top-12 z-20 flex flex-col gap-3 rounded-panel border border-divider bg-surface p-3 shadow-md">
          {empty ? <p className="text-sm">{t("map.searchEmpty")}</p> : null}
          {data.incidents.length ? (
            <section aria-label={t("map.searchIncidents")}>
              <h3 className="text-xs font-semibold uppercase text-navy">{t("map.searchIncidents")}</h3>
              {data.incidents.map((i) => (
                <button key={i.id} type="button" className={item} onClick={() => { if (i.region_code && i.region_code !== applied.region) switchRegion(i.region_code); select({ kind: "incident", id: i.id }); setOpen(false); }}>
                  <span className="font-mono">{i.reference}</span>
                  <span>{i.station_code}</span>
                </button>
              ))}
            </section>
          ) : null}
          {data.units.length ? (
            <section aria-label={t("map.searchUnits")}>
              <h3 className="text-xs font-semibold uppercase text-navy">{t("map.searchUnits")}</h3>
              {data.units.map((u) => (
                <button
                  key={u.code}
                  type="button"
                  className={item}
                  onClick={() =>
                    focus(u.region_code, u.unit_type === "STATION" ? { stations: [u.code] } : u.unit_type === "BEAT" ? { stations: [u.code.split("-B")[0] ?? ""], beats: [u.code] } : {})
                  }
                >
                  <span>{u.name}</span>
                  <span className="font-mono text-xs">{u.code}</span>
                </button>
              ))}
            </section>
          ) : null}
          {data.localities.length ? (
            <section aria-label={t("map.searchLocalities")}>
              <h3 className="text-xs font-semibold uppercase text-navy">{t("map.searchLocalities")}</h3>
              {data.localities.map((l) => (
                <button key={`${l.locality}-${l.station_code}`} type="button" className={item} onClick={() => l.station_code && focus(l.station_code.split("-")[0] ?? "", { stations: [l.station_code] })}>
                  <span>{l.locality}</span>
                  <span className="tabular-nums">{l.records}</span>
                </button>
              ))}
            </section>
          ) : null}
          {data.period ? (
            <button type="button" className={item} onClick={() => data.period && focus("", { periodFrom: data.period.period_from, periodTo: data.period.period_to })}>
              {t("map.searchPeriod", { from: data.period.period_from, to: data.period.period_to })}
            </button>
          ) : null}
          {data.categories.length ? (
            <section aria-label={t("map.searchCategories")}>
              <h3 className="text-xs font-semibold uppercase text-navy">{t("map.searchCategories")}</h3>
              {data.categories.map((c) => (
                <button key={c.code} type="button" className={item} onClick={() => focus("", { categories: [c.code] })}>
                  <span>{c.label}</span>
                </button>
              ))}
            </section>
          ) : null}
          {data.not_available.length ? <p className="text-xs">{t("map.searchNotAvailable")}</p> : null}
        </div>
      ) : null}
    </div>
  );
}
