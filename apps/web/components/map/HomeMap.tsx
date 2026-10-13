"use client";

import { QueryClient, QueryClientProvider, keepPreviousData, useQuery } from "@tanstack/react-query";
import Link from "next/link";
import { useEffect, useMemo, useState } from "react";
import { ApiError, type FilterMetadata, type MapResponse, getJson } from "@/lib/map/client";
import { type MapFilters, featuresQuery } from "@/lib/map/query";
import { useMapStore } from "@/lib/map/store";
import { formatIst } from "@/lib/dq";
import { t } from "@/lib/i18n";
import type { MessageKey } from "@/messages/en";
import { AddRecordDialog } from "./AddRecordDialog";
import { FeatureList } from "./FeatureList";
import { FilterBar } from "./FilterBar";
import { LayerPanel } from "./LayerPanel";
import { Legend } from "./Legend";
import { type FC, MapCanvas, type ViewBox } from "./MapCanvas";
import { RecordDrawer } from "./RecordDrawer";
import { RegionLocator } from "./RegionLocator";
import { SearchBox } from "./SearchBox";
import { StatusBar } from "./StatusBar";

type Props = { permissions: string[]; csrfToken: string; styleUrl: string | null; initialQuery: string };

/** What went wrong, in words the user can act on (the API's own explanation for rejected filters). */
function mapError(error: unknown): string {
  if (!(error instanceof ApiError)) return t("map.status.error");
  if (error.status === 401) return t("map.status.signIn");
  if (error.status === 403) return t("map.status.forbidden");
  if (error.status === 422 && error.explanation) return t("map.status.rejected", { reason: error.explanation });
  return t("map.status.error");
}

function activeFilters(applied: MapFilters, meta: FilterMetadata): string {
  const name = <T extends { code: string }>(list: T[], code: string, label: (x: T) => string) => {
    const hit = list.find((x) => x.code === code);
    return hit ? label(hit) : code;
  };
  const parts = [
    ...applied.beats.map((b) => b),
    ...applied.categories.map((c) => name(meta.categories, c, (x) => x.label)),
    ...applied.recordTypes.map((r) => r.replace("_", " ")),
    ...applied.statuses.map((s) => name(meta.statuses, s, (x) => x.label)),
    ...(applied.minConfidence !== "0" ? [t(`map.confidence.${applied.minConfidence}` as MessageKey)] : []),
  ];
  return parts.length ? t("map.activeFilters", { filters: parts.join(", ") }) : t("map.noActiveFilters");
}

function Workspace({ permissions, csrfToken, styleUrl, initialQuery }: Props) {
  const store = useMapStore();
  const { applied, aggregation, hexSizeM, layers, selection, picking, select, pick, cancelPicking, switchRegion } = store;
  const [view, setView] = useState<ViewBox | undefined>();
  const [styleFailed, setStyleFailed] = useState(false);
  const canPoints = permissions.includes("map.points.read");

  // Escape leaves picking mode (back to the dialog, where coordinates can be typed instead).
  useEffect(() => {
    if (!picking) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") cancelPicking();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [picking, cancelPicking]);

  // First: which regions may this user see? Then the metadata for the region in view.
  const initial = useQuery({
    queryKey: ["meta-initial"],
    queryFn: ({ signal }) => getJson<FilterMetadata>("filters/metadata", signal),
    retry: false,
    staleTime: Infinity,
  });
  const initialised = !!initial.data && initial.data.regions.some((r) => r.code === applied.region);
  useEffect(() => {
    if (initial.data && !initial.data.regions.some((r) => r.code === applied.region)) switchRegion(initial.data.region);
  }, [initial.data, applied.region, switchRegion]);
  const regional = useQuery({
    queryKey: ["meta", applied.region],
    queryFn: ({ signal }) => getJson<FilterMetadata>(`filters/metadata?region=${applied.region}`, signal),
    enabled: initialised,
    retry: false,
    // Keep the workspace (map, dialog, focus) mounted while the next region's metadata loads.
    placeholderData: keepPreviousData,
  });
  const meta = initial.isError ? initial : regional;

  const allowed = meta.data?.aggregations ?? [];
  const effectiveAggregation = allowed.includes(aggregation) ? aggregation : "HEX";
  const query = useMemo(
    () => featuresQuery(applied, { aggregation: effectiveAggregation, hexSizeM, bbox: effectiveAggregation === "POINTS" ? view : undefined }).toString(),
    [applied, effectiveAggregation, hexSizeM, view],
  );
  const features = useQuery({
    queryKey: ["features", query],
    queryFn: ({ signal }) => getJson<MapResponse>(`map/features?${query}`, signal),
    enabled: initialised && regional.isSuccess && regional.data.region === applied.region,
    placeholderData: keepPreviousData,
    retry: false,
  });
  const boundary = (level: string) => ({
    queryKey: ["boundaries", level, applied.region],
    queryFn: ({ signal }: { signal: AbortSignal }) => getJson<FC>(`boundaries?level=${level}&region=${applied.region}`, signal),
    enabled: initialised,
    staleTime: 10 * 60_000,
  });
  const regionShape = useQuery(boundary("REGION"));
  const stationShapes = useQuery(boundary("STATION"));
  const beatShapes = useQuery({ ...boundary("BEAT"), enabled: initialised && layers.beats });
  // The records layer follows the map's region and period, like the cases.
  const recordsQuery = `region=${applied.region}&period_from=${applied.periodFrom}&period_to=${applied.periodTo}`;
  const records = useQuery({
    queryKey: ["records", recordsQuery],
    queryFn: ({ signal }) => getJson<FC>(`map/operational-records?${recordsQuery}`, signal),
    enabled: initialised && layers.records && canPoints,
  });

  if (meta.isError) {
    return (
      <section aria-labelledby="page-title" className="flex max-w-3xl flex-col gap-3">
        <h1 id="page-title" className="text-2xl font-semibold text-navy">{t("map.title")}</h1>
        <p role="alert">{mapError(meta.error)}</p>
        <div className="flex gap-2">
          <button type="button" className="min-h-11 rounded-control bg-navy px-4 text-sm font-semibold text-on-navy" onClick={() => void (initial.isError ? initial.refetch() : regional.refetch())}>
            {t("map.retry")}
          </button>
          {initial.data && initial.data.region !== applied.region ? (
            <button type="button" className="min-h-11 rounded-control border border-control-border px-4 text-sm" onClick={() => switchRegion(initial.data.region)}>
              {initial.data.regions.find((r) => r.code === initial.data.region)?.name ?? initial.data.region}
            </button>
          ) : null}
        </div>
      </section>
    );
  }
  if (!meta.data) return <p role="status">{t("map.status.loading")}</p>;

  const categoryCodes = meta.data.categories.map((c) => c.code);
  const error = features.isError ? mapError(features.error) : null;
  const regionName = meta.data.regions.find((r) => r.code === applied.region)?.name ?? applied.region;
  const scope = applied.stations.length
    ? applied.stations.map((code) => meta.data.stations.find((s) => s.code === code)?.name ?? code).join(", ")
    : t("map.subtitleAll");
  const shownAs = features.data?.aggregation.applied ?? effectiveAggregation;

  const { setDraft, apply } = store;

  return (
    <section aria-labelledby="page-title" className="-mx-6 -my-8 flex min-h-[calc(100vh-7.5rem)] flex-col bg-[#0B192C]">
      {/* Dark-Navy Puducherry Police Command Header (matching reference screenshot) */}
      <div className="flex flex-wrap items-center justify-between gap-4 border-b border-slate-800 bg-[#0F1E33] px-5 py-3 text-slate-100">
        <div className="flex items-center gap-3">
          <div className="flex h-10 w-10 items-center justify-center rounded-full border border-amber-400/50 bg-[#182C47] text-xs font-extrabold tracking-wider text-amber-300 shadow-inner">
            PY
          </div>
          <div>
            <p className="text-xs font-semibold tracking-wide text-slate-300">Puducherry Police</p>
            <h1 id="page-title" className="text-lg font-bold tracking-tight text-white">
              Crimemap — {regionName}
            </h1>
          </div>
        </div>

        <div className="flex flex-1 justify-center max-w-md text-ink">
          <SearchBox initial={initialQuery} />
        </div>

        <div className="flex flex-wrap items-center gap-2.5">
          {permissions.includes("opsrecord.create") ? <AddRecordDialog meta={meta.data} csrfToken={csrfToken} /> : null}
          <Link
            href="/dashboard"
            className="inline-flex min-h-10 items-center rounded-md bg-[#2A7B78] px-4 py-2 text-xs font-semibold text-white shadow-sm hover:bg-[#328E8A]"
          >
            {t("map.dashboard")}
          </Link>
          <Link
            href="/analytics"
            className="inline-flex min-h-10 items-center rounded-md border border-slate-600 bg-[#15253B] px-4 py-2 text-xs font-semibold text-slate-100 hover:bg-slate-800"
          >
            {t("map.analytics")}
          </Link>
        </div>
      </div>

      {picking ? (
        <div role="status" className="flex flex-wrap items-center gap-3 bg-saffron px-4 py-1 text-sm font-semibold text-ink">
          <span>{t("record.picking")}</span>
          <button type="button" className="min-h-11 rounded-control border border-ink px-3" onClick={cancelPicking}>{t("record.cancelPicking")}</button>
        </div>
      ) : null}

      {/* Full-Screen Dark-Navy Map with Pill Tags, Floating Category Filter & Case Details Card */}
      <div className="relative min-h-[600px] w-full flex-1">
        <RegionLocator regions={meta.data.regions} />

        {/* Floating "Filter view by category" dropdown in top-right of map */}
        <div className="absolute right-16 top-4 z-20">
          <label className="sr-only" htmlFor="map-floating-category">
            Filter view by category
          </label>
          <select
            id="map-floating-category"
            aria-label="Filter view by category"
            value={applied.categories[0] ?? ""}
            onChange={(e) => {
              const val = e.target.value;
              setDraft({ categories: val ? [val] : [] });
              apply();
            }}
            className="min-h-10 rounded-lg border border-slate-700/90 bg-[#0F1E33]/90 px-3.5 py-2 text-xs font-semibold text-slate-200 shadow-xl backdrop-blur-md focus:outline-none focus:ring-2 focus:ring-teal-400"
          >
            <option value="">▽ Filter view by category</option>
            {meta.data.categories.map((c) => (
              <option key={c.code} value={c.code}>
                {c.label}
              </option>
            ))}
          </select>
        </div>

        <MapCanvas
          label={t("map.canvasLabel", { region: regionName })}
          styleUrl={styleUrl}
          regionKey={applied.region}
          cases={(features.data as unknown as FC | undefined) ?? null}
          aggregation={features.data?.aggregation.applied as "POINTS" | "HEX" | "BEAT" ?? effectiveAggregation}
          categoryCodes={categoryCodes}
          boundaries={{ region: regionShape.data ?? null, station: stationShapes.data ?? null, beat: beatShapes.data ?? null }}
          records={canPoints ? (records.data ?? null) : null}
          layers={layers}
          selection={selection}
          picking={picking}
          onSelect={select}
          onPick={pick}
          onView={setView}
          onError={setStyleFailed}
        />
        {styleFailed ? <p role="alert" className="absolute left-3 top-3 max-w-sm rounded-control bg-surface px-2 py-1 text-sm">{t("map.status.styleFailed")}</p> : null}
      </div>

      <RecordDrawer permissions={permissions} csrfToken={csrfToken} />
      <StatusBar data={features.data} loading={features.isFetching} error={error} />

      {/* Collapsible Advanced Filters, Layers & Legend Drawer below the Map */}
      <details className="border-t border-divider bg-surface text-ink">
        <summary className="cursor-pointer px-4 py-2.5 text-xs font-semibold uppercase tracking-wider text-navy hover:bg-surface-2">
          Advanced Map Filters, Layers & Legend ({t("map.subtitle", {
            scope,
            from: applied.periodFrom,
            to: applied.periodTo,
            basis: t(`map.basis.${applied.timeBasis}` as MessageKey).toLowerCase(),
            aggregation: t(`map.agg.${shownAs}` as MessageKey).toLowerCase(),
          })} · {activeFilters(applied, meta.data)})
        </summary>
        <FilterBar meta={meta.data} />
        <div className="flex flex-col border-t border-divider md:flex-row">
          <div className="w-full shrink-0 border-b border-divider bg-surface md:w-64 md:border-b-0 md:border-r">
            <LayerPanel aggregations={meta.data.aggregations} hexSizes={meta.data.hex_sizes_m} canPoints={canPoints} />
            <FeatureList data={features.data} />
          </div>
          <div className="flex-1 bg-surface p-4">
            <Legend data={features.data} categoryCodes={categoryCodes} />
            {features.data ? (
              <div className="mt-3 px-4 text-xs text-ink/80">
                <p>{features.data.about.spatial_unit}. {features.data.about.method} ({features.data.about.crs})</p>
                {features.data.last_refreshed_at ? <p className="mt-1">{t("map.status.refreshed", { time: formatIst(features.data.last_refreshed_at) })}</p> : null}
              </div>
            ) : null}
          </div>
        </div>
      </details>
    </section>
  );
}

export function HomeMap(props: Props) {
  const [client] = useState(() => new QueryClient({ defaultOptions: { queries: { refetchOnWindowFocus: false } } }));
  return (
    <QueryClientProvider client={client}>
      <Workspace {...props} />
    </QueryClientProvider>
  );
}
