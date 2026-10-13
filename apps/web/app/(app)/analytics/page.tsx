import { AnalyticsWorkbench } from "@/components/analytics/Workbench";
import { requireUser } from "@/lib/current-user";
import type { SectionId } from "@/lib/analytics/query";
import { t } from "@/lib/i18n";

export const dynamic = "force-dynamic";

type SearchParams = Promise<Record<string, string | string[] | undefined>>;
const SECTION_IDS = new Set<SectionId>(["overview", "location", "case_trends", "civic", "ai", "comparison", "data_quality"]);
// Roles whose analytics grant covers more than the Data Quality section (role matrix).
const FULL_ANALYTICS = new Set(["CRIME_ANALYST", "SHO", "DISTRICT_OR_REGION_SUPERVISOR", "SENIOR_LEADERSHIP", "DEMO_USER"]);

/** Analytics Workbench (Phase 5). Data is fetched client-side through the BFF; the API enforces scope. */
export default async function AnalyticsPage({ searchParams }: { searchParams: SearchParams }) {
  const { me, session } = await requireUser();
  if (!me.permissions.includes("analytics.read")) {
    return (
      <section aria-labelledby="an-title" className="max-w-3xl">
        <h1 id="an-title" className="text-2xl font-semibold text-navy">{t("an.title")}</h1>
        <p className="mt-2">{t("an.noAccess")}</p>
      </section>
    );
  }
  const params = await searchParams;
  const raw = params.section;
  const section = (Array.isArray(raw) ? raw[0] : raw) as SectionId | undefined;
  // The full query is re-validated value by value on the client (lib/analytics/query.ts fromQuery).
  const initialQuery = new URLSearchParams(
    Object.entries(params).flatMap(([k, v]) => (Array.isArray(v) ? v.map((x) => [k, x]) : v === undefined ? [] : [[k, v]])),
  ).toString().slice(0, 2000);
  const dqOnly = !me.roles.some((r) => FULL_ANALYTICS.has(r.role));
  return (
    <AnalyticsWorkbench
      permissions={me.permissions}
      csrfToken={session.csrfToken}
      dqOnly={dqOnly}
      initialSection={section && SECTION_IDS.has(section) && !(section === "comparison" && !me.permissions.includes("analytics.compare")) ? section : "overview"}
      initialQuery={initialQuery}
    />
  );
}
