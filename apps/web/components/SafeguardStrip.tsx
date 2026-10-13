import type { Freshness } from "@/lib/api";
import { formatIst } from "@/lib/dq";
import { t, tn } from "@/lib/i18n";

export const ENV_LABEL = { DEMO: "safeguard.demo", SANDBOX: "safeguard.sandbox", PRODUCTION: "safeguard.production" } as const;

/**
 * Always-visible environment, classification and data-refresh banner (text + colour, never colour
 * alone). "Unavailable" (the freshness lookup failed) is distinguished from "no data loaded".
 */
export function SafeguardStrip({
  environment,
  freshness,
}: {
  environment: keyof typeof ENV_LABEL;
  freshness: Freshness | "unavailable" | null;
}) {
  const known = freshness !== null && freshness !== "unavailable" ? freshness : null;
  const stale = known?.sources.filter((s) => s.status === "STALE").length ?? 0;
  return (
    <div role="region" aria-label={t("safeguard.region")} className="flex flex-wrap items-center gap-x-4 gap-y-1 bg-navy px-6 py-1 text-sm text-on-navy">
      <span className="rounded-control bg-saffron px-2 font-semibold text-ink">{t(ENV_LABEL[environment])}</span>
      {known?.data_label && environment !== "DEMO" ? (
        <span className="rounded-control bg-saffron px-2 font-semibold text-ink">{known.data_label}</span>
      ) : null}
      <span className="font-semibold">{t("safeguard.classification")}</span>
      <span className="ml-auto">
        {freshness === "unavailable"
          ? t("safeguard.unavailable")
          : known?.last_refreshed_at
            ? t("safeguard.refreshedAt", { time: formatIst(known.last_refreshed_at) })
            : t("safeguard.refresh")}
        {stale ? ` — ${tn("safeguard.stale", stale)}` : ""}
      </span>
    </div>
  );
}
