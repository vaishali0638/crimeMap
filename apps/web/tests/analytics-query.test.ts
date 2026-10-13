import { describe, expect, it } from "vitest";
import { defaultAnalytics, toParams, toQuery, validate } from "../lib/analytics/query";

describe("Analytics Workbench filters", () => {
  const f = defaultAnalytics("PDY", new Date("2026-10-12T06:00:00Z"));

  it("defaults to cases only and the previous period (no accidental mixing)", () => {
    expect(f.recordType).toBe("CASE");
    expect(toParams(f, "overview")).toMatchObject({ record_type: "CASE", comparison: "PREVIOUS", time_basis: "OCCURRENCE" });
  });

  it("sends section-specific parameters only to that section", () => {
    expect(toParams(f, "overview").unit).toBeUndefined();
    expect(toParams(f, "location")).toMatchObject({ unit: "HEX", hex_size_m: "500" });
    expect(toParams({ ...f, compare: "STATION", a: "PDY-S01", b: "PDY-S02" }, "comparison")).toMatchObject({ compare: "STATION", a: "PDY-S01", b: "PDY-S02" });
  });

  it("builds a query string with list parameters and a time range", () => {
    const q = toQuery(toParams({ ...f, station: "PDY-S01", hourFrom: "18", hourTo: "23", locality: " Lawspet " }, "case_trends"));
    expect(q).toContain("station=PDY-S01");
    expect(q).toContain("hour_from=18&hour_to=23");
    expect(q).toContain("locality=Lawspet");
  });

  it("refuses what the API would refuse, before sending", () => {
    expect(validate(f, "overview")).toEqual([]);
    expect(validate({ ...f, periodFrom: "2026-10-01", periodTo: "2026-09-01" }, "overview")).toContain("The start date is after the end date.");
    expect(validate({ ...f, periodFrom: "2023-01-01" }, "overview")).toContain("The period may be at most two years.");
    expect(validate({ ...f, hourFrom: "18" }, "overview")).toContain("Choose both ends of the time range, or neither.");
    expect(validate({ ...f, compare: "REGION" }, "comparison")).toContain("Choose A and B to compare.");
  });
});

describe("analysis URL", () => {
  it("round-trips section and filters, and ignores unknown or malformed values", async () => {
    const { analysisUrl, defaultAnalytics, fromQuery } = await import("../lib/analytics/query");
    const f = { ...defaultAnalytics("KKL", new Date("2026-10-12T06:00:00Z")), station: "KKL-S01", recordType: "ERSS_CALL" as const, hourFrom: "22", hourTo: "3", grain: "week" as const };
    const url = analysisUrl("case_trends", f);
    const back = fromQuery(url.split("?")[1] ?? "", "PDY", ["PDY", "KKL"]);
    expect(back.section).toBe("case_trends");
    expect(back.filters).toMatchObject({ region: "KKL", station: "KKL-S01", recordType: "ERSS_CALL", hourFrom: "22", hourTo: "3", grain: "week" });
    const bad = fromQuery("section=evil&region=XXX&station=%3Cscript%3E&record_type=ALL&period_from=yesterday", "PDY", ["PDY"]);
    expect(bad.section).toBeNull();
    expect(bad.filters.region).toBe("PDY");
    expect(bad.filters.station).toBe("");
    expect(bad.filters.recordType).toBe("CASE");
    expect(bad.filters.periodFrom).toMatch(/^\d{4}-\d{2}-\d{2}$/);
  });
});
