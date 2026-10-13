"use client";

import type { FilterMetadata } from "@/lib/map/client";
import type { MinConfidence, TimeBasis } from "@/lib/map/query";
import { useMapStore } from "@/lib/map/store";
import { t } from "@/lib/i18n";
import type { MessageKey } from "@/messages/en";

const control = "min-h-11 rounded-control border border-control-border bg-surface px-2 text-sm";
const label = "flex flex-col gap-1 text-sm font-medium";

/** Filter controls. Changes are drafts until Apply, so counts never silently mix two filter sets. */
export function FilterBar({ meta }: { meta: FilterMetadata }) {
  const { draft, applied, setDraft, apply, reset, switchRegion } = useMapStore();
  const dirty = JSON.stringify(draft) !== JSON.stringify(applied);
  const beats = meta.beats.filter((b) => draft.stations.length === 0 || draft.stations.includes(b.station_code));
  // One option per status code (several record types share codes such as CLOSED).
  const statuses = [
    ...new Map(
      meta.statuses
        .filter((s) => draft.recordTypes.length === 0 || draft.recordTypes.includes(s.record_type))
        .map((s) => [s.code, s] as const),
    ).values(),
  ].sort((a, b) => a.label.localeCompare(b.label));
  return (
    <form
      aria-label={t("map.filters")}
      className="flex flex-wrap items-end gap-3 border-b border-divider bg-surface-2 px-4 py-3"
      onSubmit={(e) => {
        e.preventDefault();
        apply();
      }}
    >
      <label className={label}>
        {t("map.region")}
        <select className={control} value={draft.region} onChange={(e) => switchRegion(e.target.value)}>
          {meta.regions.map((r) => (
            <option key={r.code} value={r.code}>{r.name}</option>
          ))}
        </select>
      </label>
      <label className={label}>
        {t("map.station")}
        <select className={control} value={draft.stations[0] ?? ""} onChange={(e) => setDraft({ stations: e.target.value ? [e.target.value] : [], beats: [] })}>
          <option value="">{t("map.allStations")}</option>
          {meta.stations.map((s) => (
            <option key={s.code} value={s.code}>{s.name}</option>
          ))}
        </select>
      </label>
      <label className={label}>
        {t("map.beat")}
        <select className={control} value={draft.beats[0] ?? ""} onChange={(e) => setDraft({ beats: e.target.value ? [e.target.value] : [] })}>
          <option value="">{t("map.allBeats")}</option>
          {beats.map((b) => (
            <option key={b.code} value={b.code}>{b.code}</option>
          ))}
        </select>
      </label>
      <label className={label}>
        {t("map.category")}
        <select className={control} value={draft.categories[0] ?? ""} onChange={(e) => setDraft({ categories: e.target.value ? [e.target.value] : [] })}>
          <option value="">{t("map.allCategories")}</option>
          {meta.categories.map((c) => (
            <option key={c.code} value={c.code}>{c.label}</option>
          ))}
        </select>
      </label>
      <label className={label}>
        {t("map.recordType")}
        <select className={control} value={draft.recordTypes[0] ?? ""} onChange={(e) => setDraft({ recordTypes: e.target.value ? [e.target.value] : [], statuses: [] })}>
          <option value="">{t("map.any")}</option>
          {meta.record_types.map((r) => (
            <option key={r} value={r}>{r.replace("_", " ")}</option>
          ))}
        </select>
      </label>
      <fieldset className="flex items-end gap-2">
        <legend className="text-sm font-medium">{t("map.period")}</legend>
        <label className={label}>
          <span className="sr-only">{t("map.from")}</span>
          <input type="date" className={control} value={draft.periodFrom} max={draft.periodTo} onChange={(e) => setDraft({ periodFrom: e.target.value })} aria-label={t("map.from")} />
        </label>
        <label className={label}>
          <span className="sr-only">{t("map.to")}</span>
          <input type="date" className={control} value={draft.periodTo} min={draft.periodFrom} onChange={(e) => setDraft({ periodTo: e.target.value })} aria-label={t("map.to")} />
        </label>
      </fieldset>
      <label className={label}>
        {t("map.timeBasis")}
        <select className={control} value={draft.timeBasis} onChange={(e) => setDraft({ timeBasis: e.target.value as TimeBasis })}>
          <option value="OCCURRENCE">{t("map.basis.OCCURRENCE")}</option>
          <option value="REPORTED">{t("map.basis.REPORTED")}</option>
        </select>
      </label>
      <label className={label}>
        {t("map.status")}
        <select className={control} value={draft.statuses[0] ?? ""} onChange={(e) => setDraft({ statuses: e.target.value ? [e.target.value] : [] })}>
          <option value="">{t("map.any")}</option>
          {statuses.map((s) => (
            <option key={s.code} value={s.code}>{s.label}</option>
          ))}
        </select>
      </label>
      <label className={label}>
        {t("map.confidence")}
        <select className={control} value={draft.minConfidence} onChange={(e) => setDraft({ minConfidence: e.target.value as MinConfidence })}>
          {(["0", "0.4", "0.8"] as const).map((v) => (
            <option key={v} value={v}>{t(`map.confidence.${v}` as MessageKey)}</option>
          ))}
        </select>
      </label>
      <div className="flex gap-2">
        <button type="submit" className="min-h-11 rounded-control bg-navy px-4 text-sm font-semibold text-on-navy">{t("map.apply")}</button>
        <button type="button" onClick={reset} className="min-h-11 rounded-control border border-control-border px-4 text-sm font-medium">{t("map.reset")}</button>
      </div>
      {dirty ? <p role="status" className="w-full text-sm text-saffron-strong">{t("map.unapplied")}</p> : null}
    </form>
  );
}
