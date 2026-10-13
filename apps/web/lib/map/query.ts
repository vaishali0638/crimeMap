/**
 * Home Map filter state and its translation into `/api/v1/map/features` query parameters (pure,
 * unit-tested). The API validates every value against the caller's scope; this only builds URLs.
 */

export type Aggregation = "POINTS" | "HEX" | "BEAT";
export type TimeBasis = "OCCURRENCE" | "REPORTED";
export type MinConfidence = "0" | "0.4" | "0.8";

export type MapFilters = {
  region: string;
  stations: string[];
  beats: string[];
  categories: string[];
  recordTypes: string[];
  statuses: string[];
  periodFrom: string; // YYYY-MM-DD (IST)
  periodTo: string;
  timeBasis: TimeBasis;
  minConfidence: MinConfidence;
};

export type View = { aggregation: Aggregation; hexSizeM: number; bbox?: [number, number, number, number] };

const IST = new Intl.DateTimeFormat("en-CA", { timeZone: "Asia/Kolkata", year: "numeric", month: "2-digit", day: "2-digit" });

export function istDate(value: Date): string {
  return IST.format(value);
}

export function defaultFilters(region: string, now: Date = new Date()): MapFilters {
  const to = istDate(now);
  const from = new Date(`${to}T00:00:00+05:30`);
  from.setUTCDate(from.getUTCDate() - 89);
  return {
    region,
    stations: [],
    beats: [],
    categories: [],
    recordTypes: [],
    statuses: [],
    periodFrom: istDate(from),
    periodTo: to,
    timeBasis: "OCCURRENCE",
    minConfidence: "0",
  };
}

export function featuresQuery(filters: MapFilters, view: View): URLSearchParams {
  const q = new URLSearchParams();
  q.set("region", filters.region);
  for (const s of filters.stations) q.append("station", s);
  for (const b of filters.beats) q.append("beat", b);
  for (const c of filters.categories) q.append("category", c);
  for (const r of filters.recordTypes) q.append("record_type", r);
  for (const s of filters.statuses) q.append("status", s);
  q.set("period_from", filters.periodFrom);
  q.set("period_to", filters.periodTo);
  q.set("time_basis", filters.timeBasis);
  if (filters.minConfidence !== "0") q.set("min_confidence", filters.minConfidence);
  q.set("aggregation", view.aggregation);
  if (view.aggregation === "HEX") q.set("hex_size_m", String(view.hexSizeM));
  if (view.aggregation === "POINTS" && view.bbox) q.set("bbox", view.bbox.map((n) => Number(n.toFixed(5))).join(","));
  return q;
}

export function regionFromCellKey(key: string): string | null {
  const match = /^([A-Z]{2,5}):\d+:-?\d+:-?\d+$/.exec(key);
  return match ? (match[1] ?? null) : null;
}
