"use client";

import { QueryClient, QueryClientProvider, keepPreviousData, useQuery } from "@tanstack/react-query";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useId, useMemo, useState } from "react";
import { type DashboardFilters, type StationList, defaultDashboard, mapQuery, mapRegion, visibleStations } from "@/lib/dashboard/query";
import { type MapResponse, getJson } from "@/lib/map/client";
import type { TimeBasis } from "@/lib/map/query";
import type { Selection } from "@/lib/map/store";
import { t } from "@/lib/i18n";
import type { MessageKey } from "@/messages/en";
import { Legend } from "../map/Legend";
import { type FC, MapCanvas, STATION_LOCALITY_LABELS } from "../map/MapCanvas";
import { StatusBar } from "../map/StatusBar";
import { StationDetails } from "./StationDetails";
import { type MapTarget, TrendsDialog } from "./TrendsDialog";

type Props = { styleUrl: string | null; initialRegion: string | null; initialStation: string | null };

const control = "min-h-11 rounded-control border border-control-border bg-surface px-2 text-sm";
const label = "flex flex-col gap-1 text-sm font-medium";
const LAYERS = { cases: true, regions: true, stations: true, beats: false, records: false };

function only(fc: FC | undefined, test: (p: Record<string, unknown>) => boolean): FC | null {
  if (!fc) return null;
  const features = fc.features.filter((f) => test(f.properties ?? {}));
  return features.length ? { type: "FeatureCollection", features } : null;
}

function Dashboard({ styleUrl, initialRegion, initialStation }: Props) {
  const router = useRouter();
  const searchId = useId();
  const [filters, setFilters] = useState<DashboardFilters>(() => ({
    ...defaultDashboard(),
    region: initialRegion ?? "ALL",
    station: initialStation,
  }));
  const [mapPreviewRegion, setMapPreviewRegion] = useState<string>("PDY");
  const [search, setSearch] = useState("");
  const [detailsFor, setDetailsFor] = useState<string | null>(null);
  const [focusCell, setFocusCell] = useState<string | null>(null);
  const [styleFailed, setStyleFailed] = useState(false);
  const patch = (p: Partial<DashboardFilters>) => setFilters((f) => ({ ...f, ...p }));

  const list = useQuery({
    queryKey: ["dashboard-stations"],
    queryFn: ({ signal }) => getJson<StationList>("dashboard/stations", signal),
    retry: false,
    staleTime: 10 * 60_000,
  });

  // Ignore a region or station from the URL that is not in this user's list (the API would refuse it).
  const known = list.data;
  const region = known && filters.region !== "ALL" && !known.regions.some((r) => r.code === filters.region) ? "ALL" : filters.region;
  const station = known && filters.station && !known.stations.some((s) => s.code === filters.station) ? null : filters.station;
  const effective = useMemo(() => ({ ...filters, region, station }), [filters, region, station]);

  // Keep region and station in the URL so a view can be shared internally and restored.
  useEffect(() => {
    const q = new URLSearchParams();
    if (region !== "ALL") q.set("region", region);
    if (station) q.set("station", station);
    const next = q.toString() ? `/dashboard?${q.toString()}` : "/dashboard";
    if (`${window.location.pathname}${window.location.search}` !== next) router.replace(next, { scroll: false });
  }, [region, station, router]);

  const fallbackRegion = mapRegion(effective, known);
  const shownRegion =
    effective.region === "ALL" && !effective.station && known?.regions.some((r) => r.code === mapPreviewRegion)
      ? mapPreviewRegion
      : fallbackRegion;
  const regionQuery = shownRegion ? mapQuery(effective, shownRegion, null) : null;
  const stationQuery = shownRegion && station ? mapQuery(effective, shownRegion, station) : null;
  const regionFeatures = useQuery({
    queryKey: ["dash-features", regionQuery],
    queryFn: ({ signal }) => getJson<MapResponse>(`map/features?${regionQuery}`, signal),
    enabled: regionQuery !== null,
    placeholderData: keepPreviousData,
    retry: false,
  });
  const stationFeatures = useQuery({
    queryKey: ["dash-features", stationQuery],
    queryFn: ({ signal }) => getJson<MapResponse>(`map/features?${stationQuery}`, signal),
    enabled: stationQuery !== null,
    retry: false,
  });
  const shape = (level: string) => ({
    queryKey: ["boundaries", level, shownRegion],
    queryFn: ({ signal }: { signal: AbortSignal }) => getJson<FC>(`boundaries?level=${level}&region=${shownRegion}`, signal),
    enabled: shownRegion !== null,
    staleTime: 10 * 60_000,
  });
  const regionShape = useQuery(shape("REGION"));
  const stationShapes = useQuery(shape("STATION"));

  if (list.isError) return <p role="alert">{t("dash.noAccess")}</p>;
  if (!known) return <p role="status">{t("dash.loading")}</p>;

  const stations = visibleStations(known, region, search);
  const regionName = (code: string | null) => known.regions.find((r) => r.code === code)?.name ?? code ?? "";
  const stationName = (code: string | null) =>
    (code ? STATION_LOCALITY_LABELS[code] : undefined) ?? known.stations.find((s) => s.code === code)?.name ?? code ?? "";
  const highlight = station ? ((stationFeatures.data as unknown as FC | undefined) ?? null) : null;
  const shown = station ? stationFeatures.data : regionFeatures.data;
  const cellFeature = focusCell ? only(regionFeatures.data as unknown as FC | undefined, (p) => p.key === focusCell) : null;
  const fitTo = cellFeature ?? (station ? only(stationShapes.data, (p) => p.code === station) : null);
  const selection: Selection = focusCell ? { kind: "cell", key: focusCell, properties: {} } : null;
  const categoryCodes = known.categories.map((c) => c.code);
  const categoryLabel = known.categories.find((c) => c.code === effective.category)?.name ?? t("dash.allCategories");

  const selectStation = (code: string | null) => {
    setFocusCell(null);
    patch({ station: code });
  };
  const viewOnMap = (target: MapTarget) => {
    if (target.kind === "region") {
      setFocusCell(null);
      setMapPreviewRegion(target.region);
      patch({ region: target.region, station: null });
    } else if (target.kind === "station") {
      selectStation(target.code);
    } else {
      setMapPreviewRegion(target.region);
      patch({ region: target.region, station: null });
      setFocusCell(target.code);
    }
  };

  return (
    <section aria-labelledby="dash-title" className="-mx-6 -my-8 flex min-h-[calc(100vh-7.5rem)] flex-col">
      <header className="flex flex-wrap items-baseline justify-between gap-3 border-b border-divider bg-surface px-4 py-3">
        <div className="flex flex-wrap items-baseline gap-x-4 gap-y-1">
          <h1 id="dash-title" className="text-xl font-semibold text-navy">{t("dash.title")}</h1>
          <p className="text-sm">
            {t("dash.subtitle", {
              scope: station ? stationName(station) : region === "ALL" ? t("dash.allRegions") : regionName(region),
              from: effective.periodFrom,
              to: effective.periodTo,
              basis: t(`map.basis.${effective.timeBasis}` as MessageKey).toLowerCase(),
              category: categoryLabel,
            })}
          </p>
          <p className="text-xs font-semibold uppercase tracking-wide text-saffron-strong">{known.data_label}</p>
        </div>
        <Link href="/" className="inline-flex min-h-11 items-center rounded-control border border-control-border px-4 text-sm font-medium">{t("dash.backHome")}</Link>
      </header>
      {/* Below md the station list, map and legend stack (reflow, WCAG 1.4.10). */}
      <div className="flex min-h-[520px] flex-1 flex-col md:flex-row">
        <aside aria-label={t("dash.sidebar")} className="flex w-full shrink-0 flex-col gap-3 border-b border-divider bg-surface p-4 md:w-80 md:border-b-0 md:border-r">
          <Link href="/" className="inline-flex min-h-11 w-fit items-center rounded-control border border-control-border px-3 text-sm">{t("dash.back")}</Link>
          <label className={label}>
            {t("dash.region")}
            <select
              className={control}
              value={region}
              onChange={(e) => {
                setFocusCell(null);
                if (e.target.value !== "ALL") setMapPreviewRegion(e.target.value);
                patch({ region: e.target.value, station: null });
              }}
            >
              <option value="ALL">{t("dash.allRegions")}</option>
              {known.regions.map((r) => (
                <option key={r.code} value={r.code}>{r.name}</option>
              ))}
            </select>
          </label>
          <label className={label} htmlFor={searchId}>{t("dash.searchStation")}</label>
          <input id={searchId} type="search" className={control} value={search} onChange={(e) => setSearch(e.target.value)} />
          <ul className="flex min-h-0 flex-1 flex-col gap-1 overflow-y-auto" aria-label={t("dash.sidebar")}>
            {stations.length === 0 ? <li className="text-sm">{t("dash.noStations")}</li> : null}
            {stations.map((s) => {
              const displayLabel = STATION_LOCALITY_LABELS[s.code] ?? s.name;
              return (
                <li key={s.code} className={`flex items-center justify-between gap-2 rounded-control px-2 ${station === s.code ? "bg-surface-2" : ""}`}>
                  <button
                    type="button"
                    aria-pressed={station === s.code}
                    onClick={() => selectStation(station === s.code ? null : s.code)}
                    className="min-h-11 flex-1 py-1 text-left text-sm aria-pressed:font-semibold"
                  >
                    {station === s.code ? <span aria-hidden>▶ </span> : null}
                    <span className="block font-medium text-navy">{displayLabel}</span>
                    <span className="block text-xs text-ink/70">{s.name}</span>
                  </button>
                  <button type="button" className="min-h-11 rounded-control border border-control-border px-2 text-xs" aria-label={t("dash.viewDetailsFor", { station: s.name })} onClick={() => setDetailsFor(s.code)}>
                    {t("dash.viewDetails")}
                  </button>
                </li>
              );
            })}
          </ul>
        </aside>
        <div className="flex min-w-0 flex-1 flex-col">
          <form aria-label={t("map.filters")} className="flex flex-wrap items-end gap-3 border-b border-divider bg-surface-2 px-4 py-3" onSubmit={(e) => e.preventDefault()}>
            <label className={label}>
              {t("dash.category")}
              <select className={control} value={effective.category} onChange={(e) => patch({ category: e.target.value })}>
                <option value="">{t("dash.allCategories")}</option>
                {known.categories.map((c) => (
                  <option key={c.code} value={c.code}>{c.name}</option>
                ))}
              </select>
            </label>
            <label className={label}>
              {t("dash.from")}
              <input type="date" className={control} value={effective.periodFrom} max={effective.periodTo} onChange={(e) => e.target.value && patch({ periodFrom: e.target.value })} />
            </label>
            <label className={label}>
              {t("dash.to")}
              <input type="date" className={control} value={effective.periodTo} min={effective.periodFrom} onChange={(e) => e.target.value && patch({ periodTo: e.target.value })} />
            </label>
            <label className={label}>
              {t("dash.timeBasis")}
              <select className={control} value={effective.timeBasis} onChange={(e) => patch({ timeBasis: e.target.value as TimeBasis })}>
                <option value="OCCURRENCE">{t("map.basis.OCCURRENCE")}</option>
                <option value="REPORTED">{t("map.basis.REPORTED")}</option>
              </select>
            </label>
            <div className="ml-auto">
              <TrendsDialog filters={effective} onViewOnMap={viewOnMap} />
            </div>
          </form>
          <div className="flex flex-wrap items-center justify-between gap-2 border-b border-divider bg-surface px-4 py-2 text-xs">
            <div className="flex flex-wrap items-center gap-2">
              <span className="font-semibold text-navy">Official Region Map & Crime Tags:</span>
              {known.regions.map((r) => {
                const active = shownRegion === r.code;
                return (
                  <button
                    key={r.code}
                    type="button"
                    onClick={() => {
                      setFocusCell(null);
                      setMapPreviewRegion(r.code);
                      if (region !== "ALL") patch({ region: r.code, station: null });
                      else patch({ station: null });
                    }}
                    className={`min-h-9 rounded-control px-3 py-1 text-xs font-semibold transition ${
                      active ? "bg-navy text-on-navy" : "border border-control-border bg-surface hover:bg-surface-2"
                    }`}
                  >
                    {r.name} Map ({r.code})
                  </button>
                );
              })}
            </div>
            {region === "ALL" && !station ? <span>{t("dash.mapOneRegion")}</span> : null}
          </div>
          <div className="flex min-h-0 flex-1 flex-col md:flex-row">
            <div className="relative min-h-[420px] min-w-0 flex-1">
              {shownRegion ? (
                <MapCanvas
                  label={t("dash.mapLabel", { region: regionName(shownRegion), scope: station ? stationName(station) : t("dash.mapScopeAll") })}
                  styleUrl={styleUrl}
                  regionKey={shownRegion}
                  cases={(regionFeatures.data as unknown as FC | undefined) ?? null}
                  aggregation="HEX"
                  categoryCodes={categoryCodes}
                  boundaries={{ region: regionShape.data ?? null, station: stationShapes.data ?? null, beat: null }}
                  records={null}
                  layers={LAYERS}
                  selection={selection}
                  picking={false}
                  onSelect={(sel) => {
                    if (sel?.kind === "cell") setFocusCell(sel.key);
                  }}
                  onPick={() => undefined}
                  onView={() => undefined}
                  onError={setStyleFailed}
                  highlight={highlight}
                  selectedStation={station}
                  fitTo={fitTo}
                />
              ) : null}
              {styleFailed ? <p role="alert" className="absolute left-3 top-3 max-w-sm rounded-control bg-surface px-2 py-1 text-sm">{t("map.status.styleFailed")}</p> : null}
            </div>
            <div className="w-full shrink-0 overflow-y-auto border-t border-divider bg-surface md:w-60 md:border-l md:border-t-0">
              <Legend data={shown} categoryCodes={categoryCodes} />
            </div>
          </div>
          <StatusBar
            data={shown}
            loading={regionFeatures.isFetching || stationFeatures.isFetching}
            error={regionFeatures.isError || stationFeatures.isError ? t("map.status.error") : null}
          />
        </div>
      </div>
      <StationDetails
        station={detailsFor}
        filters={effective}
        onClose={() => setDetailsFor(null)}
        onViewOnMap={(code) => {
          setDetailsFor(null);
          selectStation(code);
        }}
      />
    </section>
  );
}

export function PyDashboard(props: Props) {
  const [client] = useState(() => new QueryClient({ defaultOptions: { queries: { refetchOnWindowFocus: false } } }));
  return (
    <QueryClientProvider client={client}>
      <Dashboard {...props} />
    </QueryClientProvider>
  );
}
