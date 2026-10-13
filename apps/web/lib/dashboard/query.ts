import type { components } from "@crimemap/types";
import { type TimeBasis, defaultFilters } from "@/lib/map/query";

/** PY Dashboard state and its translation into API queries (pure, unit-tested). */

export type StationList = components["schemas"]["StationList"];
export type StationDetails = components["schemas"]["StationDetailsOut"];
export type TrendBoard = components["schemas"]["TrendBoardOut"];
export type TrendRow = components["schemas"]["TrendRowOut"];

export const BOARDS = ["top-spot", "station-rate", "region", "location", "time", "month", "week"] as const;
export type BoardName = (typeof BOARDS)[number];

export type DashboardFilters = {
  region: string; // "ALL" or a region code
  station: string | null;
  category: string; // "" = all categories
  periodFrom: string;
  periodTo: string;
  timeBasis: TimeBasis;
};

export function defaultDashboard(now: Date = new Date()): DashboardFilters {
  const f = defaultFilters("PDY", now);
  return { region: "ALL", station: null, category: "", periodFrom: f.periodFrom, periodTo: f.periodTo, timeBasis: "OCCURRENCE" };
}

function common(f: DashboardFilters): URLSearchParams {
  const q = new URLSearchParams();
  if (f.category) q.set("category", f.category);
  q.set("period_from", f.periodFrom);
  q.set("period_to", f.periodTo);
  q.set("time_basis", f.timeBasis);
  return q;
}

/** Trends follow the same region and station as the list and the map (PD-1). */
export function trendsQuery(f: DashboardFilters): string {
  const q = common(f);
  q.set("region", f.region);
  if (f.station) q.set("station", f.station);
  return q.toString();
}

export function detailsQuery(f: DashboardFilters): string {
  return common(f).toString();
}

/** The region drawn on the map: the selected station's, the selected region, or the first permitted one. */
export function mapRegion(f: DashboardFilters, list: StationList | undefined): string | null {
  if (!list || list.regions.length === 0) return null;
  if (f.station) return list.stations.find((s) => s.code === f.station)?.region_code ?? null;
  if (f.region !== "ALL") return f.region;
  return list.regions[0]?.code ?? null;
}

export function mapQuery(f: DashboardFilters, region: string, station: string | null): string {
  const q = common(f);
  q.set("region", region);
  q.set("aggregation", "HEX");
  q.set("hex_size_m", "500");
  if (station) q.set("station", station);
  return q.toString();
}

/** Stations shown in the sidebar: the region filter, then the search text (name or code). */
export function visibleStations(list: StationList | undefined, region: string, search: string) {
  const needle = search.trim().toLowerCase();
  return (list?.stations ?? []).filter(
    (s) => (region === "ALL" || s.region_code === region) && (!needle || s.name.toLowerCase().includes(needle) || s.code.toLowerCase().includes(needle)),
  );
}

/** Movement against the comparison bucket, as a symbol and a sentence (never colour alone). */
export function movement(row: Pick<TrendRow, "movement" | "change_pct" | "previous">): { symbol: string; text: string } {
  switch (row.movement) {
    case "UP":
      return { symbol: `▲ ${row.change_pct}%`, text: `up ${row.change_pct}% from ${row.previous}` };
    case "DOWN":
      return { symbol: `▼ ${Math.abs(row.change_pct ?? 0)}%`, text: `down ${Math.abs(row.change_pct ?? 0)}% from ${row.previous}` };
    case "SAME":
      return { symbol: "＝", text: `unchanged from ${row.previous}` };
    case "NEW":
      return { symbol: "new", text: "none in the comparison period" };
    default:
      return { symbol: "—", text: "no comparison" };
  }
}
