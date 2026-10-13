import Link from "next/link";
import { DecideReviewForm, DraftReviewForm, Flash, ProposeActionForm } from "@/components/workflow/Forms";
import { Facts, History, Notice, day } from "@/components/workflow/View";
import { apiFetch } from "@/lib/api";
import { requireUser } from "@/lib/current-user";
import { formatIst } from "@/lib/dq";
import { t, tn } from "@/lib/i18n";
import { type HistoryItem, type PatternReview, flashKey, flashNonce } from "@/lib/workflow";
import type { MessageKey } from "@/messages/en";

export const dynamic = "force-dynamic";

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

function quality(summary: Record<string, unknown>): string {
  const note = typeof summary.data_quality_note === "string" ? summary.data_quality_note : null;
  const caveats = Array.isArray(summary.caveats) ? summary.caveats.filter((c): c is string => typeof c === "string") : [];
  const excluded = summary.excluded_reasons && typeof summary.excluded_reasons === "object" ? Object.entries(summary.excluded_reasons as Record<string, unknown>) : [];
  return [
    note,
    excluded.length ? `${t("wf.f.excluded")}: ${excluded.map(([k, v]) => `${k} ${String(v)}`).join(", ")}` : null,
    ...caveats,
  ]
    .filter(Boolean)
    .join("\n") || "—";
}

export default async function PatternReviewPage({ params, searchParams }: { params: Promise<{ id: string }>; searchParams: Promise<Record<string, string | string[] | undefined>> }) {
  const { id } = await params;
  const query = await searchParams;
  const done = flashKey(query.done);
  const nonce = flashNonce(query.n);
  const { session } = await requireUser();
  if (!UUID.test(id)) return <Notice title={t("wf.review.title")} text={t("wf.error.notFound")} />;
  const [reviewResponse, historyResponse] = await Promise.all([
    apiFetch(session, `/api/v1/pattern-reviews/${id}`),
    apiFetch(session, `/api/v1/workflow-history?object_type=PATTERN_REVIEW&object_id=${id}`),
  ]);
  if (!reviewResponse.ok) {
    return <Notice title={t("wf.review.title")} text={reviewResponse.status === 404 ? t("wf.error.notFound") : t("wf.unavailable")} />;
  }
  const review = (await reviewResponse.json()) as PatternReview;
  const history = historyResponse.ok ? ((await historyResponse.json()) as { data: HistoryItem[] }).data : null;
  const state = review.supervisor_status ?? review.analyst_status;
  return (
    <div className="flex max-w-5xl flex-col gap-6">
      <p><Link href="/review?tab=patterns">{t("wf.back")}</Link></p>
      {done ? <Flash key={nonce} text={t(`wf.flash.${done}`)} /> : null}
      <header>
        <h1 id="page-title" className="text-2xl font-semibold text-navy">
          {t("wf.review.title")} · {t(`wf.reviewType.${review.review_type}` as MessageKey)}
        </h1>
        <p className="mt-1 font-semibold">{t("wf.state", { state: t(`wf.status.${state}` as MessageKey) })}</p>
        <p className="mt-1 text-sm">{t("wf.review.intro")}</p>
      </header>

      <section aria-labelledby="evidence-title" className="flex flex-col gap-2">
        <h2 id="evidence-title" className="text-xl font-semibold text-navy">{t("wf.review.evidence")}</h2>
        <Facts
          rows={[
            [t("wf.f.area"), `${review.region}${review.station ? ` · ${review.station}` : ""} · ${tn("wf.cells", review.cell_keys.length)}`],
            [t("wf.f.cells"), <span key="c" className="font-mono text-xs">{review.cell_keys.join(", ")}</span>],
            [t("wf.f.category"), review.category ?? t("wf.notShownAccess")],
            [t("wf.f.period"), `${day(review.period_start)} – ${day(review.period_end)}`],
            [t("wf.f.baseline"), `${day(review.baseline_start)} – ${day(review.baseline_end)}`],
            [t("wf.f.records"), `${review.record_count} (${t("wf.f.baselineCount", { count: review.baseline_count ?? "—" })})`],
            [t("wf.f.quality"), quality(review.quality_summary as Record<string, unknown>)],
            [
              t("wf.f.snapshot"),
              review.evidence_snapshot_id ? (
                <Link key="s" href={`/review/evidence/${encodeURIComponent(review.evidence_snapshot_id)}`}>{t("wf.openEvidence")}</Link>
              ) : (
                t("wf.notShownAccess")
              ),
            ],
            [t("wf.f.author"), `${review.author ?? "—"} · ${formatIst(review.created_at)}`],
          ]}
        />
      </section>

      <section aria-labelledby="analysis-title" className="flex flex-col gap-2">
        <h2 id="analysis-title" className="text-xl font-semibold text-navy">{t("wf.review.analysis")}</h2>
        <Facts
          rows={[
            [t("wf.f.narrative"), review.narrative],
            [t("wf.f.nextStep"), review.recommended_next_step],
            [t("wf.f.assessment"), review.analyst_assessment ?? "—"],
            [t("wf.f.submitted"), review.submitted_at ? formatIst(review.submitted_at) : "—"],
            [t("wf.f.decision"), review.decided_at ? `${t(`wf.status.${state}` as MessageKey)} · ${review.decided_by ?? "—"} · ${formatIst(review.decided_at)}` : "—"],
            [t("wf.f.rationale"), review.decision_rationale ?? "—"],
          ]}
        />
      </section>

      {review.may_edit ? (
        <DraftReviewForm
          key={`${review.narrative}|${review.recommended_next_step}`}
          reviewId={review.id}
          narrative={review.narrative}
          nextStep={review.recommended_next_step}
          returned={review.supervisor_status === "RETURNED"}
          csrfToken={session.csrfToken}
        />
      ) : null}
      {review.may_decide ? <DecideReviewForm reviewId={review.id} csrfToken={session.csrfToken} /> : null}
      {review.supervisor_status === "PENDING" && review.created_by_me ? <p className="text-sm">{t("wf.review.ownPending")}</p> : null}
      {review.may_propose_action ? (
        <ProposeActionForm reviewId={review.id} cellKeys={review.cell_keys} region={review.region} station={review.station ?? null} csrfToken={session.csrfToken} />
      ) : null}

      <History items={history} />
    </div>
  );
}
