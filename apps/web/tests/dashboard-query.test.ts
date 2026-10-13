import { describe, expect, it } from "vitest";
import { type DashboardFilters, defaultDashboard, mapQuery, mapRegion, movement, trendsQuery, visibleStations } from "../lib/dashboard/query";
import { en } from "../messages/en";

const list = {
  data_label: "DEMO / SYNTHETIC DATA",
  regions: [{ code: "KKL", name: "Karaikal" }, { code: "PDY", name: "Puducherry" }],
  stations: [
    { code: "KKL-S01", name: "Karaikal Demo Station 01 (Demo)", region_code: "KKL" },
    { code: "PDY-S01", name: "Puducherry Demo Station 01 (Demo)", region_code: "PDY" },
    { code: "PDY-S02", name: "Puducherry Demo Station 02 (Demo)", region_code: "PDY" },
  ],
  categories: [],
};
const base: DashboardFilters = { region: "ALL", station: null, category: "", periodFrom: "2026-07-01", periodTo: "2026-09-30", timeBasis: "OCCURRENCE" };

describe("PY Dashboard queries", () => {
  it("defaults to all regions and the last 90 days", () => {
    const d = defaultDashboard(new Date("2026-10-12T06:00:00Z"));
    expect(d.region).toBe("ALL");
    expect(d.periodTo).toBe("2026-10-12");
  });

  it("sends the same region and station to the trends as the list and map use (PD-1)", () => {
    expect(trendsQuery({ ...base, region: "PDY", station: "PDY-S02", category: "PROPERTY_THEFT" })).toBe(
      "category=PROPERTY_THEFT&period_from=2026-07-01&period_to=2026-09-30&time_basis=OCCURRENCE&region=PDY&station=PDY-S02",
    );
    expect(trendsQuery(base)).toContain("region=ALL");
  });

  it("filters the station list by region and search text", () => {
    expect(visibleStations(list, "PDY", "").map((s) => s.code)).toEqual(["PDY-S01", "PDY-S02"]);
    expect(visibleStations(list, "ALL", "karaikal").map((s) => s.code)).toEqual(["KKL-S01"]);
    expect(visibleStations(list, "ALL", "s02").map((s) => s.code)).toEqual(["PDY-S02"]);
  });

  it("draws the selected station's region, else the chosen or first region", () => {
    expect(mapRegion({ ...base, station: "PDY-S02" }, list)).toBe("PDY");
    expect(mapRegion({ ...base, region: "PDY" }, list)).toBe("PDY");
    expect(mapRegion(base, list)).toBe("KKL");
    expect(mapQuery(base, "PDY", "PDY-S01")).toContain("station=PDY-S01");
  });

  it("describes movement with a symbol and words, not colour", () => {
    expect(movement({ movement: "UP", change_pct: 12.5, previous: 8 })).toEqual({ symbol: "▲ 12.5%", text: "up 12.5% from 8" });
    expect(movement({ movement: "DOWN", change_pct: -50, previous: 4 }).symbol).toBe("▼ 50%");
    expect(movement({ movement: "NEW", change_pct: null, previous: 0 }).text).toBe("none in the comparison period");
  });

  it("uses neutral wording everywhere in the UI text (PD-6)", () => {
    const prohibited = /\b(dangerous|worst|criminal|high-crime)\b/i;
    expect(Object.values(en).filter((v) => prohibited.test(v))).toEqual([]);
  });
});
