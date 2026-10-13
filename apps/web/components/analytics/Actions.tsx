"use client";

import * as Dialog from "@radix-ui/react-dialog";
import { useState } from "react";
import type { SectionId } from "@/lib/analytics/query";
import { ApiError, getJson, postJson } from "@/lib/map/client";
import { formatIst } from "@/lib/dq";
import { t } from "@/lib/i18n";
import type { MessageKey } from "@/messages/en";

type Data = Record<string, any>; // eslint-disable-line @typescript-eslint/no-explicit-any
type Props = { section: SectionId; params: Record<string, string | string[]>; csrfToken: string; stale: boolean };

const btn = "min-h-11 rounded-control border border-control-border px-4 text-sm font-medium";
const primary = "min-h-11 rounded-control bg-navy px-4 text-sm font-semibold text-on-navy disabled:opacity-60";
const field = "flex flex-col gap-1 text-sm font-medium";
const input = "min-h-11 rounded-control border border-control-border bg-surface px-2 text-sm";
const API_SECTION: Record<string, string> = { overview: "overview", location: "location", case_trends: "case_trends", comparison: "comparison", data_quality: "data_quality" };

function explain(e: unknown): string {
  if (e instanceof ApiError) {
    if (e.status === 401) return t("map.status.signIn");
    if (e.status === 403 && e.slug === "reauthentication-required") return t("an.export.reauth");
    if (e.status === 403 && e.slug === "csrf-failed") return t("an.err.csrf");
    if (e.status === 403) return t("an.notPermitted");
    if (e.explanation) return t("record.rejected", { reason: e.explanation });
  }
  return t("drawer.failed");
}

/** A dialog action. Disabled with a stated reason (never silently); `onOpen` resets its state. */
function Shell({ label, title, description, children, disabled, reason, onOpen }: { label: string; title: string; description: string; children: (close: () => void) => React.ReactNode; disabled?: boolean; reason?: string | null; onOpen?: () => void }) {
  const [open, setOpen] = useState(false);
  return (
    <Dialog.Root
      open={open}
      onOpenChange={(value) => {
        if (value) onOpen?.();
        setOpen(value);
      }}
    >
      <span className="inline-flex flex-col">
        <Dialog.Trigger className={btn} disabled={disabled} title={disabled && reason ? reason : undefined}>{label}</Dialog.Trigger>
        {disabled && reason ? <span className="max-w-48 text-xs">{reason}</span> : null}
      </span>
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 bg-ink/40" />
        <Dialog.Content className="fixed left-1/2 top-1/2 flex max-h-[90vh] w-[min(640px,95vw)] -translate-x-1/2 -translate-y-1/2 flex-col gap-3 overflow-y-auto rounded-panel bg-surface p-6 shadow-lg">
          <Dialog.Title className="text-xl font-semibold text-navy">{title}</Dialog.Title>
          <Dialog.Description className="text-sm">{description}</Dialog.Description>
          {children(() => setOpen(false))}
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}

async function snapshot(section: SectionId, params: Props["params"], csrfToken: string): Promise<{ id: string }> {
  return postJson<{ id: string }>("evidence-snapshots", { section: API_SECTION[section], params }, csrfToken);
}

export function ExportAction({ section, params, csrfToken, permitted, stale }: Props & { permitted: { csv: boolean; pdf: boolean } }) {
  const [format, setFormat] = useState<"CSV" | "PDF">(permitted.csv ? "CSV" : "PDF");
  const [purpose, setPurpose] = useState("BRIEFING");
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [reauth, setReauth] = useState(false);
  const [done, setDone] = useState<{ id: string; filename: string; expires_at: string } | null>(null);
  const allowed = permitted.csv || permitted.pdf;
  return (
    <Shell
      label={t("an.export")}
      title={t("an.export.title")}
      description={t("an.export.intro")}
      disabled={!allowed || stale}
      reason={!allowed ? t("an.export.notPermitted") : stale ? t("an.wait") : null}
      onOpen={() => {
        setDone(null);
        setMessage(null);
        setReauth(false);
      }}
    >
      {() => (
        <div className="flex flex-col gap-3">
          <label className={field}>
            {t("an.export.format")}
            <select className={input} value={format} onChange={(e) => setFormat(e.target.value as "CSV" | "PDF")}>
              {permitted.csv ? <option value="CSV">{t("an.export.csv")}</option> : null}
              {permitted.pdf ? <option value="PDF">{t("an.export.pdf")}</option> : null}
            </select>
          </label>
          <label className={field}>
            {t("an.export.purpose")}
            <select className={input} value={purpose} onChange={(e) => setPurpose(e.target.value)}>
              {["BRIEFING", "OPERATIONAL_REVIEW", "PATTERN_ANALYSIS", "OUTCOME_REVIEW"].map((p) => (
                <option key={p} value={p}>{t(`an.purpose.${p}` as MessageKey)}</option>
              ))}
            </select>
          </label>
          <p className="text-xs">{t("an.export.notice")}</p>
          <button
            type="button"
            className={primary}
            disabled={busy || done !== null}
            onClick={async () => {
              setBusy(true);
              setMessage(null);
              setReauth(false);
              try {
                setDone(await postJson("reports", { format, section: API_SECTION[section], purpose, params }, csrfToken));
              } catch (e) {
                setReauth(e instanceof ApiError && e.slug === "reauthentication-required");
                setMessage(explain(e));
              } finally {
                setBusy(false);
              }
            }}
          >
            {t("an.export.generate")}
          </button>
          {reauth ? (
            <a className={btn + " inline-flex items-center"} href={`/bff/auth/login?reauth=1&returnTo=${encodeURIComponent(window.location.pathname + window.location.search)}`}>
              {t("an.export.signIn")}
            </a>
          ) : null}
          {message ? <p role="alert" className="text-sm text-saffron-strong">{message}</p> : null}
          {done ? (
            <p role="status" className="text-sm">
              {t("an.export.ready", { expires: formatIst(done.expires_at) })}{" "}
              <a className="font-semibold underline" href={`/bff/api/reports/${encodeURIComponent(done.id)}/download`}>{done.filename}</a>
            </p>
          ) : null}
        </div>
      )}
    </Shell>
  );
}

export function NoteAction({ section, params, csrfToken, stale }: Props) {
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<{ ok: boolean; text: string } | null>(null);
  return (
    <Shell label={t("an.note")} title={t("an.note.title")} description={t("an.note.intro")} disabled={stale} reason={stale ? t("an.wait") : null} onOpen={() => setMessage(null)}>
      {() => (
        <form
          className="flex flex-col gap-3"
          onSubmit={async (e) => {
            e.preventDefault();
            if (busy) return;
            setBusy(true);
            try {
              const snap = await snapshot(section, params, csrfToken);
              await postJson("notes", { subject_type: "EVIDENCE_SNAPSHOT", subject_id: snap.id, note: text.trim(), visibility: "RESTRICTED" }, csrfToken);
              setText("");
              setMessage({ ok: true, text: t("an.note.saved", { id: snap.id }) });
            } catch (err) {
              setMessage({ ok: false, text: explain(err) });
            } finally {
              setBusy(false);
            }
          }}
        >
          <label className={field}>
            {t("drawer.noteText")}
            <textarea rows={4} maxLength={4000} value={text} onChange={(e) => setText(e.target.value)} className="rounded-control border border-control-border bg-surface p-2" />
          </label>
          <button type="submit" className={primary} disabled={busy || !text.trim()}>{t("drawer.save")}</button>
          {message ? <p role={message.ok ? "status" : "alert"} className="text-sm">{message.text}</p> : null}
        </form>
      )}
    </Shell>
  );
}

export function EvidenceAction({ section, params, csrfToken, stale }: Props) {
  const [snap, setSnap] = useState<Data | null>(null);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  return (
    <Shell label={t("an.evidence")} title={t("an.evidence.title")} description={t("an.evidence.intro")} disabled={stale} reason={stale ? t("an.wait") : null} onOpen={() => { setSnap(null); setMessage(null); }}>
      {() => (
        <div className="flex flex-col gap-3 text-sm">
          <button
            type="button"
            className={primary}
            disabled={busy || snap !== null}
            onClick={async () => {
              setBusy(true);
              try {
                const created = await snapshot(section, params, csrfToken);
                setSnap(await getJson<Data>(`evidence-snapshots/${encodeURIComponent(created.id)}`));
                setMessage(null);
              } catch (err) {
                setMessage(explain(err));
              } finally {
                setBusy(false);
              }
            }}
          >
            {t("an.evidence.create")}
          </button>
          {message ? <p role="alert">{message}</p> : null}
          {snap ? (
            <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1">
              <dt className="font-medium">{t("an.evidence.id")}</dt><dd className="font-mono text-xs">{snap.id}</dd>
              <dt className="font-medium">{t("an.evidence.hash")}</dt><dd className="break-all font-mono text-xs">{snap.content_sha256}</dd>
              <dt className="font-medium">{t("an.evidence.created")}</dt><dd>{formatIst(String(snap.created_at))}</dd>
              <dt className="font-medium">{t("an.records")}</dt><dd>{snap.meta.record_count}</dd>
              <dt className="font-medium">{t("an.filters")}</dt><dd className="font-mono text-xs">{JSON.stringify(snap.meta.filters_applied)}</dd>
            </dl>
          ) : null}
        </div>
      )}
    </Shell>
  );
}

export function PatternReviewAction({ section, params, csrfToken, cells, stale, comparisonOff }: Props & { cells: { key: string; count: number }[]; comparisonOff: boolean }) {
  const [chosen, setChosen] = useState<string[]>([]);
  const [type, setType] = useState("HOTSPOT");
  const [narrative, setNarrative] = useState("");
  const [next, setNext] = useState("");
  const [message, setMessage] = useState<{ ok: boolean; text: string } | null>(null);
  const [busy, setBusy] = useState(false);
  const [saved, setSaved] = useState(false);
  const ready = chosen.length > 0 && narrative.trim().length >= 20 && next.trim().length >= 5;
  const reason = stale ? t("an.wait") : comparisonOff ? t("an.pattern.needsComparison") : cells.length === 0 ? t("an.pattern.needsHex") : null;
  return (
    <Shell label={t("an.pattern")} title={t("an.pattern.title")} description={t("an.pattern.intro")} disabled={reason !== null} reason={reason} onOpen={() => setMessage(null)}>
      {() => (
        <form
          className="flex flex-col gap-3"
          onSubmit={async (e) => {
            e.preventDefault();
            if (busy || saved) return;
            setBusy(true);
            try {
              const snap = await snapshot(section, params, csrfToken);
              const review = await postJson<{ id: string; record_count: number }>(
                "pattern-reviews",
                { review_type: type, evidence_snapshot_id: snap.id, cell_keys: chosen, narrative: narrative.trim(), recommended_next_step: next.trim() },
                csrfToken,
              );
              setSaved(true);
              setMessage({ ok: true, text: t("an.pattern.saved", { count: String(review.record_count) }) });
            } catch (err) {
              setMessage({ ok: false, text: explain(err) });
            } finally {
              setBusy(false);
            }
          }}
        >
          <label className={field}>
            {t("an.pattern.type")}
            <select className={input} value={type} onChange={(e) => setType(e.target.value)}>
              {["HOTSPOT", "EMERGING", "NEAR_REPEAT", "SERIES", "DATA_QUALITY"].map((v) => (
                <option key={v} value={v}>{t(`an.ptype.${v}` as MessageKey)}</option>
              ))}
            </select>
          </label>
          <fieldset className="flex max-h-48 flex-col gap-1 overflow-y-auto">
            <legend className="text-sm font-medium">{t("an.pattern.cells")}</legend>
            {cells.slice(0, 30).map((c) => (
              <label key={c.key} className="flex min-h-11 items-center gap-2 text-sm">
                <input type="checkbox" className="size-5" checked={chosen.includes(c.key)} onChange={(e) => setChosen((v) => (e.target.checked ? [...v, c.key] : v.filter((k) => k !== c.key)))} />
                {t("an.pattern.cell", { key: c.key, count: c.count.toLocaleString("en-IN") })}
              </label>
            ))}
          </fieldset>
          <label className={field}>
            {t("an.pattern.narrative")}
            <textarea rows={4} maxLength={4000} value={narrative} onChange={(e) => setNarrative(e.target.value)} className="rounded-control border border-control-border bg-surface p-2" />
          </label>
          <label className={field}>
            {t("an.pattern.next")}
            <input className={input} maxLength={1000} value={next} onChange={(e) => setNext(e.target.value)} />
          </label>
          <p className="text-xs">{t("an.pattern.rules")}</p>
          <button type="submit" className={primary} disabled={!ready || busy || saved}>{t("an.pattern.create")}</button>
          {message ? <p role={message.ok ? "status" : "alert"} className="text-sm">{message.text}</p> : null}
        </form>
      )}
    </Shell>
  );
}
