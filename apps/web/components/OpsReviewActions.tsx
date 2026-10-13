"use client";

import { useRouter } from "next/navigation";
import { useId, useState } from "react";

type Labels = {
  approve: string;
  reject: string;
  note: string;
  done: string;
  failed: string;
  own: string;
  notAllowed: string;
  alreadyDecided: string;
};

/** Approve or reject a pending operational record (BFF + CSRF; the API enforces role, scope and SoD). */
export function OpsReviewActions({ id, own, csrfToken, labels }: { id: string; own: boolean; csrfToken: string; labels: Labels }) {
  const router = useRouter();
  const noteId = useId();
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  if (own) return <p className="text-sm">{labels.own}</p>;
  const decide = async (decision: "APPROVE" | "REJECT") => {
    setBusy(true);
    setMessage(null);
    try {
      const r = await fetch(`/bff/api/operational-records/${encodeURIComponent(id)}/reviews`, {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-CSRF-Token": csrfToken },
        body: JSON.stringify({ decision, note: note.trim() }),
      });
      // 403: role/territory does not cover it; 409: decided by someone else, or one's own record.
      setMessage(r.ok ? labels.done : r.status === 403 ? labels.notAllowed : r.status === 409 ? labels.alreadyDecided : labels.failed);
      if (r.ok) router.refresh();
    } catch {
      setMessage(labels.failed);
    } finally {
      setBusy(false);
    }
  };
  const btn = "min-h-11 rounded-control border border-control-border px-3 text-sm font-medium disabled:opacity-60";
  return (
    <div className="flex flex-col gap-2">
      <label htmlFor={noteId} className="text-sm font-medium">{labels.note}</label>
      <textarea id={noteId} rows={2} minLength={3} maxLength={2000} value={note} onChange={(e) => setNote(e.target.value)} className="rounded-control border border-control-border bg-surface p-2 text-sm" />
      <div className="flex gap-2">
        <button type="button" className={btn} disabled={busy || note.trim().length < 3} onClick={() => void decide("APPROVE")}>{labels.approve}</button>
        <button type="button" className={btn} disabled={busy || note.trim().length < 3} onClick={() => void decide("REJECT")}>{labels.reject}</button>
      </div>
      <p role="status" aria-live="polite" className="text-sm">{message}</p>
    </div>
  );
}
