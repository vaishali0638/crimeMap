"use client";

import { QueryClient, QueryClientProvider, keepPreviousData, useQuery } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";
import { AiAnalysis } from "@/components/analytics/AiAnalysis";
import { API_SECTION, type AnalyticsFilters, SECTIONS, type SectionId, analysisUrl, defaultAnalytics, fromQuery, toParams, toQuery, validate } from "@/lib/analytics/query";
import { ApiError, type FilterMetadata, getJson } from "@/lib/map/client";
import { formatIst } from "@/lib/dq";
import { t } from "@/lib/i18n";
import type { MessageKey } from "@/messages/en";
import { EvidenceAction, ExportAction, NoteAction, PatternReviewAction } from "./Actions";
import type { FC } from "../map/MapCanvas";
import { ExplanationView, SectionChart, SectionMap, TableView, type View, viewsFor } from "./SectionViews";

type Data = Record<string, any>; // eslint-disable-line @typescript-eslint/no-explicit-any
type Props = { permissions: string[]; csrfToken: string; dqOnly: boolean; initialSection: SectionId; initialQuery: string };
type InnerProps = Props & { firstRegion: string; regions: string[] };

const control = "min-h-11 rounded-control border border-control-border bg-surface px-2 text-sm";
const label = "flex flex-col gap-1 text-xs font-medium";

export function errorText(e: unknown): string {
  if (e instanceof ApiError) {
    if (e.status === 401) return t("map.status.signIn");
    if (e.status === 403 && e.slug === "csrf-failed") return t("an.err.csrf");
    if (e.status === 403) return t("an.notPermitted");
    if (e.status === 413 || e.status === 414) return t("an.err.tooLarge");
    if (e.status === 503) return e.explanation ?? t("an.err.unavailable");
    if (e.explanation) return t("map.status.rejected", { reason: e.explanation });
  }
  return t("map.status.error");
}

function ErrorNotice({ error }: { error: unknown }) {
  const signIn = error instanceof ApiError && error.status === 401;
  return (
    <p role="alert">
      {errorText(error)}{" "}
      {signIn ? <a className="underline" href={`/bff/auth/login?returnTo=${encodeURIComponent(window.location.pathname + window.location.search)}`}>{t("an.export.signIn")}</a> : null}
    </p>
  );
}

const FILTER_LABELS: Record<string, MessageKey> = {
  region: "map.region", period: "map.period", time_basis: "map.timeBasis", record_type: "map.recordType", station: "map.station",
  beat: "map.beat", category: "map.category", status: "map.status", locality: "an.locality", time_of_day: "an.timeRange",
  min_location_confidence: "map.confidence", comparison_period: "an.comparisonPeriod", compare: "an.compare",
};

function plain(v: unknown): string {
  if (Array.isArray(v)) return v.join(", ");
  if (v && typeof v === "object") return Object.values(v as Record<string, unknown>).map(String).join(" vs ");
  return String(v).replace("/", " to ").replace(/_/g, " ");
}

function Header({ meta, loading }: { meta: Data; loading: boolean }) {
  const reasons = Object.entries(meta.excluded_reasons ?? {}) as [string, number][];
  return (
    <header className="flex flex-col gap-2 border-b border-divider pb-3" aria-busy={loading}>
      <div className="flex flex-wrap items-baseline gap-3">
        <h2 className="text-xl font-semibold text-navy">{meta.title}</h2>
        <span className="rounded-control border border-saffron-strong px-2 text-xs font-semibold text-saffron-strong">
          {meta.classification}{meta.environment === "PRODUCTION" ? "" : ` · ${t("an.demoBadge")}`}
        </span>
      </div>
      <p className="text-sm">{meta.explanation}</p>
      <p className="text-sm"><span className="font-semibold">{t("an.filters")}:</span> {Object.entries(meta.filters_applied).map(([k, v]) => `${FILTER_LABELS[k] ? t(FILTER_LABELS[k]) : k.replace(/_/g, " ")}: ${plain(v)}`).join(" · ")}</p>
      <p className="text-sm">
        <span className="font-semibold">{t("an.selected", { count: Number(meta.record_count).toLocaleString("en-IN") })}</span>
        {" · "}
        {t("an.excluded", { count: Number(meta.excluded_count).toLocaleString("en-IN") })}
        {reasons.length ? ` (${reasons.map(([k, v]) => `${t(`an.reason.${k}` as MessageKey)} ${v.toLocaleString("en-IN")}`).join(", ")})` : ""}
        {meta.suppressed ? ` · ${t("an.notCounted", { rule: meta.suppressed })}` : ""}
      </p>
      <p className="text-sm"><span className="font-semibold">{t("an.quality")}:</span> {meta.data_quality_note}</p>
      <p className="text-xs">
        {t("an.method")}: {meta.method} · {meta.data_freshness ? t("map.status.refreshed", { time: formatIst(meta.data_freshness) }) : ""}
      </p>
    </header>
  );
}

function FilterBar({ draft, setDraft, meta, section, canStatus, onApply, onReset, problems }: {
  draft: AnalyticsFilters;
  setDraft: (p: Partial<AnalyticsFilters>) => void;
  meta: FilterMetadata | undefined;
  section: SectionId;
  canStatus: boolean;
  onApply: () => void;
  onReset: () => void;
  problems: string[];
}) {
  const hours = Array.from({ length: 24 }, (_, h) => String(h));
  const beats = (meta?.beats ?? []).filter((b) => !draft.station || b.station_code === draft.station);
  return (
    <form aria-label={t("an.filterBar")} className="flex flex-wrap items-end gap-2 rounded-panel border border-divider bg-surface-2 p-3" onSubmit={(e) => { e.preventDefault(); onApply(); }}>
      <label className={label}>{t("map.region")}
        <select className={control} value={draft.region} onChange={(e) => setDraft({ region: e.target.value, station: "", beat: "", a: "", b: "" })}>
          {(meta?.regions ?? [{ code: draft.region, name: draft.region }]).map((r) => <option key={r.code} value={r.code}>{r.name}</option>)}
        </select>
      </label>
      <label className={label}>{t("map.station")}
        <select className={control} value={draft.station} onChange={(e) => setDraft({ station: e.target.value, beat: "" })}>
          <option value="">{t("map.allStations")}</option>
          {(meta?.stations ?? []).map((s) => <option key={s.code} value={s.code}>{s.name}</option>)}
        </select>
      </label>
      <label className={label}>{t("map.beat")}
        <select className={control} value={draft.beat} onChange={(e) => setDraft({ beat: e.target.value })}>
          <option value="">{t("map.allBeats")}</option>
          {beats.map((b) => <option key={b.code} value={b.code}>{b.code}</option>)}
        </select>
      </label>
      <label className={label}>{t("an.locality")}
        <input className={`${control} w-36`} value={draft.locality} maxLength={100} onChange={(e) => setDraft({ locality: e.target.value })} />
      </label>
      <label className={label}>{t("map.category")}
        <select className={control} value={draft.category} onChange={(e) => setDraft({ category: e.target.value })}>
          <option value="">{t("map.allCategories")}</option>
          {(meta?.categories ?? []).map((c) => <option key={c.code} value={c.code}>{c.label}</option>)}
        </select>
      </label>
      <label className={label}>{t("map.from")}<input type="date" className={control} value={draft.periodFrom} onChange={(e) => setDraft({ periodFrom: e.target.value })} /></label>
      <label className={label}>{t("map.to")}<input type="date" className={control} value={draft.periodTo} onChange={(e) => setDraft({ periodTo: e.target.value })} /></label>
      <fieldset className="flex items-end gap-1">
        <legend className="text-xs font-medium">{t("an.timeRange")}</legend>
        <select aria-label={t("an.hourFrom")} className={control} value={draft.hourFrom} onChange={(e) => setDraft({ hourFrom: e.target.value })}>
          <option value="">{t("map.any")}</option>
          {hours.map((h) => <option key={h} value={h}>{h.padStart(2, "0")}:00</option>)}
        </select>
        <select aria-label={t("an.hourTo")} className={control} value={draft.hourTo} onChange={(e) => setDraft({ hourTo: e.target.value })}>
          <option value="">{t("map.any")}</option>
          {hours.map((h) => <option key={h} value={h}>{h.padStart(2, "0")}:59</option>)}
        </select>
      </fieldset>
      <label className={label}>{t("map.recordType")}
        <select className={control} value={draft.recordType} onChange={(e) => setDraft({ recordType: e.target.value as AnalyticsFilters["recordType"] })}>
          {["CASE", "ERSS_CALL", "ACCIDENT", "TRAFFIC_EVENT"].map((r) => <option key={r} value={r}>{r.replace("_", " ")}</option>)}
          <option value="COMBINED">{t("an.combined")}</option>
        </select>
      </label>
      <label className={label}>{t("map.timeBasis")}
        <select className={control} value={draft.timeBasis} onChange={(e) => setDraft({ timeBasis: e.target.value as AnalyticsFilters["timeBasis"] })}>
          <option value="OCCURRENCE">{t("map.basis.OCCURRENCE")}</option>
          <option value="REPORTED">{t("map.basis.REPORTED")}</option>
        </select>
      </label>
      {canStatus ? (
        <label className={label}>{t("map.status")}
          <select className={control} value={draft.status} onChange={(e) => setDraft({ status: e.target.value })}>
            <option value="">{t("map.any")}</option>
            {[...new Map((meta?.statuses ?? []).filter((s) => draft.recordType === "COMBINED" || s.record_type === draft.recordType).map((s) => [s.code, s])).values()].map((s) => <option key={s.code} value={s.code}>{s.label}</option>)}
          </select>
        </label>
      ) : null}
      <label className={label}>{t("map.confidence")}
        <select className={control} value={draft.minConfidence} onChange={(e) => setDraft({ minConfidence: e.target.value as AnalyticsFilters["minConfidence"] })}>
          {(["0", "0.4", "0.8"] as const).map((v) => <option key={v} value={v}>{t(`map.confidence.${v}` as MessageKey)}</option>)}
        </select>
      </label>
      {section === "case_trends" ? (
        <label className={label}>{t("an.grain")}
          <select className={control} value={draft.grain} onChange={(e) => setDraft({ grain: e.target.value as AnalyticsFilters["grain"] })}>
            <option value="">{t("an.grain.auto")}</option>
            <option value="day">{t("an.grain.day")}</option>
            <option value="week">{t("an.grain.week")}</option>
            <option value="month">{t("an.grain.month")}</option>
          </select>
        </label>
      ) : null}
      <label className={label}>{t("an.comparisonPeriod")}
        <select className={control} value={draft.comparison} onChange={(e) => setDraft({ comparison: e.target.value as AnalyticsFilters["comparison"] })}>
          <option value="PREVIOUS">{t("an.cmp.previous")}</option>
          <option value="YEAR">{t("an.cmp.year")}</option>
          <option value="NONE">{t("an.cmp.none")}</option>
        </select>
      </label>
      {section === "location" ? (
        <>
          <label className={label}>{t("an.unit")}
            <select className={control} value={draft.unit} onChange={(e) => setDraft({ unit: e.target.value as AnalyticsFilters["unit"] })}>
              <option value="HEX">{t("an.unit.hex")}</option>
              <option value="BEAT">{t("an.unit.beat")}</option>
              <option value="STATION">{t("an.unit.station")}</option>
            </select>
          </label>
          {draft.unit === "HEX" ? (
            <label className={label}>{t("map.hexSize")}
              <select className={control} value={draft.hexSizeM} onChange={(e) => setDraft({ hexSizeM: e.target.value as AnalyticsFilters["hexSizeM"] })}>
                {["250", "500", "1000", "2000"].map((s) => <option key={s} value={s}>{s} m</option>)}
              </select>
            </label>
          ) : null}
        </>
      ) : null}
      {section === "comparison" ? (
        <>
          <label className={label}>{t("an.compare")}
            <select className={control} value={draft.compare} onChange={(e) => setDraft({ compare: e.target.value as AnalyticsFilters["compare"], a: "", b: "" })}>
              {["PERIOD", "YEAR", "REGION", "STATION", "BEAT", "CATEGORY"].map((k) => <option key={k} value={k}>{t(`an.compare.${k}` as MessageKey)}</option>)}
            </select>
          </label>
          {["REGION", "STATION", "BEAT", "CATEGORY"].includes(draft.compare) ? (
            (["a", "b"] as const).map((side) => {
              const options =
                draft.compare === "REGION" ? (meta?.regions ?? []).map((r) => ({ code: r.code, name: r.name }))
                : draft.compare === "STATION" ? (meta?.stations ?? []).map((s) => ({ code: s.code, name: s.name }))
                : draft.compare === "BEAT" ? (meta?.beats ?? []).map((b) => ({ code: b.code, name: b.code }))
                : (meta?.categories ?? []).map((c) => ({ code: c.code, name: c.label }));
              return (
                <label key={side} className={label}>{side.toUpperCase()}
                  <select className={control} value={draft[side]} onChange={(e) => setDraft({ [side]: e.target.value })}>
                    <option value="">—</option>
                    {options.map((o) => <option key={o.code} value={o.code}>{o.name}</option>)}
                  </select>
                </label>
              );
            })
          ) : null}
        </>
      ) : null}
      <div className="flex gap-2">
        <button type="submit" className="min-h-11 rounded-control bg-navy px-4 text-sm font-semibold text-on-navy">{t("map.apply")}</button>
        <button type="button" className="min-h-11 rounded-control border border-control-border px-4 text-sm" onClick={onReset}>{t("map.reset")}</button>
      </div>
      {draft.hourFrom !== "" && draft.hourTo !== "" && Number(draft.hourFrom) > Number(draft.hourTo) ? <p className="w-full text-xs">{t("an.hoursWrap")}</p> : null}
      {problems.length ? <p role="alert" className="w-full text-sm text-saffron-strong">{problems.join(" ")}</p> : null}
    </form>
  );
}

function Workbench({ permissions, csrfToken, dqOnly, initialSection, initialQuery, firstRegion, regions }: InnerProps) {
  const [restored] = useState(() => fromQuery(initialQuery, firstRegion, regions));
  const allowed = (s: SectionId | null) => (s && !(s === "comparison" && !permissions.includes("analytics.compare")) ? s : null);
  const [section, setSection] = useState<SectionId>(dqOnly ? "data_quality" : (allowed(restored.section) ?? initialSection));
  const [draft, setDraftState] = useState<AnalyticsFilters>(restored.filters);
  const [applied, setApplied] = useState<AnalyticsFilters>(restored.filters);
  // The address always describes the analysis on screen (re-sign-in and shared links reopen it).
  useEffect(() => {
    window.history.replaceState(null, "", analysisUrl(section, applied));
  }, [section, applied]);
  const tabs = useRef<(HTMLButtonElement | null)[]>([]);
  const [view, setView] = useState<View>("chart");
  const [problems, setProblems] = useState<string[]>([]);
  const setDraft = (p: Partial<AnalyticsFilters>) => setDraftState((d) => ({ ...d, ...p }));
  const meta = useQuery({
    queryKey: ["meta", draft.region],
    queryFn: ({ signal }) => getJson<FilterMetadata>(`filters/metadata?region=${encodeURIComponent(draft.region)}`, signal),
    retry: false,
    placeholderData: keepPreviousData,
  });
  const apiSection = API_SECTION[section];
  const params = toParams(applied, section);
  const query = apiSection ? `analytics/${apiSection}?${toQuery(params)}` : null;
  const result = useQuery({
    queryKey: ["analytics", apiSection, query],
    queryFn: ({ signal }) => getJson<{ data: Data; meta: Data }>(query as string, signal),
    enabled: query !== null,
    retry: false,
    staleTime: 60_000, // returning to a section within a minute reuses the result (and its audit event)
    // Keep the previous result while new filters load, but never another section's (its data has a
    // different shape).
    placeholderData: (previous, previousQuery) => (previousQuery?.queryKey[1] === apiSection ? previous : undefined),
  });
  const regionShape = useQuery({
    queryKey: ["boundaries", "REGION", applied.region],
    queryFn: ({ signal }) => getJson<FC>(`boundaries?level=REGION&region=${encodeURIComponent(applied.region)}`, signal),
    staleTime: 10 * 60_000,
  });
  // Actions act on the parameters of the result on screen: never while a newer one is loading.
  const stale = result.isPlaceholderData || result.isFetching;
  const views = viewsFor(section);
  const activeView = views.includes(view) ? view : (views[0] as View);
  const canExport = { csv: permissions.includes("report.export.aggregate_csv"), pdf: permissions.includes("report.export.pdf") };
  const canPattern = permissions.includes("pattern_review.create");
  const cells = section === "location" && applied.unit === "HEX" && result.data && !stale
    ? ((result.data.data.map?.features ?? []) as Data[]).map((f) => ({ key: f.properties.key as string, count: f.properties.count as number })).sort((a, b) => b.count - a.count)
    : [];

  return (
    // Below md the section menu stacks above the workbench (reflow, WCAG 1.4.10).
    <section aria-labelledby="an-title" className="flex flex-col gap-6 md:flex-row">
      <nav aria-label={t("an.menu")} className="w-full shrink-0 md:w-56">
        <h1 id="an-title" className="mb-3 text-2xl font-semibold text-navy">{t("an.title")}</h1>
        <ul className="flex flex-col gap-1">
          {SECTIONS.map((s) => {
            const unavailable = s === "civic" || (s === "ai" && !permissions.includes("ai.insight.read")) || (dqOnly && s !== "data_quality") || (s === "comparison" && !permissions.includes("analytics.compare"));
            return (
              <li key={s}>
                <button
                  type="button"
                  aria-current={section === s ? "page" : undefined}
                  disabled={unavailable && s !== "civic" && s !== "ai"}
                  onClick={() => {
                    setSection(s);
                    setView("chart");
                  }}
                  className="flex min-h-11 w-full items-center rounded-control px-3 text-left text-sm aria-[current=page]:bg-navy aria-[current=page]:font-semibold aria-[current=page]:text-on-navy disabled:text-ink/50"
                >
                  {t(`an.section.${s}` as MessageKey)}
                </button>
              </li>
            );
          })}
        </ul>
      </nav>
      <div className="flex min-w-0 flex-1 flex-col gap-4">
        <FilterBar
          draft={draft}
          setDraft={setDraft}
          meta={meta.data}
          section={section}
          canStatus={permissions.includes("incident.summary.read")}
          problems={problems}
          onApply={() => {
            const p = validate(draft, section);
            setProblems(p);
            if (!p.length) setApplied(draft);
          }}
          onReset={() => {
            const fresh = defaultAnalytics(draft.region);
            setDraftState(fresh);
            setApplied(fresh);
            setProblems([]);
          }}
        />
        {section === "civic" ? <p className="rounded-panel border border-divider p-4">{t("an.civicOff")}</p> : null}
        {section === "ai" ? (
          !permissions.includes("ai.insight.read") ? (
            <p className="rounded-panel border border-divider p-4">{t("ai.noAccess")}</p>
          ) : dqOnly ? (
            <p className="rounded-panel border border-divider p-4">{t("ai.analysis.dqOnly")}</p>
          ) : (
            <AiAnalysis region={applied.region} canRegistry={permissions.includes("ai.model.read")} />
          )
        ) : null}
        {result.isError ? <ErrorNotice error={result.error} /> : null}
        <p role="status" aria-live="polite" className="sr-only">
          {result.data && !stale ? t("an.loaded", { title: String(result.data.meta.title), count: Number(result.data.meta.record_count).toLocaleString("en-IN") }) : ""}
        </p>
        {apiSection && !result.data && result.isPending ? <p role="status">{t("dash.loading")}</p> : null}
        {apiSection && result.data ? (
          <>
            <Header meta={result.data.meta} loading={result.isFetching} />
            <div className="flex flex-wrap items-center justify-between gap-3">
              <div role="tablist" aria-label={t("an.views")} className="flex gap-1">
                {views.map((v, i) => (
                  <button
                    key={v}
                    ref={(el) => {
                      tabs.current[i] = el;
                    }}
                    type="button"
                    role="tab"
                    id={`an-tab-${v}`}
                    aria-controls="an-panel"
                    aria-selected={activeView === v}
                    tabIndex={activeView === v ? 0 : -1}
                    onClick={() => setView(v)}
                    onKeyDown={(e) => {
                      const move = e.key === "ArrowRight" ? 1 : e.key === "ArrowLeft" ? -1 : 0;
                      const to = e.key === "Home" ? 0 : e.key === "End" ? views.length - 1 : move ? (i + move + views.length) % views.length : -1;
                      if (to < 0) return;
                      e.preventDefault();
                      setView(views[to] as View);
                      tabs.current[to]?.focus();
                    }}
                    className="min-h-11 rounded-control border border-control-border px-3 text-sm aria-selected:bg-navy aria-selected:text-on-navy"
                  >
                    {t(`an.view.${v}` as MessageKey)}
                  </button>
                ))}
              </div>
              <div className="flex flex-wrap gap-2">
                <ExportAction key={`export-${query}`} section={section} params={params} csrfToken={csrfToken} permitted={canExport} stale={stale} />
                <NoteAction key={`note-${query}`} section={section} params={params} csrfToken={csrfToken} stale={stale} />
                <EvidenceAction key={`evidence-${query}`} section={section} params={params} csrfToken={csrfToken} stale={stale} />
                {section === "location" && canPattern ? (
                  <PatternReviewAction key={`pattern-${query}`} section={section} params={params} csrfToken={csrfToken} cells={cells} stale={stale} comparisonOff={applied.comparison === "NONE"} />
                ) : null}
              </div>
            </div>
            <div role="tabpanel" id="an-panel" aria-labelledby={`an-tab-${activeView}`} tabIndex={0}>
              {activeView === "map" ? <SectionMap section={section} data={result.data.data} region={applied.region} regionShape={regionShape.data ?? null} /> : null}
              {activeView === "chart" ? <SectionChart section={section} data={result.data.data} meta={result.data.meta} /> : null}
              {activeView === "table" ? <TableView data={result.data.data} /> : null}
              {activeView === "explanation" ? <ExplanationView meta={result.data.meta} /> : null}
            </div>
            <ul className="list-disc pl-5 text-xs">
              {(result.data.meta.caveats as string[]).map((c) => <li key={c}>{c}</li>)}
            </ul>
          </>
        ) : null}
      </div>
    </section>
  );
}

/** Which regions may this user see? The workbench starts from the first one, never a fixed region. */
function FirstRegion(props: Props) {
  const initial = useQuery({
    queryKey: ["meta-initial"],
    queryFn: ({ signal }) => getJson<FilterMetadata>("filters/metadata", signal),
    retry: false,
    staleTime: Infinity,
  });
  if (initial.isError) return <ErrorNotice error={initial.error} />;
  if (!initial.data) return <p role="status">{t("dash.loading")}</p>;
  return <Workbench {...props} firstRegion={initial.data.region} regions={initial.data.regions.map((x) => x.code)} />;
}

export function AnalyticsWorkbench(props: Props) {
  const [client] = useState(() => new QueryClient({ defaultOptions: { queries: { refetchOnWindowFocus: false } } }));
  return (
    <QueryClientProvider client={client}>
      <FirstRegion {...props} />
    </QueryClientProvider>
  );
}
