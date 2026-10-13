"use client";

import * as Dialog from "@radix-ui/react-dialog";
import { useQuery } from "@tanstack/react-query";
import { useRef, useState } from "react";
import { BOARDS, type BoardName, type DashboardFilters, type TrendBoard, movement, trendsQuery } from "@/lib/dashboard/query";
import { getJson } from "@/lib/map/client";
import { formatIst } from "@/lib/dq";
import { t } from "@/lib/i18n";
import type { MessageKey } from "@/messages/en";

export type MapTarget = { region: string; kind: "region" | "station" | "cell"; code: string };

const n = (v: number) => v.toLocaleString("en-IN");

/**
 * TRENDS: seven ranking boards over the dashboard's current region, station, category and period.
 * Each board states its period, category, time basis, scope, definition, comparison, data-quality
 * caveat, refresh time and the export/logging notice (PD-4). Tabs follow the ARIA tabs pattern
 * (arrow keys move between them).
 */
export function TrendsDialog({ filters, onViewOnMap }: { filters: DashboardFilters; onViewOnMap: (target: MapTarget) => void }) {
  const [open, setOpen] = useState(false);
  const [boardName, setBoard] = useState<BoardName>("top-spot");
  const tabs = useRef<(HTMLButtonElement | null)[]>([]);
  const query = `dashboard/trends/${boardName}?${trendsQuery(filters)}`;
  const board = useQuery({
    queryKey: ["trend", query],
    queryFn: ({ signal }) => getJson<TrendBoard>(query, signal),
    enabled: open,
    retry: false,
  });
  const b = board.data;
  const showRate = boardName === "station-rate";
  const moveTo = (i: number) => {
    const next = BOARDS[(i + BOARDS.length) % BOARDS.length] as BoardName;
    setBoard(next);
    tabs.current[BOARDS.indexOf(next)]?.focus();
  };
  return (
    <Dialog.Root open={open} onOpenChange={setOpen}>
      <Dialog.Trigger className="min-h-11 rounded-control bg-navy px-4 text-sm font-semibold tracking-wide text-on-navy">{t("dash.trends")}</Dialog.Trigger>
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 bg-ink/40" />
        <Dialog.Content className="fixed left-1/2 top-1/2 flex max-h-[92vh] w-[min(1080px,96vw)] -translate-x-1/2 -translate-y-1/2 flex-col gap-3 overflow-y-auto rounded-panel bg-surface p-6 shadow-lg">
          <div className="flex items-start justify-between gap-4">
            <Dialog.Title className="text-xl font-semibold text-navy">{t("trends.title")}</Dialog.Title>
            <Dialog.Close className="min-h-11 min-w-11 rounded-control border border-control-border" aria-label={t("trends.close")}>✕</Dialog.Close>
          </div>
          <Dialog.Description className="text-sm">{t("trends.neutral")}</Dialog.Description>
          <div role="tablist" aria-label={t("trends.tabs")} className="flex flex-wrap gap-1 border-b border-divider">
            {BOARDS.map((name, i) => (
              <button
                key={name}
                ref={(el) => {
                  tabs.current[i] = el;
                }}
                type="button"
                role="tab"
                id={`tab-${name}`}
                aria-selected={boardName === name}
                aria-controls="trend-panel"
                tabIndex={boardName === name ? 0 : -1}
                onClick={() => setBoard(name)}
                onKeyDown={(e) => {
                  if (e.key === "ArrowRight") moveTo(i + 1);
                  else if (e.key === "ArrowLeft") moveTo(i - 1);
                  else if (e.key === "Home") moveTo(0);
                  else if (e.key === "End") moveTo(BOARDS.length - 1);
                }}
                className="min-h-11 rounded-t-control px-3 text-sm font-medium aria-selected:border-b-4 aria-selected:border-navy aria-selected:font-semibold"
              >
                {t(`trends.board.${name}` as MessageKey)}
              </button>
            ))}
          </div>
          <section id="trend-panel" role="tabpanel" aria-labelledby={`tab-${boardName}`} className="flex flex-col gap-3">
            {board.isPending ? <p role="status">{t("dash.loading")}</p> : null}
            {board.isError ? <p role="alert">{t("dash.error")}</p> : null}
            {b ? (
              <>
                <ul className="grid gap-x-6 gap-y-1 text-sm md:grid-cols-2">
                  <li>{t("trends.period", { from: b.period.period_from, to: b.period.period_to })}</li>
                  <li>{t("trends.category", { category: b.category })}</li>
                  <li>{t("trends.basis", { basis: t(`map.basis.${b.period.basis}` as MessageKey) })}</li>
                  <li>{t("trends.scope", { scope: b.scope.label })}</li>
                  <li className="md:col-span-2">{t("trends.measure", { measure: b.measure })}</li>
                  <li className="md:col-span-2">{t("trends.comparison", { comparison: b.comparison })}</li>
                  <li className="md:col-span-2">{t("trends.quality", { caveat: b.quality.caveat })}</li>
                  {b.last_refreshed_at ? <li>{t("trends.refreshed", { time: formatIst(b.last_refreshed_at) })}</li> : null}
                  <li className="text-xs font-semibold uppercase tracking-wide text-saffron-strong">{b.data_label}</li>
                </ul>
                {b.rate ? (
                  <p role="note" className={`rounded-control border p-2 text-sm font-semibold ${b.rate.available ? "border-divider" : "border-saffron-strong text-saffron-strong"}`}>
                    {b.rate.label}
                  </p>
                ) : null}
                {b.rows.length === 0 ? (
                  <p>{t("trends.empty")}</p>
                ) : (
                  <table className="w-full border-collapse text-sm">
                    <caption className="sr-only">{t(`trends.board.${boardName}` as MessageKey)}</caption>
                    <thead>
                      <tr className="border-b border-divider text-left">
                        <th scope="col" className="p-2">{t("trends.rank")}</th>
                        <th scope="col" className="p-2">{t("trends.name")}</th>
                        <th scope="col" className="p-2 text-right">{t("trends.count")}</th>
                        {showRate ? <th scope="col" className="p-2">{t("trends.rate")}</th> : null}
                        <th scope="col" className="p-2">{t("trends.change")}</th>
                        <th scope="col" className="p-2"><span className="sr-only">{t("trends.map")}</span></th>
                      </tr>
                    </thead>
                    <tbody>
                      {b.rows.map((row) => {
                        const m = movement(row);
                        return (
                          <tr key={row.key} className="border-b border-divider">
                            <td className="p-2 tabular-nums">{row.rank}</td>
                            <th scope="row" className="p-2 text-left font-normal">{row.label}</th>
                            <td className="p-2 text-right tabular-nums">{n(row.count)}</td>
                            {showRate ? <td className="p-2">{row.rate !== null && row.rate !== undefined ? `${row.rate} ${row.rate_label ?? ""}` : row.rate_label}</td> : null}
                            <td className="p-2">
                              <span aria-hidden>{m.symbol}</span>
                              <span className="sr-only">{m.text}</span>
                            </td>
                            <td className="p-2">
                              {row.view_on_map ? (
                                <button
                                  type="button"
                                  className="min-h-11 rounded-control border border-control-border px-3"
                                  aria-label={t("trends.viewOnMapFor", { name: row.label })}
                                  onClick={() => {
                                    const target = row.view_on_map;
                                    if (!target) return;
                                    setOpen(false);
                                    onViewOnMap(target);
                                  }}
                                >
                                  {t("trends.viewOnMap")}
                                </button>
                              ) : null}
                            </td>
                          </tr>
                        );
                      })}
                    </tbody>
                  </table>
                )}
                {b.total_rows > b.rows.length ? <p className="text-xs">{t("trends.more", { shown: String(b.rows.length), total: n(b.total_rows) })}</p> : null}
                <p role="note" className="rounded-control bg-surface-2 p-2 text-sm">{b.notice}</p>
                <details className="text-sm">
                  <summary className="min-h-11 cursor-pointer content-center font-semibold text-navy">{t("trends.about")}</summary>
                  <ul className="list-disc pl-5">
                    {b.limitations.map((l) => (
                      <li key={l}>{l}</li>
                    ))}
                  </ul>
                </details>
              </>
            ) : null}
          </section>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
