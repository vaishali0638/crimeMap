"use client";

import { usePathname, useRouter } from "next/navigation";
import { type ReactNode, useEffect, useId, useState, useTransition } from "react";
import { ApiError } from "@/lib/map/client";
import { t } from "@/lib/i18n";
import type { MessageKey } from "@/messages/en";
import type { BriefingOut } from "@/lib/ai";
import { type FlashKey, actionDatesError, parseCellKeys, periodsError, problemExplanation, suggestedPeriods } from "@/lib/workflow";

/**
 * Review Queue forms (Phase 6). Each posts through the BFF with the CSRF token. The API decides:
 * permission, territory, separation of duties and the allowed state change (and the database
 * checks the same). These forms only mirror the simple field rules so mistakes show early.
 *
 * After a change the page is re-rendered with `?done=<key>`: the page shows the outcome in a
 * live region outside the (now possibly removed) form and moves focus there (WCAG 4.1.3, 2.4.3).
 */

const input = "min-h-11 rounded-control border border-control-border bg-surface px-2 text-sm";
const area = "rounded-control border border-control-border bg-surface p-2 text-sm";
const button = "min-h-11 rounded-control border border-control-border px-3 text-sm font-semibold disabled:opacity-60";
const primary = "min-h-11 rounded-control bg-navy px-4 text-sm font-semibold text-on-navy disabled:opacity-60";

type Message = { text: string; error: boolean; signIn?: boolean; reauth?: boolean };

function explain(error: unknown): Message {
  if (error instanceof ApiError) {
    if (error.status === 401) return { text: t("wf.error.signIn"), error: true, signIn: true };
    if (error.slug === "csrf-failed") return { text: t("wf.error.csrf"), error: true };
    if (error.slug === "reauthentication-required") return { text: t("wf.error.reauth"), error: true, signIn: true, reauth: true };
    if (error.status === 403) return { text: t("wf.error.forbidden"), error: true };
    if (error.status === 404) return { text: t("wf.error.notFound"), error: true };
    if (error.status === 409 || error.status === 422) return { text: error.explanation ?? t("wf.error.invalid"), error: true };
    if (error.status === 503) return { text: error.explanation ?? t("wf.error.disabled"), error: true };
  }
  return { text: t("wf.error.failed"), error: true };
}

async function send<T>(path: string, method: "POST" | "PATCH", body: unknown, csrfToken: string): Promise<T> {
  const response = await fetch(`/bff/api/${path}`, {
    method,
    headers: { "Content-Type": "application/json", "X-CSRF-Token": csrfToken },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (!response.ok) {
    const problem = (await response.json().catch(() => null)) as unknown;
    const p = (problem ?? {}) as { type?: unknown; title?: unknown };
    const str = (v: unknown) => (typeof v === "string" ? v : null);
    // Field-level messages (422 validation) come in `errors[]`, not `detail`.
    throw new ApiError(response.status, str(p.type)?.split("/").pop() ?? null, str(p.title), problemExplanation(problem));
  }
  return (await response.json()) as T;
}

/**
 * Submit once: the button stays disabled while the request runs and while the page re-renders
 * (a transition), so a second click cannot repeat a non-idempotent request.
 */
function useSubmit(csrfToken: string) {
  const router = useRouter();
  const pathname = usePathname();
  const [busy, setBusy] = useState(false);
  const [pending, startTransition] = useTransition();
  const [message, setMessage] = useState<Message | null>(null);
  const run = async <T,>(
    path: string,
    method: "POST" | "PATCH",
    body: unknown,
    done: FlashKey,
    next?: (result: T) => string | null,
    before?: () => Promise<unknown>,
  ) => {
    if (busy || pending) return;
    setBusy(true);
    setMessage(null);
    try {
      if (before) await before();
      const result = await send<T>(path, method, body, csrfToken);
      const base = next?.(result) ?? pathname;
      // The nonce makes a repeated change (same `done`) a new page state: announced and focused again.
      startTransition(() => router.replace(`${base}?done=${done}&n=${Date.now().toString(36)}`, { scroll: false }));
    } catch (error) {
      setMessage(explain(error));
    } finally {
      setBusy(false);
    }
  };
  return { busy: busy || pending, message, run };
}

function Status({ message }: { message: Message | null }) {
  if (!message) return <p role="status" aria-live="polite" className="text-sm" />;
  return (
    <p role="alert" className="text-sm font-semibold text-saffron-strong">
      {message.text}{" "}
      {message.signIn ? (
        <a className="underline" href={`/bff/auth/login?${message.reauth ? "reauth=1&" : ""}returnTo=${encodeURIComponent(window.location.pathname)}`}>{t("wf.signIn")}</a>
      ) : null}
    </p>
  );
}

/** Page-level outcome of the last change (from `?done=`), focused when it appears. */
export function Flash({ text }: { text: string }) {
  const [el, setEl] = useState<HTMLParagraphElement | null>(null);
  useEffect(() => el?.focus(), [el]);
  return (
    <p ref={setEl} tabIndex={-1} role="status" className="rounded-panel border border-divider bg-surface-2 p-3 font-semibold">
      {text}
    </p>
  );
}

function Panel({ title, intro, children }: { title: string; intro?: string; children: ReactNode }) {
  const id = useId();
  return (
    <section aria-labelledby={id} className="flex flex-col gap-3 rounded-panel border border-divider bg-surface-2 p-4">
      <h2 id={id} className="text-lg font-semibold text-navy">{title}</h2>
      {intro ? <p className="text-sm">{intro}</p> : null}
      {children}
    </section>
  );
}

function TextArea({ label, value, onChange, min, max, help, rows = 3 }: { label: string; value: string; onChange: (v: string) => void; min: number; max: number; help?: string; rows?: number }) {
  const id = useId();
  const helpId = useId();
  return (
    <div className="flex flex-col gap-1">
      <label htmlFor={id} className="text-sm font-medium">{label}</label>
      <textarea id={id} rows={rows} minLength={min} maxLength={max} required={min > 0} value={value} onChange={(e) => onChange(e.target.value)} aria-describedby={helpId} className={area} />
      <p id={helpId} className="text-xs">{help ?? t("wf.length", { min, max })}</p>
    </div>
  );
}

function Field({ label, children }: { label: string; children: (id: string) => ReactNode }) {
  const id = useId();
  return (
    <div className="flex flex-col gap-1">
      <label htmlFor={id} className="text-sm font-medium">{label}</label>
      {children(id)}
    </div>
  );
}

/** Why the main button is disabled, in words (not only a greyed-out button). */
/** Why an action is unavailable. Rendered (empty when there is nothing to say) so the button it
 * explains can reference it with `aria-describedby` (Phase 8 accessibility audit). */
function Hint({ id, text }: { id: string; text: string | null }) {
  return <p id={id} className="text-xs empty:hidden">{text ?? ""}</p>;
}

/**
 * Decisions that cannot be undone ask for confirmation: the first press names the choice, the
 * second confirms it.
 */
function DecisionButtons<C extends string>({ choices, disabled, onConfirm, describedBy }: { choices: { value: C; label: string; primary?: boolean }[]; disabled: boolean; onConfirm: (choice: C) => void; describedBy?: string }) {
  const [chosen, setChosen] = useState<C | null>(null);
  const picked = choices.find((c) => c.value === chosen);
  if (picked) {
    return (
      <div className="flex flex-wrap items-center gap-2" role="group" aria-label={t("wf.confirm.group")}>
        <p className="text-sm font-semibold">{t("wf.confirm.question", { choice: picked.label })}</p>
        <button type="button" className={primary} disabled={disabled} onClick={() => onConfirm(picked.value)}>{t("wf.confirm.yes", { choice: picked.label })}</button>
        <button type="button" className={button} disabled={disabled} onClick={() => setChosen(null)}>{t("wf.confirm.cancel")}</button>
      </div>
    );
  }
  return (
    <div className="flex flex-wrap gap-2">
      {choices.map((c) => (
        <button key={c.value} type="button" className={c.primary ? primary : button} disabled={disabled} aria-describedby={describedBy} onClick={() => setChosen(c.value)}>{c.label}</button>
      ))}
    </div>
  );
}

const enough = (text: string, min: number) => text.trim().length >= min;

// --- pattern reviews ---------------------------------------------------------------------------

/** The author's draft: revise, and submit (which saves the revision first, so nothing is lost). */
export function DraftReviewForm({ reviewId, narrative, nextStep, returned, csrfToken }: { reviewId: string; narrative: string; nextStep: string; returned: boolean; csrfToken: string }) {
  const hintId = useId();
  const [text, setText] = useState(narrative);
  const [next, setNext] = useState(nextStep);
  const [assessment, setAssessment] = useState("");
  const { busy, message, run } = useSubmit(csrfToken);
  const id = encodeURIComponent(reviewId);
  const revision = { narrative: text.trim(), recommended_next_step: next.trim() };
  const textOk = enough(text, 20) && enough(next, 5);
  const save = () => send(`pattern-reviews/${id}`, "PATCH", revision, csrfToken);
  return (
    <Panel title={returned ? t("wf.draft.titleReturned") : t("wf.draft.title")} intro={t("wf.submit.intro")}>
      <TextArea label={t("wf.revise.narrative")} value={text} onChange={setText} min={20} max={4000} rows={4} />
      <TextArea label={t("wf.revise.next")} value={next} onChange={setNext} min={5} max={1000} />
      <TextArea label={t("wf.submit.assessment")} value={assessment} onChange={setAssessment} min={10} max={4000} />
      <div className="flex flex-wrap gap-2">
        <button type="button" className={button} disabled={busy || !textOk} onClick={() => void run(`pattern-reviews/${id}`, "PATCH", revision, "revised")}>
          {t("wf.revise.button")}
        </button>
        <button
          type="button"
          className={primary}
          disabled={busy || !textOk || !enough(assessment, 10)}
          aria-describedby={hintId}
          onClick={() => void run(`pattern-reviews/${id}/submissions`, "POST", { analyst_assessment: assessment.trim() }, "submitted", undefined, save)}
        >
          {t("wf.submit.button")}
        </button>
      </div>
      <Hint id={hintId} text={!enough(assessment, 10) ? t("wf.hint.assessment") : null} />
      <Status message={message} />
    </Panel>
  );
}

export function DecideReviewForm({ reviewId, csrfToken }: { reviewId: string; csrfToken: string }) {
  const hintId = useId();
  const [rationale, setRationale] = useState("");
  const { busy, message, run } = useSubmit(csrfToken);
  return (
    <Panel title={t("wf.decide.title")} intro={t("wf.decide.intro")}>
      <TextArea label={t("wf.decide.rationale")} value={rationale} onChange={setRationale} min={10} max={4000} />
      <DecisionButtons
        describedBy={hintId}
        choices={[
          { value: "APPROVE", label: t("wf.decide.APPROVE"), primary: true },
          { value: "RETURN", label: t("wf.decide.RETURN") },
          { value: "REJECT", label: t("wf.decide.REJECT") },
        ]}
        disabled={busy || !enough(rationale, 10)}
        onConfirm={(decision) => void run(`pattern-reviews/${encodeURIComponent(reviewId)}/decisions`, "POST", { decision, rationale: rationale.trim() }, "decided")}
      />
      <Hint id={hintId} text={!enough(rationale, 10) ? t("wf.hint.rationale") : null} />
      <Status message={message} />
    </Panel>
  );
}

// --- operational actions -----------------------------------------------------------------------

export const ACTION_TYPES = ["PATROL_VISIBILITY", "COMMUNITY_ENGAGEMENT", "JOINT_INSPECTION", "ENVIRONMENTAL_REQUEST", "DATA_QUALITY_FIX", "OTHER"] as const;

type Station = { code: string; name: string };
type Assignee = { id: string; name: string };
type Loaded<T> = { state: "loading" } | { state: "error" } | { state: "ready"; data: T };

/** GET a list through the BFF; failures are reported, not shown as an empty list. */
function useList<T>(path: string | null): Loaded<T[]> {
  const [result, setResult] = useState<{ path: string | null; value: Loaded<T[]> }>({ path: null, value: { state: "loading" } });
  useEffect(() => {
    if (!path) return;
    const controller = new AbortController();
    fetch(`/bff/api/${path}`, { signal: controller.signal, headers: { Accept: "application/json" } })
      .then(async (r) => {
        if (!r.ok) throw new Error(String(r.status));
        const body = (await r.json()) as { data: T[] };
        setResult({ path, value: { state: "ready", data: body.data } });
      })
      .catch(() => {
        if (!controller.signal.aborted) setResult({ path, value: { state: "error" } });
      });
    return () => controller.abort();
  }, [path]);
  return result.path === path ? result.value : { state: "loading" };
}

function AssigneeSelect({ station, value, onChange, describedBy }: { station: string; value: string; onChange: (v: string) => void; describedBy?: string }) {
  const assignees = useList<Assignee>(`actions/assignees?station=${encodeURIComponent(station)}`);
  return (
    <Field label={t("wf.action.assignee")}>
      {(id) => (
        <>
          <select id={id} value={value} onChange={(e) => onChange(e.target.value)} className={input} disabled={assignees.state !== "ready"} aria-describedby={describedBy}>
            <option value="">{assignees.state === "loading" ? t("wf.loading") : assignees.state === "error" ? t("wf.list.failed") : t("wf.action.unassigned")}</option>
            {assignees.state === "ready" ? assignees.data.map((a) => <option key={a.id} value={a.id}>{a.name}</option>) : null}
          </select>
          {assignees.state === "ready" && assignees.data.length === 0 ? <p className="text-xs">{t("wf.action.noOfficers")}</p> : null}
          {assignees.state === "error" ? <p role="alert" className="text-xs text-saffron-strong">{t("wf.list.failed")}</p> : null}
        </>
      )}
    </Field>
  );
}

export function ProposeActionForm({ reviewId, cellKeys, region, station, csrfToken }: { reviewId: string; cellKeys: string[]; region: string; station: string | null; csrfToken: string }) {
  const hintId = useId();
  const stations = useList<Station>(`stations?region=${encodeURIComponent(region)}`);
  const [type, setType] = useState<(typeof ACTION_TYPES)[number]>("PATROL_VISIBILITY");
  const [description, setDescription] = useState("");
  const [cells, setCells] = useState<string[]>(cellKeys);
  const [chosenStation, setStation] = useState(station ?? "");
  const [assignee, setAssignee] = useState("");
  const [start, setStart] = useState("");
  const [end, setEnd] = useState("");
  const { busy, message, run } = useSubmit(csrfToken);
  const datesError = actionDatesError(start, end);
  const hint = !enough(description, 20)
    ? t("wf.hint.description")
    : cells.length === 0
      ? t("wf.hint.cells")
      : chosenStation === ""
        ? t("wf.hint.station")
        : datesError
          ? t(datesError)
          : null;
  return (
    <Panel title={t("wf.propose.title")} intro={t("wf.propose.intro")}>
      <Field label={t("wf.action.type")}>
        {(id) => (
          <select id={id} value={type} onChange={(e) => setType(e.target.value as (typeof ACTION_TYPES)[number])} className={input}>
            {ACTION_TYPES.map((a) => (
              <option key={a} value={a}>{t(`wf.actionType.${a}` as MessageKey)}</option>
            ))}
          </select>
        )}
      </Field>
      <TextArea label={t("wf.action.description")} value={description} onChange={setDescription} min={20} max={2000} help={t("wf.action.descriptionHelp")} />
      <fieldset className="flex flex-col gap-1">
        <legend className="text-sm font-medium">{t("wf.action.cells")}</legend>
        <div className="flex flex-wrap gap-3">
          {cellKeys.map((key) => (
            <label key={key} className="flex min-h-11 items-center gap-2 font-mono text-xs">
              <input type="checkbox" className="size-5" aria-label={t("wf.action.cell", { key })} checked={cells.includes(key)} onChange={(e) => setCells(e.target.checked ? [...cells, key] : cells.filter((c) => c !== key))} />
              {key}
            </label>
          ))}
        </div>
      </fieldset>
      <div className="flex flex-wrap gap-4">
        <Field label={t("wf.action.station")}>
          {(id) => (
            <>
              <select id={id} required value={chosenStation} onChange={(e) => { setStation(e.target.value); setAssignee(""); }} className={input} disabled={stations.state !== "ready"}>
                <option value="">{stations.state === "error" ? t("wf.list.failed") : stations.state === "loading" ? t("wf.loading") : t("wf.choose")}</option>
                {stations.state === "ready" ? stations.data.map((s) => <option key={s.code} value={s.code}>{s.code} · {s.name}</option>) : null}
              </select>
              {stations.state === "error" ? <p role="alert" className="text-xs text-saffron-strong">{t("wf.list.failed")}</p> : null}
            </>
          )}
        </Field>
        {chosenStation ? <AssigneeSelect key={chosenStation} station={chosenStation} value={assignee} onChange={setAssignee} /> : null}
        <Field label={t("wf.action.start")}>{(id) => <input id={id} type="date" required value={start} onChange={(e) => setStart(e.target.value)} className={input} />}</Field>
        <Field label={t("wf.action.end")}>{(id) => <input id={id} type="date" required value={end} min={start || undefined} onChange={(e) => setEnd(e.target.value)} className={input} />}</Field>
      </div>
      <p className="text-xs">{t("wf.action.stationHelp")}</p>
      <div>
        <button
          type="button"
          className={primary}
          disabled={busy || hint !== null}
          aria-describedby={hintId}
          onClick={() =>
            void run<{ id: string }>(
              "actions",
              "POST",
              { pattern_review_id: reviewId, action_type: type, description: description.trim(), cell_keys: cells, station: chosenStation, assignee_id: assignee || null, start_at: start, end_at: end },
              "proposed",
              (created) => `/review/actions/${encodeURIComponent(created.id)}`,
            )
          }
        >
          {t("wf.propose.button")}
        </button>
      </div>
      <Hint id={hintId} text={hint} />
      <Status message={message} />
    </Panel>
  );
}

export function ApproveActionForm({ actionId, station, assigneeName, csrfToken }: { actionId: string; station: string; assigneeName: string | null; csrfToken: string }) {
  const hintId = useId();
  const [rationale, setRationale] = useState("");
  const [assignee, setAssignee] = useState("");
  const keepId = useId();
  const { busy, message, run } = useSubmit(csrfToken);
  return (
    <Panel title={t("wf.approve.title")} intro={t("wf.approve.intro")}>
      <TextArea label={t("wf.approve.rationale")} value={rationale} onChange={setRationale} min={10} max={2000} />
      <AssigneeSelect station={station} value={assignee} onChange={setAssignee} describedBy={keepId} />
      <p id={keepId} className="text-xs">{assigneeName ? t("wf.approve.keepAssignee", { name: assigneeName }) : t("wf.approve.noAssignee")}</p>
      <DecisionButtons
        describedBy={hintId}
        choices={[
          { value: "APPROVE", label: t("wf.approve.APPROVE"), primary: true },
          { value: "REJECT", label: t("wf.approve.REJECT") },
        ]}
        disabled={busy || !enough(rationale, 10)}
        onConfirm={(decision) =>
          void run(`actions/${encodeURIComponent(actionId)}/approvals`, "POST", { decision, rationale: rationale.trim(), assignee_id: assignee || null }, decision === "APPROVE" ? "approved" : "rejected")
        }
      />
      <Hint id={hintId} text={!enough(rationale, 10) ? t("wf.hint.rationale") : null} />
      <Status message={message} />
    </Panel>
  );
}

export function CompleteActionForm({ actionId, csrfToken }: { actionId: string; csrfToken: string }) {
  const hintId = useId();
  const [evidence, setEvidence] = useState("");
  const [reference, setReference] = useState("");
  const { busy, message, run } = useSubmit(csrfToken);
  return (
    <Panel title={t("wf.complete.title")} intro={t("wf.complete.intro")}>
      <TextArea label={t("wf.complete.evidence")} value={evidence} onChange={setEvidence} min={10} max={2000} />
      <Field label={t("wf.complete.reference")}>{(id) => <input id={id} value={reference} maxLength={500} onChange={(e) => setReference(e.target.value)} className={input} />}</Field>
      <DecisionButtons
        describedBy={hintId}
        choices={[{ value: "COMPLETE", label: t("wf.complete.button"), primary: true }]}
        disabled={busy || !enough(evidence, 10)}
        onConfirm={() => void run(`actions/${encodeURIComponent(actionId)}/completions`, "POST", { evidence: evidence.trim(), reference: reference.trim() || null }, "completed")}
      />
      <Hint id={hintId} text={!enough(evidence, 10) ? t("wf.hint.evidence") : null} />
      <Status message={message} />
    </Panel>
  );
}

// --- outcome reviews ---------------------------------------------------------------------------

export function CreateOutcomeForm({ actionId, startAt, endAt, csrfToken }: { actionId: string; startAt: string; endAt: string; csrfToken: string }) {
  const hintId = useId();
  const [periods, setPeriods] = useState(() => suggestedPeriods(startAt, endAt));
  const [kind, setKind] = useState<"NONE" | "STATION" | "CELLS">("NONE");
  const [keys, setKeys] = useState("");
  const { busy, message, run } = useSubmit(csrfToken);
  const comparison = kind === "NONE" ? [] : kind === "STATION" ? [keys.trim()] : parseCellKeys(keys);
  const comparisonOk = kind === "NONE" || (kind === "STATION" ? /^[A-Z]{2,5}-S\d{2}$/.test(keys.trim()) : comparison !== null && comparison.length > 0);
  const periodError = periodsError(periods, startAt);
  const date = (name: keyof typeof periods, label: string) => (
    <Field label={label}>{(id) => <input id={id} type="date" required value={periods[name]} onChange={(e) => setPeriods({ ...periods, [name]: e.target.value })} className={input} />}</Field>
  );
  return (
    <Panel title={t("wf.outcome.create.title")} intro={t("wf.outcome.create.intro")}>
      <fieldset className="flex flex-wrap gap-4">
        <legend className="text-sm font-medium">{t("wf.outcome.baseline")}</legend>
        {date("baseline_start", t("wf.outcome.from"))}
        {date("baseline_end", t("wf.outcome.to"))}
      </fieldset>
      <fieldset className="flex flex-wrap gap-4">
        <legend className="text-sm font-medium">{t("wf.outcome.after")}</legend>
        {date("after_start", t("wf.outcome.from"))}
        {date("after_end", t("wf.outcome.to"))}
      </fieldset>
      <p aria-live="polite" className="text-sm text-saffron-strong">{periodError ? t(periodError) : ""}</p>
      <div className="flex flex-wrap gap-4">
        <Field label={t("wf.outcome.comparison")}>
          {(id) => (
            <select id={id} value={kind} onChange={(e) => setKind(e.target.value as typeof kind)} className={input}>
              <option value="NONE">{t("wf.outcome.comparison.NONE")}</option>
              <option value="STATION">{t("wf.outcome.comparison.STATION")}</option>
              <option value="CELLS">{t("wf.outcome.comparison.CELLS")}</option>
            </select>
          )}
        </Field>
        {kind === "NONE" ? null : (
          <Field label={kind === "STATION" ? t("wf.outcome.comparisonStation") : t("wf.outcome.comparisonCells")}>
            {(id) => <input id={id} required value={keys} onChange={(e) => setKeys(e.target.value)} className={`${input} min-w-72 font-mono`} />}
          </Field>
        )}
      </div>
      <div>
        <button
          type="button"
          className={primary}
          disabled={busy || periodError !== null || !comparisonOk}
          aria-describedby={hintId}
          onClick={() => void run("outcomes", "POST", { action_id: actionId, ...periods, comparison_kind: kind, comparison_keys: comparison ?? [] }, "outcomeStarted")}
        >
          {t("wf.outcome.create.button")}
        </button>
      </div>
      <Hint id={hintId} text={!comparisonOk ? t("wf.hint.comparison") : null} />
      <Status message={message} />
    </Panel>
  );
}

type Ref = { kind: "EVIDENCE_SNAPSHOT" | "DOCUMENT"; value: string };
const MAX_REFS = 20;

export function EditOutcomeForm({ outcomeId, notes, conclusion, references, csrfToken }: { outcomeId: string; notes: string; conclusion: string; references: Ref[]; csrfToken: string }) {
  const hintId = useId();
  const [text, setText] = useState(notes);
  const [concl, setConcl] = useState(conclusion);
  const [refs, setRefs] = useState<Ref[]>(references);
  const [kind, setKind] = useState<Ref["kind"]>("EVIDENCE_SNAPSHOT");
  const [value, setValue] = useState("");
  const refsTitle = useId();
  const { busy, message, run } = useSubmit(csrfToken);
  const id = encodeURIComponent(outcomeId);
  // Empty text clears the field (the API treats "" as clear).
  const body = { displacement_diffusion_notes: text.trim(), reviewer_conclusion: concl.trim(), evidence_references: refs };
  const candidate = value.trim();
  const duplicate = refs.some((r) => r.kind === kind && r.value === candidate);
  const addHint = refs.length >= MAX_REFS ? t("wf.outcome.maxReferences", { max: MAX_REFS }) : duplicate ? t("wf.outcome.duplicateReference") : null;
  const finaliseHint = refs.length === 0 ? t("wf.hint.reference") : !enough(concl, 20) ? t("wf.hint.conclusion") : null;
  const remove = (i: number) => {
    setRefs(refs.filter((_, j) => j !== i));
    document.getElementById(refsTitle)?.focus();
  };
  return (
    <Panel title={t("wf.outcome.edit.title")} intro={t("wf.outcome.edit.intro")}>
      <TextArea label={t("wf.outcome.notes")} value={text} onChange={setText} min={0} max={4000} help={t("wf.outcome.notesHelp")} rows={4} />
      <TextArea label={t("wf.outcome.conclusion")} value={concl} onChange={setConcl} min={0} max={4000} help={t("wf.outcome.conclusionHelp")} rows={4} />
      <fieldset className="flex flex-col gap-2">
        <legend id={refsTitle} tabIndex={-1} className="text-sm font-medium">{t("wf.outcome.references")}</legend>
        {refs.length === 0 ? <p className="text-sm">{t("wf.outcome.noReferences")}</p> : (
          <ul className="flex flex-col gap-1 text-sm">
            {refs.map((r, i) => (
              <li key={`${r.kind}:${r.value}`} className="flex items-center gap-2">
                <span>{t(`wf.ref.${r.kind}` as MessageKey)}:</span>
                <span className="break-all font-mono text-xs">{r.value}</span>
                <button type="button" className={button} disabled={busy} onClick={() => remove(i)} aria-label={t("wf.outcome.removeReference", { value: r.value })}>
                  {t("wf.remove")}
                </button>
              </li>
            ))}
          </ul>
        )}
        <div className="flex flex-wrap items-end gap-2">
          <Field label={t("wf.outcome.referenceKind")}>
            {(fid) => (
              <select id={fid} value={kind} onChange={(e) => setKind(e.target.value as Ref["kind"])} className={input}>
                <option value="EVIDENCE_SNAPSHOT">{t("wf.ref.EVIDENCE_SNAPSHOT")}</option>
                <option value="DOCUMENT">{t("wf.ref.DOCUMENT")}</option>
              </select>
            )}
          </Field>
          <Field label={kind === "EVIDENCE_SNAPSHOT" ? t("wf.outcome.snapshotId") : t("wf.outcome.documentRef")}>
            {(fid) => <input id={fid} value={value} maxLength={300} onChange={(e) => setValue(e.target.value)} className={`${input} min-w-72`} />}
          </Field>
          <button
            type="button"
            className={button}
            disabled={busy || candidate.length < 3 || addHint !== null}
            aria-describedby={hintId}
            onClick={() => {
              setRefs([...refs, { kind, value: candidate }]);
              setValue("");
            }}
          >
            {t("wf.outcome.addReference")}
          </button>
        </div>
        <Hint id={hintId} text={addHint} />
      </fieldset>
      <div className="flex flex-wrap gap-2">
        <button type="button" className={button} disabled={busy} onClick={() => void run(`outcomes/${id}`, "PATCH", body, "outcomeSaved")}>
          {t("wf.outcome.save")}
        </button>
      </div>
      <DecisionButtons
        choices={[{ value: "FINAL", label: t("wf.outcome.finalise"), primary: true }]}
        disabled={busy || finaliseHint !== null}
        describedBy={`${hintId}-finalise`}
        onConfirm={() => void run(`outcomes/${id}/finalisations`, "POST", undefined, "finalised", undefined, () => send(`outcomes/${id}`, "PATCH", body, csrfToken))}
      />
      <p id={`${hintId}-finalise`} className="text-xs">{finaliseHint ?? t("wf.outcome.finaliseHelp")}</p>
      <Status message={message} />
    </Panel>
  );
}

// --- AI insights (Phase 7) ---------------------------------------------------------------------

/** Validate or dismiss an insight with a note. Validation records a judgement; it changes nothing else. */
export function AiReviewForm({ insightId, csrfToken }: { insightId: string; csrfToken: string }) {
  const hintId = useId();
  const [note, setNote] = useState("");
  const { busy, message, run } = useSubmit(csrfToken);
  return (
    <Panel title={t("ai.review.title")} intro={t("ai.review.intro")}>
      <TextArea label={t("ai.review.note")} value={note} onChange={setNote} min={10} max={2000} />
      <DecisionButtons
        describedBy={hintId}
        choices={[
          { value: "VALIDATE", label: t("ai.review.VALIDATE"), primary: true },
          { value: "DISMISS", label: t("ai.review.DISMISS") },
        ]}
        disabled={busy || !enough(note, 10)}
        onConfirm={(decision) => void run(`ai/insights/${encodeURIComponent(insightId)}/reviews`, "POST", { decision, note: note.trim() }, "insightReviewed")}
      />
      <Hint id={hintId} text={!enough(note, 10) ? t("wf.hint.rationale") : null} />
      <Status message={message} />
    </Panel>
  );
}

/** "Add Note" on an insight card (analyst notes; the API checks the caller may see the insight). */
export function AddNoteForm({ subjectType, subjectId, csrfToken }: { subjectType: "AI_INSIGHT"; subjectId: string; csrfToken: string }) {
  const [note, setNote] = useState("");
  const { busy, message, run } = useSubmit(csrfToken);
  return (
    <Panel title={t("ai.note.title")}>
      <TextArea label={t("ai.note.label")} value={note} onChange={setNote} min={1} max={4000} help={t("ai.note.help")} />
      <div>
        <button type="button" className={button} disabled={busy || !enough(note, 1)} onClick={() => void run("notes", "POST", { subject_type: subjectType, subject_id: subjectId, note: note.trim(), visibility: "RESTRICTED" }, "noteAdded")}>
          {t("ai.note.add")}
        </button>
      </div>
      <Status message={message} />
    </Panel>
  );
}

/** A model's kill switch, or the global AI switch (ai.config; needs a recent sign-in). */
export function SwitchForm({ path, body, enabled, label, csrfToken }: { path: string; body: Record<string, unknown>; enabled: boolean; label: string; csrfToken: string }) {
  const [reason, setReason] = useState("");
  const { busy, message, run } = useSubmit(csrfToken);
  return (
    <div className="flex flex-col gap-2 rounded-panel border border-divider p-3">
      <TextArea label={t("ai.switch.reason", { label })} value={reason} onChange={setReason} min={10} max={500} rows={2} />
      <DecisionButtons
        choices={[{ value: "GO", label: enabled ? t("ai.switch.off", { label }) : t("ai.switch.on", { label }), primary: !enabled }]}
        disabled={busy || !enough(reason, 10)}
        onConfirm={() => void run(path, "POST", { ...body, enabled: !enabled, reason: reason.trim() }, "switched")}
      />
      <Status message={message} />
    </div>
  );
}

type Briefing = BriefingOut;

/** Fill the briefing template from validated insights. Shown here as a draft; nothing is stored. */
export function BriefingBuilder({ insights, csrfToken }: { insights: { id: string; text: string }[]; csrfToken: string }) {
  const hintId = useId();
  const [chosen, setChosen] = useState<string[]>([]);
  const [title, setTitle] = useState(t("ai.briefing.defaultTitle"));
  const [briefing, setBriefing] = useState<Briefing | null>(null);
  const [message, setMessage] = useState<Message | null>(null);
  const [busy, setBusy] = useState(false);
  const [article, setArticle] = useState<HTMLElement | null>(null);
  const resultId = useId();
  useEffect(() => article?.focus(), [article, briefing]);
  const choose = (next: string[]) => {
    setChosen(next);
    setBriefing(null); // a draft always matches the current selection
  };
  const make = async () => {
    setBusy(true);
    setMessage(null);
    setBriefing(null);
    try {
      setBriefing(await send<Briefing>("ai/briefings", "POST", { insight_ids: chosen, title: title.trim() }, csrfToken));
    } catch (error) {
      setMessage(explain(error));
    } finally {
      setBusy(false);
    }
  };
  const hint = chosen.length === 0 ? t("ai.briefing.hint") : title.trim().length < 5 ? t("ai.briefing.hintTitle") : chosen.length >= 20 ? t("ai.briefing.hintMax") : null;
  return (
    <div className="flex flex-col gap-4">
      <Panel title={t("ai.briefing.choose")} intro={t("ai.briefing.intro")}>
        {insights.length === 0 ? <p>{t("ai.briefing.none")}</p> : (
          <fieldset className="flex flex-col gap-2">
            <legend className="text-sm font-medium">{t("ai.briefing.insights")}</legend>
            {insights.map((i) => (
              <label key={i.id} className="flex min-h-11 items-start gap-2 text-sm">
                <input type="checkbox" className="mt-1 size-5" checked={chosen.includes(i.id)} disabled={!chosen.includes(i.id) && chosen.length >= 20} onChange={(e) => choose(e.target.checked ? [...chosen, i.id] : chosen.filter((c) => c !== i.id))} />
                {i.text}
              </label>
            ))}
          </fieldset>
        )}
        <Field label={t("ai.briefing.titleLabel")}>{(id) => <input id={id} value={title} maxLength={120} onChange={(e) => setTitle(e.target.value)} className={input} />}</Field>
        <div>
          <button type="button" className={primary} disabled={busy || chosen.length === 0 || title.trim().length < 5} aria-describedby={hintId} onClick={() => void make()}>{t("ai.briefing.make")}</button>
        </div>
        <Hint id={hintId} text={hint} />
        <Status message={message} />
      </Panel>
      {briefing ? (
        <article ref={setArticle} id={resultId} tabIndex={-1} aria-labelledby={`${resultId}-h`} className="flex flex-col gap-3 rounded-panel border border-divider p-4">
          <p className="font-semibold text-saffron-strong">{briefing.label}</p>
          <h2 id={`${resultId}-h`} className="text-xl font-semibold text-navy">{briefing.title}</h2>
          <p className="text-xs">{t("ai.briefing.meta", { template: briefing.template })}</p>
          {briefing.sections.map((s) => (
            <section key={s.heading}>
              <h3 className="font-semibold">{s.heading}</h3>
              {s.paragraphs.map((para) => <p key={para} className="mt-1 text-sm">{para}</p>)}
            </section>
          ))}
          <section>
            <h3 className="font-semibold">{t("ai.briefing.citations")}</h3>
            <ol className="list-decimal pl-6 text-xs">
              {briefing.citations.map((c) => (
                <li key={c.insight_id}>
                  {t("ai.briefing.citation", { model: c.model })}{" "}
                  <a className="underline" href={`/review/insights/${encodeURIComponent(c.insight_id)}`}>{t("ai.briefing.openInsight", { n: briefing.citations.indexOf(c) + 1 })}</a>{" "}
                  · <a className="underline" href={`/review/ai-runs/${encodeURIComponent(c.input_snapshot_id)}`}>{t("ai.card.openEvidence")}</a>
                </li>
              ))}
            </ol>
          </section>
        </article>
      ) : null}
    </div>
  );
}
