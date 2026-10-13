"use client";

import type { Aggregation } from "@/lib/map/query";
import { type Layers, useMapStore } from "@/lib/map/store";
import { t } from "@/lib/i18n";
import type { MessageKey } from "@/messages/en";

const LAYERS: (keyof Layers)[] = ["cases", "regions", "stations", "beats", "records"];

export function LayerPanel({ aggregations, hexSizes, canPoints }: { aggregations: string[]; hexSizes: number[]; canPoints: boolean }) {
  const { layers, toggleLayer, aggregation, setAggregation, hexSizeM, setHexSize } = useMapStore();
  return (
    <section aria-labelledby="layers-title" className="flex flex-col gap-4 p-4 text-sm">
      <h2 id="layers-title" className="font-semibold text-navy">{t("map.layers")}</h2>
      <ul className="flex flex-col gap-1">
        {LAYERS.map((layer) => {
          // Records are points: without point access the layer is shown, explained and disabled.
          const unavailable = layer === "records" && !canPoints;
          return (
            <li key={layer}>
              <label className={`flex min-h-11 items-center gap-2 ${unavailable ? "text-ink/60" : ""}`}>
                <input type="checkbox" className="size-5" disabled={unavailable} checked={!unavailable && layers[layer]} onChange={() => toggleLayer(layer)} />
                {t(`map.layer.${layer}` as MessageKey)}
              </label>
              {unavailable ? <p className="pl-7 text-xs">{t("map.layer.recordsNeedPoints")}</p> : null}
            </li>
          );
        })}
        <li>
          <label className="flex min-h-11 items-center gap-2 text-ink/60">
            <input type="checkbox" className="size-5" disabled checked={false} readOnly />
            {t("map.layer.civic")}
          </label>
        </li>
      </ul>
      <fieldset className="flex flex-col gap-1">
        <legend className="font-semibold text-navy">{t("map.aggregation")}</legend>
        {(["POINTS", "HEX", "BEAT"] as Aggregation[])
          .filter((a) => aggregations.includes(a))
          .map((a) => (
            <label key={a} className="flex min-h-11 items-center gap-2">
              <input type="radio" name="aggregation" className="size-5" checked={aggregation === a} onChange={() => setAggregation(a)} />
              {t(`map.agg.${a}` as MessageKey)}
            </label>
          ))}
      </fieldset>
      {aggregation === "HEX" ? (
        <label className="flex flex-col gap-1 font-medium">
          {t("map.hexSize")}
          <select className="min-h-11 rounded-control border border-control-border bg-surface px-2" value={hexSizeM} onChange={(e) => setHexSize(Number(e.target.value))}>
            {hexSizes.map((s) => (
              <option key={s} value={s}>{s} m</option>
            ))}
          </select>
        </label>
      ) : null}
    </section>
  );
}
