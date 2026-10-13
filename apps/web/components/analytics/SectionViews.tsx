"use client";

import type { EChartsCoreOption } from "echarts/core";
import Link from "next/link";
import { useMemo } from "react";
import { PALETTE } from "@/lib/map/style";
import type { SectionId } from "@/lib/analytics/query";
import { formatIst } from "@/lib/dq";
import { t } from "@/lib/i18n";
import type { MessageKey } from "@/messages/en";
import { type FC, MapCanvas } from "../map/MapCanvas";
import { EChart } from "./EChart";

// API payloads are typed loosely here: the envelope's data shape differs per section and is
// documented in docs/api.md; everything shown is also in the Table view (data.tables).
type Data = Record<string, any>; // eslint-disable-line @typescript-eslint/no-explicit-any
export type View = "map" | "chart" | "table" | "explanation";

const NAVY = "#14324E";
const TEAL = "#1D6F77";
const n = (v: number | null | undefined) => (v === null || v === undefined ? "—" : v.toLocaleString("en-IN"));
const LAYERS = { cases: true, regions: true, stations: true, beats: false, records: false };
const NO_CODES: string[] = [];
const DASHES = ["solid", "dashed", "dotted"] as const;
const SYMBOLS = ["circle", "rect", "triangle", "diamond", "pin", "arrow"] as const;

/** Series i gets its own colour (colour-blind-safe palette) AND its own dash and marker shape, so
 * lines are never told apart by colour alone (WCAG 1.4.1). */
function line(points: { bucket: string; count: number }[], name: string, index = 0) {
  const color = index === 0 ? NAVY : (PALETTE[(index - 1) % PALETTE.length] ?? TEAL);
  return {
    type: "line",
    name,
    data: points.map((p) => [p.bucket, p.count]),
    showSymbol: true,
    symbol: SYMBOLS[index % SYMBOLS.length],
    symbolSize: points.length > 60 ? 4 : 7,
    lineStyle: { color, type: DASHES[index % DASHES.length], width: 2 },
    itemStyle: { color },
  };
}

function bars(rows: { label: string; count: number }[], color = TEAL): EChartsCoreOption {
  const top = rows.slice(0, 15).reverse();
  return {
    grid: { left: 180, right: 24, top: 8, bottom: 24 },
    xAxis: { type: "value" },
    yAxis: { type: "category", data: top.map((r) => r.label), axisLabel: { width: 170, overflow: "truncate" } },
    series: [{ type: "bar", data: top.map((r) => r.count), itemStyle: { color } }],
  };
}

function timeOption(series: object[]): EChartsCoreOption {
  return { grid: { left: 48, right: 16, top: 32, bottom: 32 }, legend: { top: 0 }, xAxis: { type: "time" }, yAxis: { type: "value" }, series };
}

export function MapView({ region, fc, label, regionShape, legend = "count" }: { region: string; fc: FC | null; label: string; regionShape: FC | null; legend?: "count" | "density" }) {
  // Stable inputs: a new object each render would refit the camera and re-upload every source.
  const boundaries = useMemo(() => ({ region: regionShape, station: null, beat: null }), [regionShape]);
  const fitTo = useMemo(() => (fc && fc.features.length ? fc : regionShape), [fc, regionShape]);
  return (
    <div className="flex flex-col gap-2">
      <div className="h-[480px] w-full overflow-hidden rounded-panel border border-divider">
        <MapCanvas
          label={label}
          styleUrl={null}
          regionKey={region}
          cases={fc}
          aggregation="HEX"
          categoryCodes={NO_CODES}
          boundaries={boundaries}
          records={null}
          layers={LAYERS}
          selection={null}
          picking={false}
          onSelect={noop}
          onPick={noop}
          onView={noop}
          onError={noop}
          fitTo={fitTo}
        />
      </div>
      <MapLegend kind={legend} />
    </div>
  );
}

const noop = () => undefined;

/** The map's legend: colour ramp with its meaning in words (never colour alone). */
function MapLegend({ kind }: { kind: "count" | "density" }) {
  return (
    <p className="flex items-center gap-2 text-xs">
      <span aria-hidden className="inline-block h-3 w-16" style={{ background: "linear-gradient(90deg, #CFE3E2, #1B4F4D)" }} />
      {kind === "count" ? t("an.legend.count") : t("an.legend.density")}
      <span aria-hidden className="ml-3 inline-block h-0.5 w-6 bg-navy" />
      {t("an.legend.region")}
    </p>
  );
}

export function TableView({ data }: { data: Data }) {
  const tables = (data.tables ?? []) as { title: string; headers: string[]; rows: string[][] }[];
  return (
    <div className="flex flex-col gap-6">
      {tables.map((tb) => (
        <table key={tb.title} className="w-full border-collapse text-sm">
          <caption className="mb-1 text-left font-semibold text-navy">{tb.title}</caption>
          <thead>
            <tr className="border-b border-divider text-left">
              {tb.headers.map((h, i) => (
                <th key={`${h}-${i}`} scope="col" className="p-1">{h}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {tb.rows.length === 0 ? (
              <tr><td className="p-1" colSpan={tb.headers.length}>{t("an.noRows")}</td></tr>
            ) : (
              tb.rows.map((r, i) => (
                <tr key={i} className="border-b border-divider">
                  {r.map((c, j) => (j === 0 ? <th key={j} scope="row" className="p-1 text-left font-normal">{c}</th> : <td key={j} className="p-1 tabular-nums">{c}</td>))}
                </tr>
              ))
            )}
          </tbody>
        </table>
      ))}
    </div>
  );
}

export function ExplanationView({ meta }: { meta: Data }) {
  return (
    <div className="flex max-w-3xl flex-col gap-3 text-sm">
      <p>{meta.explanation}</p>
      <p><span className="font-semibold">{t("an.method")}:</span> {meta.method}</p>
      <ul className="list-disc pl-5">
        {Object.entries(meta.method_parameters ?? {}).map(([k, v]) => (v === null ? null : <li key={k}>{k.replace(/_/g, " ")}: {String(v)}</li>))}
      </ul>
      <p><span className="font-semibold">{t("an.quality")}:</span> {meta.data_quality_note}</p>
      <h3 className="font-semibold text-navy">{t("an.caveats")}</h3>
      <ul className="list-disc pl-5">
        {(meta.caveats as string[]).map((c) => <li key={c}>{c}</li>)}
      </ul>
    </div>
  );
}

function Kpis({ k }: { k: Data }) {
  const items: [string, string][] = [
    [t("an.kpi.selected"), n(k.selected)],
    [t("an.kpi.change"), k.change_pct === null ? "—" : `${k.change_pct > 0 ? "▲" : k.change_pct < 0 ? "▼" : "＝"} ${Math.abs(k.change_pct)}% (${n(k.comparison_selected)})`],
    [t("an.kpi.usable"), k.usable_location_pct === null ? "—" : `${k.usable_location_pct}%`],
    [t("an.kpi.review"), n(k.open_review_items)],
  ];
  return (
    <dl className="grid grid-cols-2 gap-3 md:grid-cols-4">
      {items.map(([label, value]) => (
        <div key={label} className="rounded-panel border border-divider bg-surface p-3">
          <dt className="text-xs">{label}</dt>
          <dd className="text-xl font-semibold tabular-nums">{value}</dd>
        </div>
      ))}
    </dl>
  );
}

export function SectionChart({ section, data, meta }: { section: SectionId; data: Data; meta: Data }) {
  // Every chart states its count, time basis, period and freshness (spec: every chart, not only the page).
  const fa = meta.filters_applied as Record<string, string>;
  const subtitle = t("an.chart.subtitle", {
    count: Number(meta.record_count).toLocaleString("en-IN"),
    basis: fa.time_basis ?? "",
    period: String(fa.period).replace("/", " to "),
    refreshed: meta.data_freshness ? formatIst(String(meta.data_freshness)) : "—",
  });
  const charts = useMemo((): { title: string; option: EChartsCoreOption; height?: number }[] => {
    if (section === "overview") {
      return [
        { title: t("an.chart.trend", { grain: data.trend.grain }), option: timeOption([line(data.trend.points, t("an.records"))]) },
        { title: t("an.chart.categories"), option: bars(data.categories) },
        { title: t("an.chart.quality"), option: bars(data.quality.map((q: Data) => ({ label: q.status, count: q.count })), NAVY), height: 180 },
      ];
    }
    if (section === "case_trends") {
      const cats = (data.by_category as Data[]).slice(0, 6);
      const max = Math.max(1, ...data.day_hour.counts.flat());
      const days = (data.calendar as Data[]).map((p) => [p.bucket, p.count]);
      const out: { title: string; option: EChartsCoreOption; height?: number }[] = [
        { title: t("an.chart.series", { grain: data.grain }), option: timeOption([line(data.series, t("an.records"))]) },
        { title: t("an.chart.byCategory"), option: timeOption(cats.map((c, i) => line(c.points, c.label, i + 1))) },
        {
          title: t("an.chart.dayHour"),
          height: 260,
          option: {
            grid: { left: 48, right: 16, top: 8, bottom: 64 },
            xAxis: { type: "category", data: data.day_hour.hours.map((h: number) => `${h}`) },
            yAxis: { type: "category", data: data.day_hour.days },
            visualMap: { min: 0, max, orient: "horizontal", left: "center", bottom: 0, inRange: { color: ["#F1F4F7", NAVY] } },
            series: [{ type: "heatmap", data: data.day_hour.counts.flatMap((row: number[], d: number) => row.map((v, h) => [h, d, v])) }],
          },
        },
        {
          title: t("an.chart.calendar"),
          height: 200,
          option: days.length
            ? {
                visualMap: { min: 0, max: Math.max(1, ...days.map((d) => Number(d[1]))), orient: "horizontal", left: "center", bottom: 0, inRange: { color: ["#F1F4F7", TEAL] } },
                calendar: { range: [days[0]?.[0], days[days.length - 1]?.[0]], top: 24, left: 40, right: 16, cellSize: ["auto", 14] },
                series: [{ type: "heatmap", coordinateSystem: "calendar", data: days }],
              }
            : {},
        },
        { title: t("an.chart.delay"), option: bars((data.delay as Data[]).map((d) => ({ label: d.bucket.replace(/_/g, " "), count: d.count })).reverse(), NAVY), height: 220 },
      ];
      if (data.status.available) {
        const pts = data.status.points as Data[];
        out.push({
          title: t("an.chart.status"),
          option: timeOption(["active", "detected", "closed"].map((k, i) => line(pts.map((p) => ({ bucket: p.bucket, count: p[k] })), t(`details.${k}` as "details.active"), i))),
        });
      }
      return out;
    }
    if (section === "location") {
      const feats = ((data.map?.features ?? []) as Data[]).slice(0, 15).map((f) => ({ label: f.properties.key ?? f.properties.name, count: f.properties.count }));
      const persist = (data.persistence ?? []) as Data[];
      const kinds = ["IN_BOTH", "CURRENT_ONLY", "COMPARISON_ONLY"].map((k) => ({ label: t(`an.persist.${k}` as MessageKey), count: persist.filter((p) => p.status === k).length }));
      return [
        { title: t("an.chart.topUnits"), option: bars(feats) },
        ...(persist.length ? [{ title: t("an.chart.persistence"), option: bars(kinds, NAVY), height: 180 }] : []),
        { title: t("an.chart.repeat"), option: bars((data.repeat_locations as Data[]).map((r) => ({ label: r.locality, count: r.count })), NAVY) },
      ];
    }
    if (section === "comparison") {
      return [
        { title: t("an.chart.ab"), option: timeOption([line(data.a.series, `A: ${data.a.label}`, 0), line(data.b.series, `B: ${data.b.label}`, 1)]) },
        {
          title: t("an.chart.abCategories"),
          option: {
            grid: { left: 180, right: 24, top: 32, bottom: 24 },
            legend: { top: 0 },
            xAxis: { type: "value" },
            yAxis: { type: "category", data: (data.table as Data[]).slice(0, 12).map((r) => r.label).reverse() },
            series: [
              { type: "bar", name: "A", data: (data.table as Data[]).slice(0, 12).map((r) => r.a).reverse(), itemStyle: { color: NAVY } },
              { type: "bar", name: "B", data: (data.table as Data[]).slice(0, 12).map((r) => r.b).reverse(), itemStyle: { color: TEAL } },
            ],
          },
        },
      ];
    }
    if (section === "data_quality") {
      return [
        { title: t("an.chart.dqMeasures"), option: bars((data.measures as Data[]).map((m) => ({ label: m.label, count: m.count })), NAVY) },
        { title: t("an.chart.geocode"), option: bars((data.geocode_methods as Data[]).map((m) => ({ label: m.method, count: m.count }))), height: 200 },
        { title: t("an.chart.confidence"), option: bars((data.confidence as Data[]).map((m) => ({ label: m.band, count: m.count })), NAVY), height: 180 },
      ];
    }
    return [];
  }, [section, data]);
  return (
    <div className="flex flex-col gap-6">
      {section === "overview" ? <Kpis k={data.kpis} /> : null}
      {section === "comparison" ? (
        <p className="text-sm font-semibold">
          A {n(data.a.count)} · B {n(data.b.count)} · {t("an.difference")} {n(data.difference.difference)}
          {data.difference.change_pct !== null ? ` (${data.difference.change_pct}%)` : ""} · {data.rate.label}
        </p>
      ) : null}
      {section === "location" && !data.kde.available ? <p className="text-sm">{data.kde.reason}</p> : null}
      {section === "location" ? <p className="text-sm">{data.road_segments.reason}</p> : null}
      {section === "case_trends" && !data.status.available ? <p className="text-sm">{data.status.reason}</p> : null}
      {charts.map((c) => <EChart key={c.title} title={c.title} subtitle={subtitle} option={c.option} height={c.height} />)}
      {section === "overview" ? (
        <section aria-labelledby="open-reviews" className="text-sm">
          <h3 id="open-reviews" className="font-semibold text-navy">{t("an.openReviews")}</h3>
          {(data.open_reviews as Data[]).length ? (
            <ul className="list-disc pl-5">
              {(data.open_reviews as Data[]).map((o) => (
                <li key={`${o.issue_type}-${o.severity}`}>{t(`an.issue.${o.issue_type}` as MessageKey)}{o.severity !== "-" ? ` (${String(o.severity).toLowerCase()})` : ""}: {n(o.count)}</li>
              ))}
            </ul>
          ) : <p>{t("an.noRows")}</p>}
          <Link className="underline" href="/review">{t("an.toReviewQueue")}</Link>
        </section>
      ) : null}
      {section === "data_quality" ? (
        <section aria-labelledby="feeds" className="text-sm">
          <h3 id="feeds" className="font-semibold text-navy">{t("an.feeds")}</h3>
          <table className="w-full border-collapse">
            <thead><tr className="border-b border-divider text-left"><th scope="col" className="p-1">{t("an.feed.source")}</th><th scope="col" className="p-1">{t("an.feed.status")}</th><th scope="col" className="p-1">{t("an.feed.health")}</th><th scope="col" className="p-1">{t("an.feed.last")}</th></tr></thead>
            <tbody>
              {(data.feeds as Data[]).map((f) => (
                <tr key={f.code} className="border-b border-divider"><th scope="row" className="p-1 text-left font-normal">{f.name}</th><td className="p-1">{f.approved_status}</td><td className="p-1">{f.health ?? "—"}</td><td className="p-1">{f.last_batch_at ? formatIst(String(f.last_batch_at)) : "—"}</td></tr>
              ))}
            </tbody>
          </table>
          <Link className="underline" href="/review">{t("an.toDqQueue")}</Link>
        </section>
      ) : null}
    </div>
  );
}

/** Which views a section offers (a time-only analysis has no meaningful map). */
export function viewsFor(section: SectionId): View[] {
  if (section === "case_trends" || section === "data_quality") return ["chart", "table", "explanation"];
  return ["map", "chart", "table", "explanation"];
}

export function SectionMap({ section, data, region, regionShape }: { section: SectionId; data: Data; region: string; regionShape: FC | null }) {
  const density = useMemo(() => recount(data.kde?.map ?? null), [data]);
  if (section === "comparison") {
    return (
      <div className="grid gap-3 md:grid-cols-2">
        <div><p className="text-sm font-semibold">A: {data.a.label}</p><MapView region={`${region}-a`} fc={data.a.map} label={`A: ${data.a.label}`} regionShape={data.a.region === region ? regionShape : null} /></div>
        <div><p className="text-sm font-semibold">B: {data.b.label}</p><MapView region={`${region}-b`} fc={data.b.map} label={`B: ${data.b.label}`} regionShape={data.b.region === region ? regionShape : null} /></div>
      </div>
    );
  }
  if (section === "location" && data.kde?.available) {
    return (
      <div className="grid gap-3 md:grid-cols-2">
        <div><p className="text-sm font-semibold">{t("an.map.counts")}</p><MapView region={region} fc={data.map} label={t("an.map.counts")} regionShape={regionShape} /></div>
        <div><p className="text-sm font-semibold">{t("an.map.kde")}</p><MapView region={`${region}-kde`} fc={density} label={t("an.map.kde")} regionShape={regionShape} legend="density" /></div>
      </div>
    );
  }
  return (
    <div className="flex flex-col gap-2">
      {section === "location" ? <p className="text-sm">{t("an.pointsOnHomeMap")} <Link className="underline" href="/">{t("nav.home")}</Link></p> : null}
      <MapView region={region} fc={data.map} label={t("an.map.counts")} regionShape={regionShape} />
    </div>
  );
}

/** The density layer reuses the count colour ramp: scale densities to 1..40 for display. */
function recount(fc: FC | null): FC | null {
  if (!fc) return null;
  const max = Math.max(1e-9, ...fc.features.map((f) => Number(f.properties?.density_per_km2 ?? 0)));
  return { type: "FeatureCollection", features: fc.features.map((f) => ({ ...f, properties: { ...f.properties, count: 1 + 39 * (Number(f.properties?.density_per_km2 ?? 0) / max) } })) };
}
