import Link from "next/link";
import type { ReactNode } from "react";
import { formatIst } from "@/lib/dq";
import { t, tn } from "@/lib/i18n";
import type { MessageKey } from "@/messages/en";
import { type HistoryItem, type QueueItem, type TabKey, type WorkflowQueue, daysUntil, itemHref, todayIst, visibleTabs } from "@/lib/workflow";

/** Server-rendered parts of the Review Queue workflow (Phase 6). */

export function TabNav({ permissions, active }: { permissions: readonly string[]; active: TabKey }) {
  return (
    <nav aria-label={t("wf.tabs")}>
      <ul className="flex flex-wrap gap-2 border-b border-divider pb-2">
        {visibleTabs(permissions).map((tab) => {
          const label = t(`wf.tab.${tab.key}` as MessageKey);
          if (tab.disabled) {
            return (
              <li key={tab.key}>
                <span className="inline-flex min-h-11 items-center rounded-control px-3 text-sm opacity-70">
                  {label} <span className="ml-1 text-xs">({t("wf.tab.later")})</span>
                </span>
              </li>
            );
          }
          const current = tab.key === active;
          return (
            <li key={tab.key}>
              <Link
                href={`/review?tab=${tab.key}`}
                aria-current={current ? "page" : undefined}
                className={`inline-flex min-h-11 items-center rounded-control px-3 text-sm font-semibold ${current ? "bg-navy text-on-navy" : "border border-control-border"}`}
              >
                {label}
              </Link>
            </li>
          );
        })}
      </ul>
    </nav>
  );
}

/** A date as "13 Oct 2026" (dates without a time). */
export function day(value: string | null | undefined): string {
  if (!value) return "—";
  return new Intl.DateTimeFormat("en-GB", { day: "numeric", month: "short", year: "numeric", timeZone: "UTC" }).format(new Date(`${value}T00:00:00Z`));
}

export function dueText(due: string, today: string = todayIst()): string {
  const days = daysUntil(due, today);
  return days < 0 ? tn("wf.overdueBy", -days) : days === 0 ? t("wf.dueToday") : tn("wf.dueIn", days);
}

function Due({ due }: { due: string | null }) {
  if (!due) return <>—</>;
  const overdue = daysUntil(due, todayIst()) < 0;
  return (
    <>
      {day(due)}
      <span className={`block text-xs ${overdue ? "font-semibold text-saffron-strong" : ""}`}>{dueText(due)}</span>
    </>
  );
}

export function areaText(item: Pick<QueueItem, "region" | "station" | "cells">): string {
  return `${item.station ?? item.region} · ${tn("wf.cells", item.cells)}`;
}

export function QueueFilters({ queue, values }: { queue: WorkflowQueue; values: URLSearchParams }) {
  const select = "min-h-11 rounded-control border border-control-border bg-surface px-2";
  return (
    <form method="get" aria-label={t("review.filters")} className="flex flex-wrap items-end gap-4 rounded-panel border border-divider bg-surface-2 p-4">
      <input type="hidden" name="tab" value={queue} />
      <label className="flex flex-col gap-1 text-sm font-medium">
        {t("wf.col.priority")}
        <select name="priority" defaultValue={values.get("priority") ?? ""} className={select}>
          <option value="">{t("review.any")}</option>
          {(["HIGH", "MEDIUM", "LOW"] as const).map((p) => <option key={p} value={p}>{t(`wf.priority.${p}`)}</option>)}
        </select>
      </label>
      <label className="flex flex-col gap-1 text-sm font-medium">
        {t("review.region")}
        <select name="region" defaultValue={values.get("region") ?? ""} className={select}>
          <option value="">{t("review.any")}</option>
          {["PDY", "KKL", "MAH", "YAN"].map((r) => <option key={r} value={r}>{r}</option>)}
        </select>
      </label>
      <label className="flex min-h-11 items-center gap-2 text-sm font-medium">
        <input type="checkbox" name="mine" value="1" defaultChecked={values.get("mine") === "true"} className="size-5" />
        {t(`wf.filter.mine.${queue}`)}
      </label>
      <label className="flex min-h-11 items-center gap-2 text-sm font-medium">
        <input type="checkbox" name="overdue" value="1" defaultChecked={values.get("overdue") === "true"} className="size-5" />
        {t("wf.filter.overdue")}
      </label>
      <button type="submit" className="min-h-11 rounded-control bg-navy px-4 text-sm font-semibold text-on-navy">{t("review.apply")}</button>
    </form>
  );
}

export function QueueTable({ items, caption, more }: { items: QueueItem[]; caption: string; more: boolean }) {
  if (items.length === 0) return <p>{more ? t("wf.emptyMore") : t("wf.empty")}</p>;
  return (
    <>
      {more ? <p className="text-sm font-semibold">{t("wf.more", { count: items.length })}</p> : null}
      <div className="overflow-x-auto">
        <table className="w-full min-w-[1000px] border-collapse text-left text-sm">
          <caption className="sr-only">{caption}</caption>
          <thead>
            <tr className="border-b border-control-border">
              {(["item", "priority", "opened", "category", "owner", "due", "status", "evidence", "next"] as const).map((c) => (
                <th key={c} scope="col" className="p-2">{t(`wf.col.${c}`)}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {items.map((item) => {
              const rowId = `row-${item.id}`;
              return (
                <tr key={`${item.type}:${item.id}`} className="border-b border-divider align-top">
                  <th scope="row" id={rowId} className="p-2 text-left font-normal">
                    <span className="font-semibold">{t(`wf.type.${item.type}`)}</span>
                    <span className="block">{areaText(item)}</span>
                  </th>
                  <td className="p-2 font-semibold">
                    <span className={item.priority === "HIGH" ? "text-saffron-strong" : undefined}>{t(`wf.priority.${item.priority}`)}</span>
                  </td>
                  <td className="p-2">{formatIst(item.opened_at)}</td>
                  <td className="p-2">{item.category ?? t("wf.notShown")}</td>
                  <td className="p-2">
                    {item.owner ?? "—"}
                    {item.mine ? <span className="block text-xs">{t("review.you")}</span> : null}
                  </td>
                  <td className="p-2"><Due due={item.due ?? null} /></td>
                  <td className="p-2">{t(`wf.status.${item.status}` as MessageKey)}</td>
                  <td className="p-2">
                    {item.evidence_snapshot_id ? (
                      <Link href={`/review/evidence/${encodeURIComponent(item.evidence_snapshot_id)}`} aria-describedby={rowId}>{t("wf.openEvidence")}</Link>
                    ) : (
                      t("wf.notShown")
                    )}
                  </td>
                  <td className="p-2">
                    <Link href={itemHref(item)} className="font-semibold" aria-describedby={rowId}>
                      {t(`wf.next.${item.next_step}` as MessageKey)}
                    </Link>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </>
  );
}

export function History({ items }: { items: HistoryItem[] | null }) {
  return (
    <section aria-labelledby="history-title" className="flex flex-col gap-2">
      <h2 id="history-title" className="text-xl font-semibold text-navy">{t("wf.history")}</h2>
      <p className="text-sm">{t("wf.historyIntro")}</p>
      {items === null ? <p>{t("wf.unavailable")}</p> : items.length === 0 ? <p>{t("wf.historyEmpty")}</p> : (
        <ol className="flex flex-col gap-2">
          {items.map((h, i) => (
            <li key={i} className="rounded-panel border border-divider p-3 text-sm">
              <p className="font-semibold">
                {h.from_state ? `${t(`wf.status.${h.from_state}` as MessageKey)} → ` : ""}
                {t(`wf.status.${h.to_state}` as MessageKey)}
              </p>
              <p>{formatIst(h.at)} · {h.actor ?? "—"}</p>
              {h.rationale ? <p className="mt-1 whitespace-pre-wrap">{h.rationale}</p> : null}
            </li>
          ))}
        </ol>
      )}
    </section>
  );
}

export function Facts({ rows }: { rows: [string, ReactNode][] }) {
  return (
    <dl className="grid grid-cols-[minmax(10rem,auto)_1fr] gap-x-4 gap-y-1 text-sm">
      {rows.map(([label, value]) => (
        <div key={label} className="contents">
          <dt className="font-medium">{label}</dt>
          <dd className="whitespace-pre-wrap">{value ?? "—"}</dd>
        </div>
      ))}
    </dl>
  );
}

export function Notice({ title, text }: { title: string; text: string }) {
  return (
    <section aria-labelledby="page-title" className="max-w-3xl">
      <h1 id="page-title" className="text-2xl font-semibold text-navy">{title}</h1>
      <p className="mt-2">{text}</p>
    </section>
  );
}
