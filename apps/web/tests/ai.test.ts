import { describe, expect, it } from "vitest";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { AI_LABEL, analyticsHref, entries, humanKey, placeTimeAi, plainValue } from "../lib/ai";
import { fromQuery } from "../lib/analytics/query";
import { FLASH_KEYS, flashNonce } from "../lib/workflow";
import { en } from "../messages/en";

describe("AI insight helpers", () => {
  it("shows evidence values as plain text (never HTML)", () => {
    expect(plainValue(true)).toBe("yes");
    expect(plainValue(12345)).toBe("12,345");
    expect(plainValue(0.125)).toBe("0.125");
    expect(plainValue([1, 2])).toBe("1, 2");
    expect(plainValue({ a_b: 1 })).toBe("A b: 1");
    expect(plainValue(null)).toBe("—");
    expect(plainValue("<b>x</b>")).toBe("<b>x</b>"); // React escapes it; no HTML is built here
    expect(humanKey("without_usable_location")).toBe("Without usable location");
    expect(entries({ count: 23, p_value: 1e-9 })).toEqual([["Count", "23"], ["P value", "1e-9"]]);
  });

  it("links only place-time insights to the Analytics Workbench, and the link restores the view", () => {
    const base = { region: "PDY", station: null, window_start: "2026-09-15", window_end: "2026-10-12" };
    const href = analyticsHref({ ...base, insight_type: "EMERGING_PATTERN" });
    expect(href).toBe("/analytics?section=location&region=PDY&period_from=2026-09-15&period_to=2026-10-12&unit=HEX&hex_size_m=1000");
    const restored = fromQuery((href ?? "").split("?")[1] ?? "", "KKL", ["PDY", "KKL"]);
    expect(restored.section).toBe("location");
    expect(restored.filters.region).toBe("PDY");
    expect([restored.filters.periodFrom, restored.filters.periodTo]).toEqual(["2026-09-15", "2026-10-12"]);
    expect(analyticsHref({ ...base, station: "PDY-S03", insight_type: "CLUSTER" })).toContain("station=PDY-S03");
    expect(analyticsHref({ ...base, insight_type: "GEOCODE" })).toBeNull();
  });

  it("shows place-time AI only to roles beyond data steward, and has every flash message", () => {
    expect(placeTimeAi({ permissions: ["ai.insight.read"], roles: [{ role: "DATA_STEWARD" }] })).toBe(false);
    expect(placeTimeAi({ permissions: ["ai.insight.read"], roles: [{ role: "CRIME_ANALYST" }] })).toBe(true);
    expect(placeTimeAi({ permissions: [], roles: [{ role: "CRIME_ANALYST" }] })).toBe(false);
    expect(FLASH_KEYS.filter((k) => !(`wf.flash.${k}` in en))).toEqual([]);
    expect(flashNonce("k3x9a")).toBe("k3x9a");
    expect(flashNonce("<script>")).toBe("0");
    for (const label of ["LOW", "MEDIUM", "HIGH"]) expect(`wf.priority.${label}` in en).toBe(true);
    for (const status of ["RUNNING", "COMPLETED", "FAILED", "SKIPPED"]) expect(`ai.runStatus.${status}` in en).toBe(true);
    for (const kind of ["RULE", "STATISTICAL", "TEMPLATE", "ML", "LLM"]) expect(`ai.kind.${kind}` in en).toBe(true);
  });

  it("uses the spec's label and has a message for every insight type, state and approval status", () => {
    expect(AI_LABEL).toBe("AI-assisted; analyst review required.");
    const api = JSON.parse(readFileSync(join(__dirname, "../../../packages/types/openapi.json"), "utf8")) as {
      paths: Record<string, { get?: { parameters?: { name: string; schema?: { anyOf?: { enum?: string[] }[]; enum?: string[] } }[] } }>;
    };
    const params = api.paths["/api/v1/ai/insights"]?.get?.parameters ?? [];
    const values = (name: string) => params.filter((p) => p.name === name).flatMap((p) => [...(p.schema?.enum ?? []), ...(p.schema?.anyOf ?? []).flatMap((a) => a.enum ?? [])]);
    const types = values("type");
    const states = values("state");
    expect(types.length).toBe(5);
    expect(states.length).toBe(3);
    const keys = [...types.map((v) => `ai.type.${v}`), ...states.map((v) => `ai.state.${v}`), ...["DRAFT", "APPROVED", "RETIRED"].map((v) => `ai.approval.${v}`)];
    expect(keys.filter((k) => !(k in en))).toEqual([]);
  });
});
