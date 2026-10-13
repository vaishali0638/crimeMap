import { HomeMap } from "@/components/map/HomeMap";
import { requireUser } from "@/lib/current-user";
import { mapConfig } from "@/lib/map-config";
import { securityEvent } from "@/lib/security-log";

export const dynamic = "force-dynamic";

type SearchParams = Promise<Record<string, string | string[] | undefined>>;

/** Home Map (Phase 3). Data is fetched client-side through the BFF; the API enforces scope. */
export default async function HomePage({ searchParams }: { searchParams: SearchParams }) {
  const { me, session } = await requireUser();
  const q = (await searchParams).q;
  const map = mapConfig(process.env);
  if (map.problem) securityEvent("map_style_rejected", { problem: map.problem });
  return (
    <HomeMap
      permissions={me.permissions}
      csrfToken={session.csrfToken}
      styleUrl={map.styleUrl}
      initialQuery={(Array.isArray(q) ? q[0] : q)?.slice(0, 100) ?? ""}
    />
  );
}
