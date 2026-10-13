"use client";

import { useRouter } from "next/navigation";
import { useEffect, useId, useRef, useState } from "react";

type Action = "ASSIGNED" | "OPEN" | "RESOLVED" | "ACCEPTED_AS_IS";

export type DqActionLabels = Record<Action, string> & {
  done: Record<Action, string>;
  note: string;
  noteHelp: string;
  confirm: string;
  cancel: string;
  failed: string;
  conflict: string;
  forbidden: string;
};

const CLOSING = new Set<Action>(["RESOLVED", "ACCEPTED_AS_IS"]);

/**
 * State changes for one data-quality issue. Requests go through the BFF (session cookie + CSRF
 * token); the API decides whether the change is allowed and audits it. Closing an issue needs a
 * note. Focus moves into the note field when it opens and back to the button that opened it on
 * cancel; outcomes are announced in a polite live region (the row may leave a filtered list).
 */
export function DqIssueActions({
  issueId,
  actions,
  csrfToken,
  labels,
}: {
  issueId: string;
  actions: string[];
  csrfToken: string;
  labels: DqActionLabels;
}) {
  const router = useRouter();
  const noteId = useId();
  const noteRef = useRef<HTMLTextAreaElement>(null);
  const triggers = useRef<Partial<Record<Action, HTMLButtonElement | null>>>({});
  const [closing, setClosing] = useState<Action | null>(null);
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [announcement, setAnnouncement] = useState("");

  useEffect(() => {
    if (closing) noteRef.current?.focus();
  }, [closing]);

  function cancel() {
    const opener = closing;
    setClosing(null);
    setError(null);
    requestAnimationFrame(() => {
      if (opener) triggers.current[opener]?.focus();
    });
  }

  async function send(state: Action, resolutionNote?: string) {
    setBusy(true);
    setError(null);
    try {
      const response = await fetch(`/bff/api/dq/issues/${encodeURIComponent(issueId)}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json", "X-CSRF-Token": csrfToken },
        body: JSON.stringify(resolutionNote ? { state, resolution_note: resolutionNote } : { state }),
      });
      if (!response.ok) {
        setError(
          response.status === 409
            ? labels.conflict
            : response.status === 403 || response.status === 404
              ? labels.forbidden
              : labels.failed,
        );
        return;
      }
      setClosing(null);
      setNote("");
      setAnnouncement(labels.done[state]);
      router.refresh();
    } catch {
      setError(labels.failed);
    } finally {
      setBusy(false);
    }
  }

  const button =
    "min-h-11 rounded-control border border-control-border px-3 text-sm font-medium hover:bg-surface-2 disabled:opacity-60";

  return (
    <div>
      <p role="status" aria-live="polite" className="sr-only">
        {announcement}
      </p>
      {closing ? (
        <form
          className="flex flex-col gap-2"
          onSubmit={(event) => {
            event.preventDefault();
            void send(closing, note.trim());
          }}
          onKeyDown={(event) => {
            if (event.key === "Escape") cancel();
          }}
        >
          <label htmlFor={noteId} className="text-sm font-medium">
            {labels.note}
          </label>
          <textarea
            id={noteId}
            ref={noteRef}
            required
            minLength={5}
            maxLength={2000}
            rows={2}
            value={note}
            onChange={(event) => setNote(event.target.value)}
            aria-describedby={`${noteId}-help`}
            className="rounded-control border border-control-border bg-surface p-2 text-sm"
          />
          <p id={`${noteId}-help`} className="text-xs">
            {labels.noteHelp}
          </p>
          <div className="flex gap-2">
            <button type="submit" disabled={busy || note.trim().length < 5} className={button}>
              {labels.confirm}: {labels[closing]}
            </button>
            <button type="button" onClick={cancel} className={button}>
              {labels.cancel}
            </button>
          </div>
        </form>
      ) : (
        <div className="flex flex-wrap gap-2">
          {actions
            .filter((a): a is Action => a in labels.done)
            .map((state) => (
              <button
                key={state}
                ref={(el) => {
                  triggers.current[state] = el;
                }}
                type="button"
                disabled={busy}
                onClick={() => (CLOSING.has(state) ? setClosing(state) : void send(state))}
                className={button}
              >
                {labels[state]}
              </button>
            ))}
        </div>
      )}
      {error ? (
        <p role="alert" className="mt-1 text-sm text-saffron-strong">
          {error}
        </p>
      ) : null}
    </div>
  );
}
