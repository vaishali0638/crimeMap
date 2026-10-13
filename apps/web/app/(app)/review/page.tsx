import Link from "next/link";
import { DqIssueActions, type DqActionLabels } from "@/components/DqIssueActions";
import type { components } from "@crimemap/types";
import { OpsReviewActions } from "@/components/OpsReviewActions";
import { apiFetch, type DqSummary, type Issue, type IssuePage, type Me } from "@/lib/api";
import type { Session } from "@/lib/session";
import type { MessageKey } from "@/messages/en";
import { requireUser } from "@/lib/current-user";
import {
  ISSUE_TYPES,
  REGIONS,
  SEVERITIES,
  STATES,
  actionsFor,
  filterParams,
  formatIst,
  issueLabel,
  queueQuery,
  severityLabel,
  stateLabel,
} from "@/lib/dq";
import { t, tn } from "@/lib/i18n";
import { QueueFilters, QueueTable, TabNav, day } from "@/components/workflow/View";
import { AI_LABEL, type InsightSummary, placeTimeAi } from "@/lib/ai";
import { TABS, type QueueItem, type WorkflowQueue as WorkflowQueueName, chooseTab, isWorkflowQueue, queueFilters } from "@/lib/workflow";

export const dynamic = "force-dynamic";

type SearchParams = Promise<Record<string, string | string[] | undefined>>;

function first(value: string | string[] | undefined): string {
  return (Array.isArray(value) ? value[0] : value) ?? "";
}

function Select({ name, label, value, options }: { name: string; label: string; value: string; options: { value: string; label: string }[] }) {
  return (
    <label className="flex flex-col gap-1 text-sm font-medium">
      {label}
      <select name={name} defaultValue={value} className="min-h-11 rounded-control border border-control-border bg-surface px-2">
        {options.map((o) => (
          <option key={o.value} value={o.value}>
            {o.label}
          </option>
        ))}
      </select>
    </label>
  );
}

function Facts({ issue, categoriesShown }: { issue: Issue; categoriesShown: boolean }) {
  if (issue.source) return <p>{issue.source.name}</p>;
  const inc = issue.incident;
  if (!inc) return <p>—</p>;
  const distance = issue.detail.distance_m;
  const category = categoriesShown ? (inc.category_code ?? t("review.fact.unmapped")) : t("review.fact.categoryHidden");
  return (
    <dl className="grid grid-cols-[auto_1fr] gap-x-3 text-sm">
      <dt className="font-medium">{t("review.fact.type")}</dt>
      <dd>
        {inc.record_type} · {category}
      </dd>
      <dt className="font-medium">{t("review.fact.occurred")}</dt>
      <dd>{formatIst(inc.occurrence_start_at)}</dd>
      <dt className="font-medium">{t("review.fact.location")}</dt>
      <dd>
        {inc.location_precision ?? t("review.fact.none")} · {inc.geocode_method ?? "NONE"}
        {inc.location_confidence !== null ? ` · ${t("review.fact.confidence", { value: inc.location_confidence.toFixed(2) })}` : ""}
      </dd>
      {issue.duplicate_of ? (
        <>
          <dt className="font-medium">{t("review.duplicateOf")}</dt>
          <dd>
            {issue.duplicate_of.visible ? issue.duplicate_of.reference : t("review.duplicateHidden")}
            {typeof distance === "number" ? ` (${t("review.distance", { value: distance })})` : ""}
          </dd>
        </>
      ) : null}
    </dl>
  );
}

function Message({ text }: { text: string }) {
  return (
    <section aria-labelledby="page-title" className="max-w-3xl">
      <h1 id="page-title" className="text-2xl font-semibold text-navy">
        {t("review.title")}
      </h1>
      <p className="mt-2">{text}</p>
    </section>
  );
}

type OpsRecord = components["schemas"]["RecordOut"];

/** Operational records that need a reviewer (HM-4): event markers, location corrections. */
async function OpsQueue({ session }: { session: Session }) {
  let records: OpsRecord[] | null = null;
  try {
    const response = await apiFetch(session, "/api/v1/operational-records?review_status=PENDING");
    records = response.ok ? ((await response.json()) as { data: OpsRecord[] }).data : null;
  } catch {
    records = null; // shown as unavailable; the tabs stay usable
  }
  const labels = {
    approve: t("review.approve"),
    reject: t("review.reject"),
    note: t("review.decisionNote"),
    done: t("review.decided"),
    failed: t("review.failed"),
    own: t("review.ownRecord"),
    notAllowed: t("review.notAllowed"),
    alreadyDecided: t("review.alreadyDecided"),
  };
  return (
    <section aria-labelledby="ops-title" className="flex flex-col gap-3">
      <h2 id="ops-title" className="text-xl font-semibold text-navy">{t("review.opsTitle")}</h2>
      {records === null ? (
        <p>{t("wf.unavailable")}</p>
      ) : records.length === 0 ? (
        <p>{t("review.opsEmpty")}</p>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full min-w-[900px] border-collapse text-left text-sm">
            <caption className="sr-only">{t("review.opsTitle")}</caption>
            <thead>
              <tr className="border-b border-control-border">
                <th scope="col" className="p-2">{t("review.opsKind")}</th>
                <th scope="col" className="p-2">{t("review.opsTitleCol")}</th>
                <th scope="col" className="p-2">{t("review.opsWhen")}</th>
                <th scope="col" className="p-2">{t("review.opsWhere")}</th>
                <th scope="col" className="p-2">{t("review.opsReason")}</th>
                <th scope="col" className="p-2">{t("review.opsDecision")}</th>
                <th scope="col" className="p-2">{t("wf.history")}</th>
              </tr>
            </thead>
            <tbody>
              {records.map((r) => (
                <tr key={r.id} className="border-b border-divider align-top">
                  <td className="p-2">{t(`record.kind.${r.kind}` as MessageKey)}</td>
                  <th scope="row" className="p-2 text-left font-normal">{r.title}</th>
                  <td className="p-2">{formatIst(r.occurred_at)}</td>
                  <td className="p-2">{[r.region, r.station, r.locality].filter(Boolean).join(" · ")}</td>
                  <td className="p-2 whitespace-pre-wrap">{r.reason}</td>
                  <td className="p-2"><OpsReviewActions id={r.id} own={r.created_by_me} csrfToken={session.csrfToken} labels={labels} /></td>
                  <td className="p-2"><Link href={`/review/history?type=OPERATIONAL_RECORD&id=${encodeURIComponent(r.id)}`}>{t("wf.historyLink")}</Link></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}

/** AI insights awaiting analyst review (Phase 7): each links to its insight card. */
async function AiQueue({ me, session, params }: { me: Me; session: Session; params: Awaited<SearchParams> }) {
  const type = first(params.type);
  // Data stewards review data-quality insights only (the API applies the same rule).
  const known = placeTimeAi(me) ? ["DQ_ANOMALY", "DUPLICATE", "GEOCODE", "EMERGING_PATTERN", "CLUSTER"] : ["DQ_ANOMALY", "DUPLICATE", "GEOCODE"];
  const query = new URLSearchParams({ state: "PENDING", limit: "200" });
  if (known.includes(type)) query.set("type", type);
  let body: { data: InsightSummary[]; ai_enabled: boolean } | null = null;
  try {
    const response = await apiFetch(session, `/api/v1/ai/insights?${query}`);
    body = response.ok ? ((await response.json()) as { data: InsightSummary[]; ai_enabled: boolean }) : null;
  } catch {
    body = null;
  }
  return (
    <section aria-labelledby="queue-title" className="flex flex-col gap-3">
      <h2 id="queue-title" className="text-xl font-semibold text-navy">{t("wf.tab.ai")}</h2>
      <p className="w-fit rounded-control border-2 border-saffron-strong px-2 py-1 text-sm font-semibold">{AI_LABEL}</p>
      <p className="max-w-4xl text-sm">{t("ai.queue.intro")}</p>
      <p className="text-sm">
        {me.permissions.includes("ai.model.read") ? <><Link href="/ai">{t("ai.models.title")}</Link> · </> : null}
        {me.permissions.includes("ai.insight.review") ? <Link href="/ai/briefing">{t("ai.briefing.title")}</Link> : null}
      </p>
      <form method="get" aria-label={t("review.filters")} className="flex flex-wrap items-end gap-4 rounded-panel border border-divider bg-surface-2 p-4">
        <input type="hidden" name="tab" value="ai" />
        <Select name="type" label={t("ai.queue.type")} value={known.includes(type) ? type : ""} options={[{ value: "", label: t("review.any") }, ...known.map((k) => ({ value: k, label: t(`ai.type.${k}` as MessageKey) }))]} />
        <button type="submit" className="min-h-11 rounded-control bg-navy px-4 text-sm font-semibold text-on-navy">{t("review.apply")}</button>
      </form>
      {body === null ? <p>{t("wf.unavailable")}</p> : !body.ai_enabled ? <p role="status" className="font-semibold">{t("ai.queue.off")}</p> : null}
      {body && body.data.length === 0 ? <p>{t("wf.empty")}</p> : null}
      {body && body.data.length ? (
        <div className="overflow-x-auto">
          <table className="w-full min-w-[960px] border-collapse text-left text-sm">
            <caption className="sr-only">{t("wf.tab.ai")}</caption>
            <thead>
              <tr className="border-b border-control-border">
                {(["item", "confidence", "window", "category", "explanation", "next"] as const).map((c) => <th key={c} scope="col" className="p-2">{t(`ai.col.${c}`)}</th>)}
              </tr>
            </thead>
            <tbody>
              {body.data.map((i) => (
                <tr key={i.id} className="border-b border-divider align-top">
                  <th scope="row" id={`ai-${i.id}`} className="p-2 text-left font-normal">
                    <span className="font-semibold">{t(`ai.type.${i.insight_type}` as MessageKey)}</span>
                    <span className="block">{i.station ?? i.region}</span>
                    <span className="block font-mono text-xs">{i.model}</span>
                  </th>
                  <td className="p-2">{t(`wf.priority.${i.confidence_label}` as MessageKey)}</td>
                  <td className="p-2">{day(i.window_start)} – {day(i.window_end)}</td>
                  <td className="p-2">{i.category}</td>
                  <td className="p-2">{i.explanation}</td>
                  <td className="p-2"><Link href={`/review/insights/${encodeURIComponent(i.id)}`} aria-describedby={`ai-${i.id}`} className="font-semibold">{i.reviewable ? t("ai.queue.review") : t("ai.queue.open")}</Link></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : null}
    </section>
  );
}

/** A Phase 6 workflow queue: pattern reviews, actions, or outcome reviews due. */
async function WorkflowQueue({ session, queue, params }: { session: Session; queue: WorkflowQueueName; params: Awaited<SearchParams> }) {
  const filters = queueFilters(params);
  let body: { data: QueueItem[]; more: boolean } | null = null;
  try {
    const response = await apiFetch(session, `/api/v1/review-queue?queue=${queue}${filters.size ? `&${filters}` : ""}`);
    body = response.ok ? ((await response.json()) as { data: QueueItem[]; more: boolean }) : null;
  } catch {
    body = null; // shown as unavailable; the tabs stay usable
  }
  return (
    <section aria-labelledby="queue-title" className="flex flex-col gap-3">
      <h2 id="queue-title" className="text-xl font-semibold text-navy">{t(`wf.tab.${queue}`)}</h2>
      <p className="max-w-4xl text-sm">{t(`wf.intro.${queue}`)}</p>
      <QueueFilters queue={queue} values={filters} />
      {body === null ? <p>{t("wf.unavailable")}</p> : <QueueTable items={body.data} more={body.more} caption={t(`wf.tab.${queue}`)} />}
    </section>
  );
}

export default async function ReviewPage({ searchParams }: { searchParams: SearchParams }) {
  const { me, session } = await requireUser();
  const params = await searchParams;
  const tab = chooseTab(first(params.tab), me.permissions);
  if (tab === null) {
    const blocked = me.mfa_blocked_permissions.some((p) => TABS.some((tb) => tb.permissions.includes(p)));
    return <Message text={blocked ? t("access.mfaRequired") : t("review.noAccess")} />;
  }
  return (
    <div className="flex flex-col gap-6">
      <h1 id="page-title" className="text-2xl font-semibold text-navy">{t("review.title")}</h1>
      <TabNav permissions={me.permissions} active={tab} />
      {tab === "ops" ? <OpsQueue session={session} /> : null}
      {tab === "dq" ? <DqQueue me={me} session={session} params={params} /> : null}
      {tab === "duplicates" ? <DqQueue me={me} session={session} params={{ ...params, issue_type: "POSSIBLE_DUPLICATE" }} duplicates /> : null}
      {isWorkflowQueue(tab) ? <WorkflowQueue session={session} queue={tab} params={params} /> : null}
      {tab === "ai" ? <AiQueue me={me} session={session} params={params} /> : null}
    </div>
  );
}

async function DqQueue({ me, session, params, duplicates = false }: { me: Me; session: Session; params: Awaited<SearchParams>; duplicates?: boolean }) {

  const [pageResponse, summaryResponse] = await Promise.all([
    apiFetch(session, `/api/v1/dq/issues?${queueQuery(params)}`),
    apiFetch(session, "/api/v1/dq/summary?state=OPEN"),
  ]);
  if (pageResponse.status === 403) return <p role="alert">{t("review.filterForbidden")}</p>;
  if (!pageResponse.ok) return <p role="alert">{t("review.unavailable")}</p>;
  const page = (await pageResponse.json()) as IssuePage;
  const summary = summaryResponse.ok ? ((await summaryResponse.json()) as DqSummary) : null;

  const filters = filterParams(params);
  filters.set("tab", duplicates ? "duplicates" : "dq");
  const next = new URLSearchParams(filters);
  if (page.pagination.next_cursor) next.set("cursor", page.pagination.next_cursor);
  const actionLabels: DqActionLabels = {
    ASSIGNED: t("review.action.ASSIGNED"),
    OPEN: t("review.action.OPEN"),
    RESOLVED: t("review.action.RESOLVED"),
    ACCEPTED_AS_IS: t("review.action.ACCEPTED_AS_IS"),
    done: {
      ASSIGNED: t("review.done.ASSIGNED"),
      OPEN: t("review.done.OPEN"),
      RESOLVED: t("review.done.RESOLVED"),
      ACCEPTED_AS_IS: t("review.done.ACCEPTED_AS_IS"),
    },
    note: t("review.note"),
    noteHelp: t("review.noteHelp"),
    confirm: t("review.confirm"),
    cancel: t("review.cancel"),
    failed: t("review.failed"),
    conflict: t("review.conflict"),
    forbidden: t("review.forbidden"),
  };

  return (
    <section aria-labelledby="dq-title" className="flex flex-col gap-6">
      <div className="max-w-4xl">
        <h2 id="dq-title" className="text-xl font-semibold text-navy">
          {duplicates ? t("wf.tab.duplicates") : t("review.dqTitle")}
        </h2>
        <p className="mt-2">{duplicates ? t("wf.intro.duplicates") : t("review.intro")}</p>
        {page.categories_shown ? null : <p className="mt-1 text-sm">{t("review.categoriesHidden")}</p>}
        {summary ? (
          <p className="mt-2 font-semibold">
            {duplicates
              ? tn("review.duplicateCount", summary.by_type.filter((c) => c.issue_type === "POSSIBLE_DUPLICATE").reduce((n, c) => n + c.count, 0))
              : tn("review.openCount", summary.total)}
          </p>
        ) : null}
      </div>

      <form method="get" aria-label={t("review.filters")} className="flex flex-wrap items-end gap-4 rounded-panel border border-divider bg-surface-2 p-4">
        <input type="hidden" name="tab" value={duplicates ? "duplicates" : "dq"} />
        <Select
          name="state"
          label={t("review.state")}
          value={first(params.state) || "OPEN"}
          options={[...STATES.map((s) => ({ value: s, label: stateLabel(s) })), { value: "ALL", label: t("review.all") }]}
        />
        <Select
          name="severity"
          label={t("review.severity")}
          value={first(params.severity)}
          options={[{ value: "", label: t("review.any") }, ...SEVERITIES.map((s) => ({ value: s, label: severityLabel(s) }))]}
        />
        {duplicates ? null : (
          <Select
            name="issue_type"
            label={t("review.type")}
            value={first(params.issue_type)}
            options={[{ value: "", label: t("review.any") }, ...ISSUE_TYPES.map((s) => ({ value: s, label: issueLabel(s) }))]}
          />
        )}
        <Select
          name="region"
          label={t("review.region")}
          value={first(params.region)}
          options={[{ value: "", label: t("review.any") }, ...REGIONS.map((s) => ({ value: s, label: s }))]}
        />
        <label className="flex min-h-11 items-center gap-2 text-sm font-medium">
          <input type="checkbox" name="mine" value="1" defaultChecked={first(params.mine) === "1"} className="size-5" />
          {t("review.mine")}
        </label>
        <button type="submit" className="min-h-11 rounded-control bg-navy px-4 text-sm font-semibold text-on-navy">
          {t("review.apply")}
        </button>
      </form>

      {page.data.length === 0 ? (
        <p>{t("review.empty")}</p>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full min-w-[960px] border-collapse text-left text-sm">
            <caption className="sr-only">{t("review.dqTitle")}</caption>
            <thead>
              <tr className="border-b border-control-border">
                <th scope="col" className="p-2">{t("review.severity")}</th>
                <th scope="col" className="p-2">{t("review.type")}</th>
                <th scope="col" className="p-2">{t("review.subject")}</th>
                <th scope="col" className="p-2">{t("review.where")}</th>
                <th scope="col" className="p-2">{t("review.facts")}</th>
                <th scope="col" className="p-2">{t("review.state")}</th>
                <th scope="col" className="p-2">{t("review.actions")}</th>
                <th scope="col" className="p-2">{t("wf.history")}</th>
              </tr>
            </thead>
            <tbody>
              {page.data.map((issue) => (
                <tr key={issue.id} className="border-b border-divider align-top">
                  <td className="p-2 font-semibold">
                    <span className={issue.severity === "HIGH" ? "text-saffron-strong" : undefined}>{severityLabel(issue.severity)}</span>
                  </td>
                  <td className="p-2">
                    {issueLabel(issue.issue_type)}
                    <span className="block text-xs">
                      {t("review.rule")} {issue.rule_code}
                    </span>
                  </td>
                  <th scope="row" className="p-2 text-left font-mono text-xs font-normal">
                    {issue.incident?.reference ?? issue.source?.code ?? "—"}
                  </th>
                  <td className="p-2">
                    {issue.region_code ?? "—"}
                    {issue.station_code ? ` · ${issue.station_code}` : ""}
                  </td>
                  <td className="p-2">
                    <Facts issue={issue} categoriesShown={page.categories_shown} />
                  </td>
                  <td className="p-2">
                    {stateLabel(issue.state)}
                    {issue.assigned_to === me.user_id ? <span className="block text-xs">{t("review.you")}</span> : null}
                    {issue.resolution_note ? <span className="block text-xs">{issue.resolution_note}</span> : null}
                  </td>
                  <td className="p-2">
                    <DqIssueActions issueId={issue.id} actions={actionsFor(issue.state)} csrfToken={session.csrfToken} labels={actionLabels} />
                  </td>
                  <td className="p-2"><Link href={`/review/history?type=DQ_ISSUE&id=${encodeURIComponent(issue.id)}`}>{t("wf.historyLink")}</Link></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <nav aria-label={t("review.pagination")} className="flex gap-4">
        {first(params.cursor) ? <Link href={`/review?${filters}`}>{t("review.first")}</Link> : null}
        {page.pagination.has_more ? <Link href={`/review?${next}`}>{t("review.next")}</Link> : null}
      </nav>
    </section>
  );
}
