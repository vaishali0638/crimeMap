import { describe, expect, it } from "vitest";
import { mapConfig } from "@/lib/map-config";

describe("mapConfig (ADR-033)", () => {
  it("has no basemap by default", () => {
    expect(mapConfig({})).toEqual({ styleUrl: null, origins: [], problem: null });
  });

  it("accepts an https style and its asset origins", () => {
    const c = mapConfig({
      CRIMEMAP_MAP_STYLE_URL: "https://maps.example.gov.in/style.json",
      CRIMEMAP_MAP_ASSET_ORIGINS: "https://tiles.example.gov.in, https://maps.example.gov.in",
    });
    expect(c.styleUrl).toBe("https://maps.example.gov.in/style.json");
    expect(c.origins).toEqual(["https://maps.example.gov.in", "https://tiles.example.gov.in"]);
  });

  it.each(["javascript:alert(1)", "data:application/json,{}", "not a url", "http://maps.example.com/s.json", "https://u:p@maps.example.com/s.json"])(
    "turns the basemap off for %s instead of failing",
    (value) => {
      const c = mapConfig({ CRIMEMAP_MAP_STYLE_URL: value });
      expect(c.styleUrl).toBeNull();
      expect(c.origins).toEqual([]);
      expect(c.problem).not.toBeNull();
    },
  );

  it("allows a localhost http style outside PRODUCTION only", () => {
    expect(mapConfig({ CRIMEMAP_MAP_STYLE_URL: "http://localhost:8081/style.json" }).styleUrl).not.toBeNull();
    expect(mapConfig({ CRIMEMAP_ENVIRONMENT: "PRODUCTION", CRIMEMAP_MAP_STYLE_URL: "http://localhost:8081/style.json" }).styleUrl).toBeNull();
  });

  it("rejects asset origins with a path", () => {
    const c = mapConfig({ CRIMEMAP_MAP_STYLE_URL: "https://a.example/s.json", CRIMEMAP_MAP_ASSET_ORIGINS: "https://b.example/tiles" });
    expect(c.styleUrl).toBeNull();
  });
});
