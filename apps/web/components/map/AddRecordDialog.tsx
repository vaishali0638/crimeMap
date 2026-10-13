"use client";

import { zodResolver } from "@hookform/resolvers/zod";
import * as Dialog from "@radix-ui/react-dialog";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { useForm, useWatch } from "react-hook-form";
import { ApiError, type FilterMetadata, type OperationalRecord, postJson } from "@/lib/map/client";
import { istDate } from "@/lib/map/query";
import { RECORD_KINDS, REVIEWED_KINDS, type RecordForm, UNAVAILABLE_KINDS, recordFormSchema, toApiBody } from "@/lib/map/record-form";
import { useMapStore } from "@/lib/map/store";
import { t } from "@/lib/i18n";
import type { MessageKey } from "@/messages/en";

const input = "min-h-11 rounded-control border border-control-border bg-surface px-2 text-sm";
const field = "flex flex-col gap-1 text-sm font-medium";

function nowIst(): string {
  const now = new Date();
  const time = new Intl.DateTimeFormat("en-GB", { timeZone: "Asia/Kolkata", hour: "2-digit", minute: "2-digit", hourCycle: "h23" }).format(now);
  return `${istDate(now)}T${time}`;
}

export function AddRecordDialog({ meta, csrfToken }: { meta: FilterMetadata; csrfToken: string }) {
  const [done, setDone] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [typed, setTyped] = useState({ lat: "", lon: "" });
  const client = useQueryClient();
  const { applied, selection, pickedPoint, picking, startPicking, pick, dialogOpen: open, setDialogOpen: setOpen } = useMapStore();
  const subject = selection?.kind === "incident" ? selection.id : null;
  const form = useForm<RecordForm>({
    resolver: zodResolver(recordFormSchema),
    defaultValues: {
      kind: "OPERATIONAL_OBSERVATION",
      title: "",
      occurredAt: nowIst(),
      locality: "",
      point: null,
      region: applied.region,
      station: applied.stations[0] ?? "",
      precision: "APPROXIMATE",
      confidence: 0.6,
      reason: "",
      evidence: "",
      visibility: "RESTRICTED",
      subjectIncidentId: subject,
    },
  });
  const kind = useWatch({ control: form.control, name: "kind" });

  useEffect(() => {
    form.setValue("point", pickedPoint);
  }, [pickedPoint, form]);
  useEffect(() => {
    form.setValue("subjectIncidentId", subject);
  }, [subject, form]);

  const submit = useMutation({
    mutationFn: (values: RecordForm) => postJson<OperationalRecord>("operational-records", toApiBody(values), csrfToken),
    onSuccess: (record) => {
      setDone(record.review_status === "PENDING" ? t("record.doneReview") : t("record.done"));
      setError(null);
      form.reset({ ...form.getValues(), title: "", reason: "", evidence: "", point: null });
      pick(null);
      void client.invalidateQueries({ queryKey: ["records"] });
    },
    onError: (e) => {
      setError(
        e instanceof ApiError && e.status === 403
          ? t("map.status.forbidden")
          : e instanceof ApiError && e.status === 401
            ? t("map.status.signIn")
            : e instanceof ApiError && e.explanation
              ? t("record.rejected", { reason: e.explanation })
              : t("drawer.failed"),
      );
    },
  });
  const errors = form.formState.errors;
  const point = useWatch({ control: form.control, name: "point" });

  return (
    <Dialog.Root
      open={open && !picking}
      onOpenChange={(value) => {
        if (value) {
          // A record is made in the map's current region (stations listed are that region's);
          // the time starts at "now" each time the dialog opens.
          form.setValue("region", applied.region);
          if (!form.getValues("point")) form.setValue("station", applied.stations[0] ?? "");
          if (!form.formState.dirtyFields.occurredAt) form.setValue("occurredAt", nowIst());
          setError(null);
        }
        setOpen(value);
        if (!value) setDone(null);
      }}
    >
      <Dialog.Trigger className="min-h-11 rounded-control bg-navy px-4 text-sm font-semibold text-on-navy">{t("map.addRecord")}</Dialog.Trigger>
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 bg-ink/40" />
        <Dialog.Content className="fixed left-1/2 top-1/2 max-h-[90vh] w-[min(640px,95vw)] -translate-x-1/2 -translate-y-1/2 overflow-y-auto rounded-panel bg-surface p-6 shadow-lg">
          <Dialog.Title className="text-xl font-semibold text-navy">{t("record.title")}</Dialog.Title>
          <Dialog.Description className="mt-1 text-sm">{t("record.intro")}</Dialog.Description>
          {done ? (
            <div className="mt-4 flex flex-col gap-4">
              <p role="status" className="font-semibold">{done}</p>
              <Dialog.Close className="min-h-11 rounded-control border border-control-border px-4 text-sm">{t("drawer.close")}</Dialog.Close>
            </div>
          ) : (
            <form className="mt-4 grid grid-cols-2 gap-4" onSubmit={form.handleSubmit((values) => submit.mutate(values))} noValidate>
              <label className={`${field} col-span-2`}>
                {t("record.kind")}
                <select className={input} {...form.register("kind")}>
                  {RECORD_KINDS.map((k) => (
                    <option key={k} value={k} disabled={UNAVAILABLE_KINDS.has(k)}>
                      {UNAVAILABLE_KINDS.has(k) ? t("record.kindUnavailable", { label: t(`record.kind.${k}` as MessageKey) }) : t(`record.kind.${k}` as MessageKey)}
                    </option>
                  ))}
                </select>
                <span className="text-xs font-normal">{t("record.noFir")}</span>
              </label>
              {REVIEWED_KINDS.has(kind) ? <p className="col-span-2 text-sm text-saffron-strong">{t("record.reviewNote")}</p> : null}
              {kind === "LOCATION_CORRECTION_REQUEST" && !subject ? <p className="col-span-2 text-sm text-saffron-strong">{t("record.subjectHelp")}</p> : null}
              <label className={`${field} col-span-2`}>
                {t("record.titleField")}
                <input className={input} {...form.register("title")} aria-invalid={!!errors.title} />
              </label>
              <label className={field}>
                {t("record.occurredAt")}
                <input type="datetime-local" className={input} {...form.register("occurredAt")} />
              </label>
              <label className={field}>
                {t("record.visibility")}
                <select className={input} {...form.register("visibility")}>
                  <option value="RESTRICTED">RESTRICTED</option>
                  <option value="INTERNAL">INTERNAL</option>
                </select>
              </label>
              <fieldset className="col-span-2 flex flex-col gap-2">
                <legend className="text-sm font-medium">{t("record.location")}</legend>
                <div className="flex flex-wrap items-center gap-2 text-sm">
                  <button type="button" className="min-h-11 rounded-control border border-control-border px-3" onClick={() => startPicking()}>{t("record.pick")}</button>
                  {point ? (
                    <>
                      <span>{t("record.picked", { lat: point.lat.toFixed(5), lon: point.lon.toFixed(5) })}</span>
                      <button type="button" className="min-h-11 rounded-control border border-control-border px-3" onClick={() => pick(null)}>{t("record.clearPoint")}</button>
                    </>
                  ) : null}
                </div>
                <div className="flex flex-wrap items-end gap-2">
                  <label className={field}>
                    {t("record.lat")}
                    <input inputMode="decimal" className={`${input} w-32`} value={typed.lat} onChange={(e) => setTyped((v) => ({ ...v, lat: e.target.value }))} />
                  </label>
                  <label className={field}>
                    {t("record.lon")}
                    <input inputMode="decimal" className={`${input} w-32`} value={typed.lon} onChange={(e) => setTyped((v) => ({ ...v, lon: e.target.value }))} />
                  </label>
                  <button
                    type="button"
                    className="min-h-11 rounded-control border border-control-border px-3 text-sm"
                    disabled={!Number.isFinite(Number.parseFloat(typed.lat)) || !Number.isFinite(Number.parseFloat(typed.lon))}
                    onClick={() => pick({ lat: Number.parseFloat(typed.lat), lon: Number.parseFloat(typed.lon) })}
                  >
                    {t("record.setPoint")}
                  </button>
                </div>
                <label className={field}>
                  {t("record.locality")}
                  <input className={input} {...form.register("locality")} aria-invalid={!!errors.locality} />
                </label>
                {errors.locality || errors.point ? <p role="alert" className="text-sm text-saffron-strong">{errors.locality?.message ?? errors.point?.message}</p> : null}
              </fieldset>
              <p className={`${field} justify-end`}>
                {t("record.regionFixed", { region: meta.regions.find((r) => r.code === applied.region)?.name ?? applied.region })}
              </p>
              <label className={field}>
                {t("record.station")}
                <select className={input} {...form.register("station")}>
                  <option value="">—</option>
                  {meta.stations.map((s) => (
                    <option key={s.code} value={s.code}>{s.name}</option>
                  ))}
                </select>
              </label>
              <label className={field}>
                {t("record.precision")}
                <select className={input} {...form.register("precision")}>
                  {["EXACT", "APPROXIMATE", "GRID", "UNKNOWN"].map((p) => (
                    <option key={p} value={p}>{p}</option>
                  ))}
                </select>
              </label>
              <label className={field}>
                {t("record.confidenceField")}
                <input type="number" step="0.05" min="0" max="1" className={input} {...form.register("confidence", { valueAsNumber: true })} />
              </label>
              <label className={`${field} col-span-2`}>
                {t("record.reason")}
                <textarea rows={2} className="rounded-control border border-control-border bg-surface p-2" {...form.register("reason")} aria-invalid={!!errors.reason} />
              </label>
              <label className={`${field} col-span-2`}>
                {t("record.evidence")}
                <input className={input} {...form.register("evidence")} />
              </label>
              {Object.keys(errors).length ? <p role="alert" className="col-span-2 text-sm text-saffron-strong">{Object.values(errors).map((e) => e?.message).filter(Boolean).join(" · ")}</p> : null}
              {error ? <p role="alert" className="col-span-2 text-sm text-saffron-strong">{error}</p> : null}
              <div className="col-span-2 flex justify-end gap-2">
                <Dialog.Close type="button" className="min-h-11 rounded-control border border-control-border px-4 text-sm">{t("record.cancel")}</Dialog.Close>
                <button type="submit" disabled={submit.isPending} className="min-h-11 rounded-control bg-navy px-4 text-sm font-semibold text-on-navy">{t("record.submit")}</button>
              </div>
            </form>
          )}
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
