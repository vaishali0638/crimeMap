import { PyDashboard } from "@/components/dashboard/PyDashboard";
import { requireUser } from "@/lib/current-user";
import { mapConfig } from "@/lib/map-config";
import { t } from "@/lib/i18n";

export const dynamic = "force-dynamic";

type SearchParams = Promise<Record<string, string | string[] | undefined>>;

const first = (v: string | string[] | undefined) => (Array.isArray(v) ? v[0] : v);

/** PY Dashboard (Phase 4). Data is fetched client-side through the BFF; the API enforces scope. */
export default async function DashboardPage({ searchParams }: { searchParams: SearchParams }) {
  const { me } = await requireUser();
  if (!me.permissions.includes("map.aggregate.read")) {
    return (
      <section aria-labelledby="dash-title" className="max-w-3xl">
        <h1 id="dash-title" className="text-2xl font-semibold text-navy">{t("dash.title")}</h1>
        <p className="mt-2">{t("dash.noAccess")}</p>
      </section>
    );
  }
  const params = await searchParams;
  const region = first(params.region);
  const station = first(params.station);
  return (
    <PyDashboard
      styleUrl={mapConfig(process.env).styleUrl}
      initialRegion={region && /^[A-Z]{2,5}$/.test(region) ? region : null}
      initialStation={station && /^[A-Z]{2,5}-S\d{2}$/.test(station) ? station : null}
    />
  );
}
