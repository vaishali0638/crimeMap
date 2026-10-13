import { defaultFilters } from "@/lib/map/query";
import { t } from "@/lib/i18n";

/** Analytics Workbench filter state and its translation to the API (pure, unit-tested). */

export const SECTIONS = ["overview", "location", "case_trends", "civic", "ai", "comparison", "data_quality"] as const;
export type SectionId = (typeof SECTIONS)[number];
export const API_SECTION: Partial<Record<SectionId, string>> = {
  overview: "overview",
  location: "location",
  case_trends: "case-trends",
  comparison: "comparison",
  data_quality: "data-quality",
};

export type RecordType = "CASE" | "ERSS_CALL" | "ACCIDENT" | "TRAFFIC_EVENT" | "COMBINED";

export type AnalyticsFilters = {
  region: string;
  station: string;
  beat: string;
  category: string;
  locality: string;
  periodFrom: string;
  periodTo: string;
  hourFrom: string; // "" or 0..23
  hourTo: string;
  recordType: RecordType;
  timeBasis: "OCCURRENCE" | "REPORTED";
  status: string;
  minConfidence: "0" | "0.4" | "0.8";
  comparison: "PREVIOUS" | "YEAR" | "NONE";
  grain: "" | "day" | "week" | "month"; // "" = chosen by the period length
  // section-specific
  unit: "HEX" | "BEAT" | "STATION";
  hexSizeM: "250" | "500" | "1000" | "2000";
  compare: "REGION" | "STATION" | "BEAT" | "PERIOD" | "YEAR" | "CATEGORY";
  a: string;
  b: string;
};

export function defaultAnalytics(region = "PDY", now: Date = new Date()): AnalyticsFilters {
  const f = defaultFilters(region, now);
  return {
    region, station: "", beat: "", category: "", locality: "", periodFrom: f.periodFrom, periodTo: f.periodTo,
    hourFrom: "", hourTo: "", recordType: "CASE", timeBasis: "OCCURRENCE", status: "", minConfidence: "0",
    comparison: "PREVIOUS", grain: "", unit: "HEX", hexSizeM: "500", compare: "PERIOD", a: "", b: "",
  };
}

/** The API's parameter object (also the body of evidence snapshots and exports). */
export function toParams(f: AnalyticsFilters, section: SectionId): Record<string, string | string[]> {
  const p: Record<string, string | string[]> = {
    region: f.region,
    period_from: f.periodFrom,
    period_to: f.periodTo,
    time_basis: f.timeBasis,
    record_type: f.recordType,
    comparison: f.comparison,
  };
  if (f.station) p.station = [f.station];
  if (f.beat) p.beat = [f.beat];
  if (f.category) p.category = [f.category];
  if (f.status) p.status = [f.status];
  if (f.locality.trim().length >= 2) p.locality = f.locality.trim();
  if (f.minConfidence !== "0") p.min_confidence = f.minConfidence;
  if (f.hourFrom !== "" && f.hourTo !== "") {
    p.hour_from = f.hourFrom;
    p.hour_to = f.hourTo;
  }
  if (section === "case_trends" && f.grain) p.grain = f.grain;
  if (section === "location") {
    p.unit = f.unit;
    p.hex_size_m = f.hexSizeM;
  }
  if (section === "comparison") {
    p.compare = f.compare;
    if (f.a) p.a = f.a;
    if (f.b) p.b = f.b;
  }
  return p;
}

export function toQuery(params: Record<string, string | string[]>): string {
  const q = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) for (const item of Array.isArray(v) ? v : [v]) q.append(k, item);
  return q.toString();
}

/** Problems the API would refuse, said before the request is sent (no wasted, audited 422s). */
export function validate(f: AnalyticsFilters, section: SectionId): string[] {
  const out: string[] = [];
  if (!/^\d{4}-\d{2}-\d{2}$/.test(f.periodFrom) || !/^\d{4}-\d{2}-\d{2}$/.test(f.periodTo)) out.push(t("an.v.dates"));
  else {
    const from = new Date(`${f.periodFrom}T00:00:00Z`).getTime();
    const to = new Date(`${f.periodTo}T00:00:00Z`).getTime();
    if (from > to) out.push(t("an.v.order"));
    else if ((to - from) / 86_400_000 + 1 > 731) out.push(t("an.v.long"));
    if (Number(f.periodFrom.slice(0, 4)) < 2000) out.push(t("an.v.early"));
  }
  if ((f.hourFrom === "") !== (f.hourTo === "")) out.push(t("an.v.hours"));
  if (f.locality.trim().length === 1) out.push(t("an.v.locality"));
  if (section === "comparison" && ["REGION", "STATION", "BEAT", "CATEGORY"].includes(f.compare) && (!f.a || !f.b)) out.push(t("an.v.ab"));
  return out;
}

const CODE = /^[A-Z0-9_-]{2,40}$/;
const DATE = /^\d{4}-\d{2}-\d{2}$/;

/** Restore section and filters from the URL (so a re-sign-in or a shared link reopens the same
 * analysis). Only recognised values are taken; anything else falls back to the default. */
export function fromQuery(search: string, fallbackRegion: string, regions: string[]): { section: SectionId | null; filters: AnalyticsFilters } {
  const q = new URLSearchParams(search);
  const pick = <T extends string>(key: string, allowed: readonly T[], fallback: T): T => {
    const v = q.get(key);
    return v !== null && (allowed as readonly string[]).includes(v) ? (v as T) : fallback;
  };
  const code = (key: string) => {
    const v = q.get(key) ?? "";
    return CODE.test(v) ? v : "";
  };
  const region = regions.includes(q.get("region") ?? "") ? (q.get("region") as string) : fallbackRegion;
  const base = defaultAnalytics(region);
  const hours = Array.from({ length: 24 }, (_, h) => String(h));
  const section = q.get("section");
  return {
    section: (SECTIONS as readonly string[]).includes(section ?? "") ? (section as SectionId) : null,
    filters: {
      ...base,
      station: code("station"),
      beat: code("beat"),
      category: code("category"),
      status: code("status"),
      locality: (q.get("locality") ?? "").slice(0, 100),
      periodFrom: DATE.test(q.get("period_from") ?? "") ? (q.get("period_from") as string) : base.periodFrom,
      periodTo: DATE.test(q.get("period_to") ?? "") ? (q.get("period_to") as string) : base.periodTo,
      hourFrom: pick("hour_from", ["", ...hours], ""),
      hourTo: pick("hour_to", ["", ...hours], ""),
      recordType: pick("record_type", ["CASE", "ERSS_CALL", "ACCIDENT", "TRAFFIC_EVENT", "COMBINED"] as const, "CASE"),
      timeBasis: pick("time_basis", ["OCCURRENCE", "REPORTED"] as const, "OCCURRENCE"),
      minConfidence: pick("min_confidence", ["0", "0.4", "0.8"] as const, "0"),
      comparison: pick("comparison", ["PREVIOUS", "YEAR", "NONE"] as const, "PREVIOUS"),
      grain: pick("grain", ["", "day", "week", "month"] as const, ""),
      unit: pick("unit", ["HEX", "BEAT", "STATION"] as const, "HEX"),
      hexSizeM: pick("hex_size_m", ["250", "500", "1000", "2000"] as const, "500"),
      compare: pick("compare", ["REGION", "STATION", "BEAT", "PERIOD", "YEAR", "CATEGORY"] as const, "PERIOD"),
      a: code("a"),
      b: code("b"),
    },
  };
}

/** The address of an analysis: section plus every parameter the API receives. */
export function analysisUrl(section: SectionId, f: AnalyticsFilters): string {
  return `/analytics?${toQuery({ section, ...toParams(f, section) })}`;
}
