import Link from "next/link";
import { AddNoteForm, AiReviewForm, Flash } from "@/components/workflow/Forms";
import { Facts, Notice, day } from "@/components/workflow/View";
import { AI_LABEL, type InsightCard, analyticsHref, entries } from "@/lib/ai";
import { apiFetch } from "@/lib/api";
import { requireUser } from "@/lib/current-user";
import { formatIst } from "@/lib/dq";
import { t } from "@/lib/i18n";
import { flashKey, flashNonce } from "@/lib/workflow";
import type { MessageKey } from "@/messages/en";

export const dynamic = "force-dynamic";

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

type Note = { id: string; note: string; created_at: string; created_by_me: boolean };

function KeyValues({ caption, rows }: { caption: string; rows: [string, string][] }) {
  return (
    <table className="w-full max-w-2xl border-collapse text-left text-sm">
      <caption className="mb-1 text-left font-semibold">{caption}</caption>
      <tbody>
        {rows.map(([k, v]) => (
          <tr key={k} className="border-b border-divider">
            <th scope="row" className="p-2 font-medium">{k}</th>
            <td className="p-2">{v}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

/** The AI insight card (spec contract): type, model version, input snapshot, place and window,
 * category, evidence and baseline, data quality, confidence, explanation, limitations, required
 * review, Open Evidence / Validate / Dismiss / Add Note, and the audit trail. */
export default async function InsightPage({ params, searchParams }: { params: Promise<{ id: string }>; searchParams: Promise<Record<string, string | string[] | undefined>> }) {
  const { id } = await params;
  const query = await searchParams;
  const done = flashKey(query.done);
  const nonce = flashNonce(query.n);
  const { me, session } = await requireUser();
  if (!UUID.test(id)) return <Notice title={t("ai.card.title")} text={t("wf.error.notFound")} />;
  // Notes are read and written with the analyst-note permission (opsrecord.create).
  const mayNote = me.permissions.includes("opsrecord.create");
  let response: Response;
  try {
    response = await apiFetch(session, `/api/v1/ai/insights/${id}`);
  } catch {
    return <Notice title={t("ai.card.title")} text={t("wf.unavailable")} />;
  }
  if (!response.ok) {
    return <Notice title={t("ai.card.title")} text={response.status === 404 || response.status === 403 ? t("wf.error.notFound") : t("wf.unavailable")} />;
  }
  const card = (await response.json()) as InsightCard;
  let notes: Note[] | null = null;
  if (mayNote) {
    try {
      const r = await apiFetch(session, `/api/v1/notes?subject_type=AI_INSIGHT&subject_id=${id}`);
      notes = r.ok ? ((await r.json()) as { data: Note[] }).data : null;
    } catch {
      notes = null;
    }
  }
  const evidenceLink = me.permissions.includes("analytics.read") ? analyticsHref(card) : null;
  return (
    <div className="flex max-w-5xl flex-col gap-6">
      <p><Link href="/review?tab=ai">{t("wf.back")}</Link></p>
      {done ? <Flash key={nonce} text={t(`wf.flash.${done}`)} /> : null}
      <header className="flex flex-col gap-1">
        <p className="w-fit rounded-control border-2 border-saffron-strong px-2 py-1 text-sm font-semibold">{AI_LABEL}</p>
        <h1 id="page-title" className="text-2xl font-semibold text-navy">{t(`ai.type.${card.insight_type}` as MessageKey)}</h1>
        <p className="font-semibold">{t("wf.state", { state: t(`ai.state.${card.state}` as MessageKey) })}</p>
        <p>{card.explanation}</p>
      </header>

      <section aria-labelledby="what-title" className="flex flex-col gap-2">
        <h2 id="what-title" className="text-xl font-semibold text-navy">{t("ai.card.what")}</h2>
        <Facts
          rows={[
            [t("ai.card.model"), `${card.model_name} (${card.model})${card.model_enabled ? "" : ` · ${t("ai.card.modelOff")}`}`],
            [t("ai.card.place"), [card.region, card.station, entries(card.geography).map(([k, v]) => `${k}: ${v}`).join("; ")].filter(Boolean).join(" · ")],
            [t("ai.card.window"), `${day(card.window_start)} – ${day(card.window_end)}`],
            [t("wf.f.category"), card.category],
            [t("ai.card.confidence"), `${t(`wf.priority.${card.confidence_label}` as MessageKey)} (${card.confidence})`],
            [t("ai.card.calibration"), card.calibration_note],
            [t("ai.card.dq"), String(card.data_quality_score)],
            [t("ai.card.exclusions"), entries(card.exclusions).map(([k, v]) => `${k}: ${v}`).join("\n") || "—"],
            [
              t("ai.card.input"),
              <span key="run">
                <span className="font-mono text-xs">{card.input_snapshot_id}</span>{" "}
                <Link href={`/review/ai-runs/${encodeURIComponent(card.input_snapshot_id)}`}>{t("ai.card.openEvidence")}</Link>
              </span>,
            ],
          ]}
        />
        {evidenceLink ? <p><Link href={evidenceLink}>{t("ai.card.openAnalytics")}</Link></p> : null}
      </section>

      <section aria-labelledby="evidence-title" className="flex flex-col gap-3">
        <h2 id="evidence-title" className="text-xl font-semibold text-navy">{t("ai.card.evidence")}</h2>
        <KeyValues caption={t("ai.card.evidenceValues")} rows={entries(card.evidence)} />
        <KeyValues caption={t("ai.card.baseline")} rows={entries(card.baseline)} />
        {card.references.length ? (
          <div>
            <h3 className="font-semibold">{t("ai.card.records")}</h3>
            <ul className="list-disc pl-6 text-sm">
              {card.references.map((r) => <li key={r.incident_id}>{r.reference ?? t("ai.card.recordHidden")}</li>)}
            </ul>
          </div>
        ) : null}
      </section>

      <section aria-labelledby="limits-title" className="flex flex-col gap-2">
        <h2 id="limits-title" className="text-xl font-semibold text-navy">{t("ai.card.limitations")}</h2>
        <ul className="list-disc pl-6 text-sm">{card.limitations.map((l) => <li key={l}>{l}</li>)}</ul>
        <h3 className="font-semibold">{t("ai.card.alternatives")}</h3>
        <ul className="list-disc pl-6 text-sm">{card.alternative_explanations.map((l) => <li key={l}>{l}</li>)}</ul>
        {card.state === "PENDING" ? <p className="font-semibold">{t("ai.card.required", { action: card.required_action })}</p> : null}
      </section>

      {card.reviewable ? <AiReviewForm insightId={card.id} csrfToken={session.csrfToken} /> : null}
      {!card.reviewable && card.state === "PENDING" ? (
        <p className="text-sm">{!card.ai_enabled ? t("ai.queue.off") : !card.model_enabled ? t("ai.card.switchedOff") : t("ai.card.notReviewer")}</p>
      ) : null}
      {card.decided_at ? (
        <Facts rows={[[t("ai.card.decision"), `${t(`ai.state.${card.state}` as MessageKey)} · ${card.decided_by ?? "—"} · ${formatIst(card.decided_at)}`]]} />
      ) : null}

      {mayNote ? (
      <section aria-labelledby="notes-title" className="flex flex-col gap-2">
        <h2 id="notes-title" className="text-xl font-semibold text-navy">{t("ai.note.list")}</h2>
        {notes === null ? <p>{t("wf.unavailable")}</p> : notes.length === 0 ? <p className="text-sm">{t("ai.note.none")}</p> : (
          <ul className="flex flex-col gap-2 text-sm">
            {notes.map((n) => <li key={n.id} className="rounded-panel border border-divider p-2"><span className="whitespace-pre-wrap">{n.note}</span><span className="block text-xs">{formatIst(n.created_at)}{n.created_by_me ? ` · ${t("review.you")}` : ""}</span></li>)}
          </ul>
        )}
        <AddNoteForm key={notes?.length ?? 0} subjectType="AI_INSIGHT" subjectId={card.id} csrfToken={session.csrfToken} />
      </section>
      ) : null}

      <section aria-labelledby="history-title" className="flex flex-col gap-2">
        <h2 id="history-title" className="text-xl font-semibold text-navy">{t("wf.history")}</h2>
        <ol className="flex flex-col gap-2">
          {card.history.map((h, i) => (
            <li key={i} className="rounded-panel border border-divider p-3 text-sm">
              <p className="font-semibold">{h.from_state ? `${t(`ai.state.${h.from_state}` as MessageKey)} → ` : ""}{t(`ai.state.${h.to_state}` as MessageKey)}</p>
              <p>{formatIst(h.at)} · {h.actor}</p>
              {h.note ? <p className="mt-1 whitespace-pre-wrap">{h.note}</p> : null}
            </li>
          ))}
        </ol>
      </section>
    </div>
  );
}
