import Link from "next/link";
import { ApproveActionForm, CompleteActionForm, CreateOutcomeForm, EditOutcomeForm, Flash } from "@/components/workflow/Forms";
import { Facts, History, Notice, day } from "@/components/workflow/View";
import { apiFetch } from "@/lib/api";
import type { Session } from "@/lib/session";
import { requireUser } from "@/lib/current-user";
import { formatIst } from "@/lib/dq";
import { t, tn } from "@/lib/i18n";
import { type Action, type HistoryItem, type Outcome, flashKey, flashNonce } from "@/lib/workflow";
import type { MessageKey } from "@/messages/en";

export const dynamic = "force-dynamic";

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

async function history(session: Session, type: "ACTION" | "OUTCOME_REVIEW", id: string): Promise<HistoryItem[] | null> {
  const r = await apiFetch(session, `/api/v1/workflow-history?object_type=${type}&object_id=${id}`);
  return r.ok ? ((await r.json()) as { data: HistoryItem[] }).data : null;
}

type Metrics = { action_area: number; surrounding_ring: number; comparison_area: number | null; per_30_days: Record<string, number>; period: { from: string; to: string; days: number }; sensitive_rule: string | null };

function MetricsTable({ outcome }: { outcome: Outcome }) {
  const before = outcome.before_metrics as unknown as Metrics;
  const after = outcome.after_metrics as unknown as Metrics;
  const rows = (["action_area", "surrounding_ring", "comparison_area"] as const).filter((k) => before[k] !== null && after[k] !== null);
  return (
    <div className="overflow-x-auto">
      <table className="w-full min-w-[640px] border-collapse text-left text-sm">
        <caption className="mb-2 text-left font-semibold">{t("wf.outcome.metrics")}</caption>
        <thead>
          <tr className="border-b border-control-border">
            <th scope="col" className="p-2">{t("wf.outcome.measure")}</th>
            <th scope="col" className="p-2">{t("wf.outcome.beforeCol", { from: before.period.from, to: before.period.to })}</th>
            <th scope="col" className="p-2">{t("wf.outcome.afterCol", { from: after.period.from, to: after.period.to })}</th>
            <th scope="col" className="p-2">{t("wf.outcome.per30")}</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((k) => (
            <tr key={k} className="border-b border-divider">
              <th scope="row" className="p-2 text-left font-medium">{t(`wf.outcome.m.${k}`)}</th>
              <td className="p-2">{before[k]}</td>
              <td className="p-2">{after[k]}</td>
              <td className="p-2">{before.per_30_days[k]} → {after.per_30_days[k]}</td>
            </tr>
          ))}
        </tbody>
      </table>
      {before.sensitive_rule ? <p className="mt-1 text-xs">{before.sensitive_rule}</p> : null}
    </div>
  );
}

async function OutcomeSection({ session, action }: { session: Session; action: Action }) {
  if (!action.outcome_id) {
    return action.may_review_outcome ? (
      <CreateOutcomeForm actionId={action.id} startAt={action.start_at} endAt={action.end_at} csrfToken={session.csrfToken} />
    ) : (
      <p>{t("wf.outcome.none")}</p>
    );
  }
  const [response, steps] = await Promise.all([apiFetch(session, `/api/v1/outcomes/${action.outcome_id}`), history(session, "OUTCOME_REVIEW", action.outcome_id)]);
  if (!response.ok) return <p>{response.status === 404 || response.status === 403 ? t("wf.outcome.notVisible") : t("wf.unavailable")}</p>;
  const outcome = (await response.json()) as Outcome;
  const refs = outcome.evidence_references as { kind: "EVIDENCE_SNAPSHOT" | "DOCUMENT"; value: string }[];
  return (
    <section aria-labelledby="outcome-title" className="flex flex-col gap-3">
      <h2 id="outcome-title" className="text-xl font-semibold text-navy">
        {t("wf.outcome.title")} · {t(`wf.status.${outcome.status}` as MessageKey)}
      </h2>
      <Facts
        rows={[
          [t("wf.outcome.baseline"), `${day(outcome.baseline_start)} – ${day(outcome.baseline_end)}`],
          [t("wf.outcome.after"), `${day(outcome.after_start)} – ${day(outcome.after_end)}`],
          [t("wf.outcome.comparison"), outcome.comparison_kind === "NONE" ? t("wf.outcome.comparison.NONE") : `${t(`wf.outcome.comparison.${outcome.comparison_kind}` as MessageKey)}: ${outcome.comparison_keys.join(", ")}`],
          [t("wf.f.author"), `${outcome.reviewer ?? "—"} · ${formatIst(outcome.created_at)}`],
        ]}
      />
      {outcome.metrics_visible ? (
        <>
          <MetricsTable outcome={outcome} />
          <ul className="list-disc pl-6 text-sm">
            {outcome.summary.map((line) => <li key={line}>{line}</li>)}
          </ul>
        </>
      ) : (
        <p>{t("wf.outcome.metricsHidden")}</p>
      )}
      {outcome.may_edit ? (
        <EditOutcomeForm key={outcome.updated_at} outcomeId={outcome.id} notes={outcome.displacement_diffusion_notes ?? ""} conclusion={outcome.reviewer_conclusion ?? ""} references={refs} csrfToken={session.csrfToken} />
      ) : (
        <Facts
          rows={[
            [t("wf.outcome.notes"), outcome.displacement_diffusion_notes ?? "—"],
            [t("wf.outcome.conclusion"), outcome.reviewer_conclusion ?? "—"],
            [
              t("wf.outcome.references"),
              refs.length ? (
                <ul key="refs">
                  {refs.map((r) => (
                    <li key={`${r.kind}:${r.value}`}>
                      {t(`wf.ref.${r.kind}` as MessageKey)}:{" "}
                      {r.kind === "EVIDENCE_SNAPSHOT" ? <Link href={`/review/evidence/${encodeURIComponent(r.value)}`}>{r.value}</Link> : r.value}
                    </li>
                  ))}
                </ul>
              ) : (
                "—"
              ),
            ],
            [t("wf.outcome.finalisedAt"), outcome.finalised_at ? formatIst(outcome.finalised_at) : "—"],
          ]}
        />
      )}
      <History items={steps} />
    </section>
  );
}

export default async function ActionPage({ params, searchParams }: { params: Promise<{ id: string }>; searchParams: Promise<Record<string, string | string[] | undefined>> }) {
  const { id } = await params;
  const query = await searchParams;
  const done = flashKey(query.done);
  const nonce = flashNonce(query.n);
  const { session } = await requireUser();
  if (!UUID.test(id)) return <Notice title={t("wf.action.title")} text={t("wf.error.notFound")} />;
  const response = await apiFetch(session, `/api/v1/actions/${id}`);
  if (!response.ok) {
    return <Notice title={t("wf.action.title")} text={response.status === 404 ? t("wf.error.notFound") : t("wf.unavailable")} />;
  }
  const action = (await response.json()) as Action;
  const steps = await history(session, "ACTION", id);
  const state = action.completion_status === "COMPLETED" ? "COMPLETED" : action.approval_status;
  return (
    <div className="flex max-w-5xl flex-col gap-6">
      <p><Link href="/review?tab=actions">{t("wf.back")}</Link></p>
      {done ? <Flash key={nonce} text={t(`wf.flash.${done}`)} /> : null}
      <header>
        <h1 id="page-title" className="text-2xl font-semibold text-navy">
          {t("wf.action.title")} · {t(`wf.actionType.${action.action_type}` as MessageKey)}
        </h1>
        <p className="mt-1 font-semibold">{t("wf.state", { state: t(`wf.status.${state}` as MessageKey) })}</p>
        <p className="mt-1 text-sm">{t("wf.action.intro")}</p>
      </header>
      <Facts
        rows={[
          [t("wf.action.description"), action.description],
          [t("wf.f.area"), `${action.region} · ${action.station} ${action.station_name} · ${tn("wf.cells", action.cell_keys.length)}`],
          [t("wf.f.cells"), <span key="c" className="font-mono text-xs">{action.cell_keys.join(", ")}</span>],
          [t("wf.f.category"), action.category ?? t("wf.notShownAccess")],
          [t("wf.action.dates"), `${day(action.start_at)} – ${day(action.end_at)}`],
          [t("wf.action.assignee"), action.assignee ?? t("wf.action.unassigned")],
          [t("wf.action.proposer"), action.proposer ?? "—"],
          [
            t("wf.f.review"),
            action.pattern_review_visible ? (
              <Link key="r" href={`/review/patterns/${encodeURIComponent(action.pattern_review_id)}`}>{t("wf.openReview")}</Link>
            ) : (
              t("wf.notShownTerritory")
            ),
          ],
          [t("wf.f.snapshot"), action.evidence_snapshot_id ? <Link key="s" href={`/review/evidence/${encodeURIComponent(action.evidence_snapshot_id)}`}>{t("wf.openEvidence")}</Link> : t("wf.notShownAccess")],
          [t("wf.f.decision"), action.approved_at ? `${t(`wf.status.${action.approval_status}` as MessageKey)} · ${action.approver ?? "—"} · ${formatIst(action.approved_at)}` : "—"],
          [t("wf.f.rationale"), action.approval_rationale ?? "—"],
          [t("wf.complete.evidence"), action.completion_evidence ?? "—"],
          [t("wf.complete.reference"), action.completion_reference ?? "—"],
          [t("wf.action.completed"), action.completed_at ? `${action.completer ?? "—"} · ${formatIst(action.completed_at)}` : "—"],
          [t("wf.action.outcomeDue"), day(action.outcome_review_due_at)],
        ]}
      />
      {action.may_approve ? <ApproveActionForm actionId={action.id} station={action.station} assigneeName={action.assignee ?? null} csrfToken={session.csrfToken} /> : null}
      {action.approval_status === "PROPOSED" && !action.may_approve ? <p className="text-sm">{t("wf.action.awaitingApproval")}</p> : null}
      {action.may_complete ? <CompleteActionForm actionId={action.id} csrfToken={session.csrfToken} /> : null}
      {action.completion_status === "COMPLETED" ? <OutcomeSection session={session} action={action} /> : null}
      <History items={steps} />
    </div>
  );
}
