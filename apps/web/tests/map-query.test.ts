import { describe, expect, it } from "vitest";
import { defaultFilters, featuresQuery, istDate, regionFromCellKey } from "../lib/map/query";

describe("featuresQuery", () => {
  it("encodes only set filters, repeating list parameters", () => {
    const filters = {
      ...defaultFilters("PDY", new Date("2026-10-12T10:00:00+05:30")),
      stations: ["PDY-S01"],
      categories: ["PROPERTY_THEFT", "VEHICLE_THEFT"],
      minConfidence: "0.4" as const,
    };
    const q = featuresQuery(filters, { aggregation: "HEX", hexSizeM: 500 });
    expect(q.getAll("category")).toEqual(["PROPERTY_THEFT", "VEHICLE_THEFT"]);
    expect(q.get("station")).toBe("PDY-S01");
    expect(q.get("region")).toBe("PDY");
    expect(q.get("min_confidence")).toBe("0.4");
    expect(q.get("hex_size_m")).toBe("500");
    expect(q.has("beat")).toBe(false);
    expect(q.has("status")).toBe(false);
  });

  it("omits the hexagon size outside hexagon mode and adds a bounding box for points", () => {
    const q = featuresQuery(defaultFilters("KKL"), { aggregation: "POINTS", hexSizeM: 500, bbox: [79.8, 10.9, 79.9, 11.0] });
    expect(q.has("hex_size_m")).toBe(false);
    expect(q.get("bbox")).toBe("79.8,10.9,79.9,11");
  });
});

describe("defaultFilters", () => {
  it("covers the last 90 days in India Standard Time", () => {
    const f = defaultFilters("PDY", new Date("2026-10-12T20:00:00Z")); // 13 Oct 01:30 IST
    expect(f.periodTo).toBe("2026-10-13");
    expect(f.periodFrom).toBe("2026-07-16");
    expect(f.timeBasis).toBe("OCCURRENCE");
  });
});

describe("helpers", () => {
  it("formats IST dates and reads the region from a cell key", () => {
    expect(istDate(new Date("2026-01-01T19:00:00Z"))).toBe("2026-01-02");
    expect(regionFromCellKey("MAH:500:-12:40")).toBe("MAH");
    expect(regionFromCellKey("bad")).toBeNull();
  });
});

describe("clampView", () => {
  it("keeps a normal view and clamps a wrapped or zoomed-out one into the API's bbox range", async () => {
    const { clampView } = await import("../components/map/MapCanvas");
    expect(clampView(79.7, 11.8, 79.9, 12.0)).toEqual([79.7, 11.8, 79.9, 12.0]);
    expect(clampView(-200, -95, 250, 95)).toEqual([-180, -90, 180, 90]);
    expect(clampView(170, 10, 190, 20)).toEqual([170, 10, 180, 20]);
  });
});
