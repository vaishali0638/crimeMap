"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";
import {
  ApiError,
  type IncidentDetails,
  type IncidentSummary,
  type NoteList,
  getJson,
  postJson,
} from "@/lib/map/client";
import { type Selection, useMapStore } from "@/lib/map/store";
import { formatIst, issueLabel } from "@/lib/dq";
import { t } from "@/lib/i18n";
import type { MessageKey } from "@/messages/en";

const btn = "min-h-11 rounded-control border border-control-border px-3 text-sm font-medium hover:bg-surface-2";
const ISSUE_TYPES = [
  "MISSING_LOCATION",
  "LOW_CONFIDENCE_LOCATION",
  "MISSING_OCCURRENCE_TIME",
  "POSSIBLE_DUPLICATE",
  "INVALID_CATEGORY_MAPPING",
  "OUTSIDE_JURISDICTION",
  "OTHER",
] as const;

function Row({ label, value }: { label: string; value: string | null | undefined }) {
  return (
    <>
      <dt className="font-medium">{label}</dt>
      <dd>{value || "—"}</dd>
    </>
  );
}

function Notes({ subject, csrfToken, canWrite }: { subject: { type: "INCIDENT"; id: string } | { type: "CELL"; key: string }; csrfToken: string; canWrite: boolean }) {
  const client = useQueryClient();
  const query = subject.type === "INCIDENT" ? `subject_type=INCIDENT&subject_id=${subject.id}` : `subject_type=CELL&subject_key=${encodeURIComponent(subject.key)}`;
  const notes = useQuery({ queryKey: ["notes", query], queryFn: ({ signal }) => getJson<NoteList>(`notes?${query}`, signal), enabled: canWrite });
  const [text, setText] = useState("");
  const [message, setMessage] = useState<string | null>(null);
  const save = useMutation({
    mutationFn: () =>
      postJson("notes", {
        subject_type: subject.type,
        ...(subject.type === "INCIDENT" ? { subject_id: subject.id } : { subject_key: subject.key }),
        note: text.trim(),
        visibility: "RESTRICTED",
      }, csrfToken),
    onSuccess: () => {
      setText("");
      setMessage(t("drawer.saved"));
      void client.invalidateQueries({ queryKey: ["notes", query] });
    },
    onError: () => setMessage(t("drawer.failed")),
  });
  if (!canWrite) return null;
  return (
    <section aria-labelledby="notes-title" className="flex flex-col gap-2">
      <h3 id="notes-title" className="font-semibold text-navy">{t("drawer.notes")}</h3>
      {notes.data?.data.length ? (
        <ul className="flex flex-col gap-1">
          {notes.data.data.map((n) => (
            <li key={n.id} className="rounded-control bg-surface-2 p-2">
              <p className="whitespace-pre-wrap">{n.note}</p>
              <p className="text-xs">{formatIst(n.created_at)}</p>
            </li>
          ))}
        </ul>
      ) : (
        <p>{t("drawer.noNotes")}</p>
      )}
      <form
        onSubmit={(e) => {
          e.preventDefault();
          if (text.trim()) save.mutate();
        }}
        className="flex flex-col gap-2"
      >
        <label className="text-sm font-medium" htmlFor="note-text">{t("drawer.noteText")}</label>
        <textarea id="note-text" rows={2} maxLength={4000} value={text} onChange={(e) => setText(e.target.value)} className="rounded-control border border-control-border bg-surface p-2" />
        <button type="submit" className={btn} disabled={save.isPending || !text.trim()}>{t("drawer.note")}</button>
      </form>
      {message ? <p role="status">{message}</p> : null}
    </section>
  );
}

function Flag({ incidentId, csrfToken }: { incidentId: string; csrfToken: string }) {
  const [open, setOpen] = useState(false);
  const [type, setType] = useState<(typeof ISSUE_TYPES)[number]>("POSSIBLE_DUPLICATE");
  const [note, setNote] = useState("");
  const [message, setMessage] = useState<string | null>(null);
  const flag = useMutation({
    mutationFn: () => postJson(`incidents/${incidentId}/data-issues`, { issue_type: type, note: note.trim() }, csrfToken),
    onSuccess: () => {
      setOpen(false);
      setNote("");
      setMessage(t("drawer.flagged"));
    },
    onError: (e) => setMessage(e instanceof ApiError && e.status === 409 ? t("drawer.alreadyFlagged") : t("drawer.failed")),
  });
  return (
    <div className="flex flex-col gap-2">
      {open ? (
        <form
          className="flex flex-col gap-2 rounded-panel border border-divider p-3"
          onSubmit={(e) => {
            e.preventDefault();
            flag.mutate();
          }}
        >
          <label className="flex flex-col gap-1 text-sm font-medium">
            {t("drawer.issueType")}
            <select className="min-h-11 rounded-control border border-control-border bg-surface px-2" value={type} onChange={(e) => setType(e.target.value as (typeof ISSUE_TYPES)[number])}>
              {ISSUE_TYPES.map((i) => (
                <option key={i} value={i}>{issueLabel(i)}</option>
              ))}
            </select>
          </label>
          <label className="flex flex-col gap-1 text-sm font-medium">
            {t("drawer.flagNote")}
            <textarea rows={2} minLength={5} maxLength={500} required value={note} onChange={(e) => setNote(e.target.value)} className="rounded-control border border-control-border bg-surface p-2" />
          </label>
          <div className="flex gap-2">
            <button type="submit" className={btn} disabled={flag.isPending || note.trim().length < 5}>{t("drawer.save")}</button>
            <button type="button" className={btn} onClick={() => setOpen(false)}>{t("record.cancel")}</button>
          </div>
        </form>
      ) : (
        <button type="button" className={btn} onClick={() => setOpen(true)}>{t("drawer.flag")}</button>
      )}
      {message ? <p role="status">{message}</p> : null}
    </div>
  );
}

function IncidentPanel({ id, permissions, csrfToken }: { id: string; permissions: string[]; csrfToken: string }) {
  const summary = useQuery({ queryKey: ["summary", id], queryFn: ({ signal }) => getJson<IncidentSummary>(`incidents/${id}/summary`, signal), retry: false });
  const [showDetails, setShowDetails] = useState(false);
  const [showSource, setShowSource] = useState(false);
  const canDetail = permissions.includes("incident.detail.read");
  const canWrite = permissions.includes("opsrecord.create");
  const details = useQuery({ queryKey: ["details", id], queryFn: ({ signal }) => getJson<IncidentDetails>(`incidents/${id}`, signal), enabled: showDetails && canDetail, retry: false });
  if (summary.isPending) return <p>{t("drawer.loading")}</p>;
  if (summary.isError || !summary.data) return <p role="alert">{t("drawer.unavailable")}</p>;
  const s = summary.data;
  const range = s.occurred_to && s.occurred_to !== s.occurred_from ? `${formatIst(s.occurred_from)} – ${formatIst(s.occurred_to)}` : formatIst(s.occurred_from);
  return (
    <div className="flex flex-col gap-4">
      <p className="font-mono text-sm font-semibold">{s.reference}</p>
      <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 text-sm">
        <Row label={t("drawer.category")} value={s.category_label} />
        {s.event_type ? (
          <Row label={t("drawer.eventType")} value={s.event_type} />
        ) : (
          <Row label={t("drawer.offence")} value={[s.offence_name, s.offence_section].filter(Boolean).join(" · ")} />
        )}
        <Row label={t("drawer.occurred")} value={range} />
        <Row label={t("drawer.reported")} value={formatIst(s.reported_at)} />
        <Row label={t("drawer.locality")} value={s.locality} />
        <Row label={t("drawer.confidence")} value={[s.location_precision, s.location_confidence?.toFixed(2)].filter(Boolean).join(" · ")} />
        <Row label={t("drawer.station")} value={s.station} />
        <Row label={t("drawer.region")} value={s.region} />
        <Row label={t("drawer.status")} value={s.status} />
      </dl>
      {s.time_precision === "HOUR" ? <p className="text-xs">{t("drawer.hourNote")}</p> : null}
      <div className="flex flex-col gap-2">
        {canDetail ? (
          <button type="button" className={btn} aria-expanded={showDetails} onClick={() => setShowDetails((v) => !v)}>{t("drawer.details")}</button>
        ) : null}
        {showDetails && details.data ? (
          <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 rounded-panel border border-divider p-3 text-sm">
            <Row label={t("drawer.recordType")} value={details.data.record_type} />
            <Row label={t("drawer.act")} value={details.data.offence_act} />
            <Row label={t("drawer.beat")} value={details.data.beat} />
            <Row label={t("drawer.reportingStation")} value={details.data.reporting_station} />
            <Row label={t("drawer.quality")} value={details.data.quality_status} />
            <Row
              label={t("drawer.openIssues")}
              value={details.data.open_issues.length ? details.data.open_issues.map((i) => issueLabel(i.issue_type)).join(", ") : t("drawer.none")}
            />
            <Row label={t("drawer.sourceVersion")} value={String(details.data.source_version)} />
            <Row label={t("drawer.received")} value={formatIst(details.data.source_received_at)} />
          </dl>
        ) : null}
        <button type="button" className={btn} aria-expanded={showSource} onClick={() => setShowSource((v) => !v)}>{t("drawer.source")}</button>
        {showSource ? <p className="text-sm">{t("drawer.sourceStub", { source: s.source_system })}</p> : null}
        {canWrite ? <Flag incidentId={id} csrfToken={csrfToken} /> : null}
      </div>
      <Notes subject={{ type: "INCIDENT", id }} csrfToken={csrfToken} canWrite={canWrite} />
    </div>
  );
}

function CellPanel({ properties, cellKey, permissions, csrfToken }: { properties: Record<string, unknown>; cellKey: string | null; permissions: string[]; csrfToken: string }) {
  const categories = (properties.categories as { code: string; label: string; count: number }[] | string) ?? [];
  const list = typeof categories === "string" ? (JSON.parse(categories) as { code: string; label: string; count: number }[]) : categories;
  const raw = typeof properties.sensitive === "string" ? (JSON.parse(properties.sensitive) as { count: number; label: string }) : (properties.sensitive as { count: number; label: string } | undefined);
  // Without the sensitive-category permission those records are combined and shown only at 3 or more;
  // "none" and "fewer than 3" are deliberately indistinguishable (ADR-012).
  const combined = !permissions.includes("incident.sensitive_category.read");
  return (
    <div className="flex flex-col gap-3 text-sm">
      <p className="font-semibold">{t("drawer.count", { count: Number(properties.count ?? 0).toLocaleString("en-IN") })}</p>
      <ul className="flex flex-col gap-1">
        {list.map((c) => (
          <li key={c.code} className="flex justify-between gap-2"><span>{c.label}</span><span className="tabular-nums">{c.count}</span></li>
        ))}
      </ul>
      {combined ? (
        raw && raw.count > 0 ? <p>{t("drawer.sensitive", { label: raw.label })}</p> : <p className="text-xs">{t("drawer.sensitiveRule")}</p>
      ) : null}
      <p className="text-xs">{t("drawer.patternReview")}</p>
      {cellKey ? <Notes subject={{ type: "CELL", key: cellKey }} csrfToken={csrfToken} canWrite={permissions.includes("opsrecord.create")} /> : null}
    </div>
  );
}

function RecordPanel({ properties }: { properties: Record<string, unknown> }) {
  const kind = String(properties.kind ?? "");
  return (
    <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 text-sm">
      <Row label={t("record.titleField")} value={String(properties.title ?? "")} />
      <Row label={t("record.kind")} value={kind ? t(`record.kind.${kind}` as MessageKey) : null} />
      <Row label={t("record.occurredAt")} value={properties.occurred_at ? formatIst(String(properties.occurred_at)) : null} />
      <Row label={t("drawer.reviewStatus")} value={kind === "APPROVED_EVENT_MARKER" ? t("drawer.reviewApproved") : t("drawer.reviewNotRequired")} />
    </dl>
  );
}

function selectionKey(selection: NonNullable<Selection>): string {
  switch (selection.kind) {
    case "incident":
    case "record":
      return `${selection.kind}:${selection.id}`;
    case "cell":
      return `cell:${selection.key}`;
    case "beat":
      return `beat:${selection.code}`;
  }
}

/**
 * Right-side drawer; the map stays visible and usable. Focus moves to the drawer when it opens and
 * back to where it was when it closes; Escape closes it. Each selection gets fresh panel state
 * (keyed), so a half-written note or flag can never be saved against a different record.
 */
export function RecordDrawer({ permissions, csrfToken }: { permissions: string[]; csrfToken: string }) {
  const { selection, select } = useMapStore();
  const heading = useRef<HTMLHeadingElement>(null);
  const returnTo = useRef<HTMLElement | null>(null);
  const open = selection !== null;
  useEffect(() => {
    if (!selection) return;
    if (!returnTo.current && document.activeElement instanceof HTMLElement && document.activeElement !== document.body) {
      returnTo.current = document.activeElement;
    }
    heading.current?.focus();
  }, [selection]);
  useEffect(() => {
    if (open) return;
    const target = returnTo.current;
    returnTo.current = null;
    if (target?.isConnected) target.focus();
  }, [open]);
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape" && !e.defaultPrevented && !document.querySelector("[role=dialog]")) select(null);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, select]);
  if (!selection) return null;
  const key = selectionKey(selection);
  const title =
    selection.kind === "incident"
      ? t("drawer.title")
      : selection.kind === "cell"
        ? t("drawer.cellTitle")
        : selection.kind === "record"
          ? t("drawer.recordTitle")
          : `${t("drawer.beatTitle")} ${selection.code}`;
  return (
    <aside aria-labelledby="drawer-title" className="flex max-h-full w-[360px] flex-col gap-4 overflow-y-auto border-l border-divider bg-surface p-4">
      <div className="flex items-start justify-between gap-2">
        <h2 id="drawer-title" ref={heading} tabIndex={-1} className="text-lg font-semibold text-navy">{title}</h2>
        <button type="button" onClick={() => select(null)} aria-label={t("drawer.close")} className="min-h-11 min-w-11 rounded-control border border-control-border">✕</button>
      </div>
      {selection.kind === "incident" ? (
        <IncidentPanel key={key} id={selection.id} permissions={permissions} csrfToken={csrfToken} />
      ) : selection.kind === "record" ? (
        <RecordPanel key={key} properties={selection.properties} />
      ) : (
        <CellPanel key={key} properties={selection.properties} cellKey={selection.kind === "cell" ? selection.key : null} permissions={permissions} csrfToken={csrfToken} />
      )}
      <button type="button" className={btn} onClick={() => select(null)}>{t("drawer.back")}</button>
    </aside>
  );
}
