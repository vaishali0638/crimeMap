import { describe, expect, it } from "vitest";
import { RECORD_KINDS, recordFormSchema, toApiBody } from "../lib/map/record-form";

const valid = {
  kind: "OPERATIONAL_OBSERVATION",
  title: "Street light out",
  occurredAt: "2026-09-20T19:30",
  locality: "PDY-S01 demo locality 03",
  point: null,
  region: "PDY",
  station: "PDY-S01",
  precision: "APPROXIMATE",
  confidence: 0.6,
  reason: "Seen on patrol",
  evidence: "patrol log 12",
  visibility: "RESTRICTED",
  subjectIncidentId: null,
};

describe("operational record form", () => {
  it("offers exactly the six specified kinds and no FIR", () => {
    expect(RECORD_KINDS).toEqual([
      "ANALYST_NOTE",
      "LOCATION_CORRECTION_REQUEST",
      "APPROVED_EVENT_MARKER",
      "OPERATIONAL_OBSERVATION",
      "ACTION_COMPLETION",
      "CIVIC_CONTEXT_REFERENCE",
    ]);
    expect(RECORD_KINDS.some((k) => /FIR|CASE/.test(k))).toBe(false);
    expect(recordFormSchema.safeParse({ ...valid, kind: "FIR" }).success).toBe(false);
  });

  it("needs a point or a locality, and a correction needs the record and a point", () => {
    expect(recordFormSchema.safeParse(valid).success).toBe(true);
    expect(recordFormSchema.safeParse({ ...valid, locality: "" }).success).toBe(false);
    expect(recordFormSchema.safeParse({ ...valid, kind: "LOCATION_CORRECTION_REQUEST" }).success).toBe(false);
    expect(
      recordFormSchema.safeParse({ ...valid, kind: "LOCATION_CORRECTION_REQUEST", subjectIncidentId: "6f1c4c8e-0000-4000-8000-000000000000", point: { lat: 11.9, lon: 79.8 } }).success,
    ).toBe(true);
  });

  it("requires an evidence or source reference (spec: required field)", () => {
    expect(recordFormSchema.safeParse({ ...valid, evidence: "" }).success).toBe(false);
    expect(recordFormSchema.safeParse({ ...valid, evidence: "  " }).success).toBe(false);
  });

  it("refuses the kinds the API does not accept yet", () => {
    expect(recordFormSchema.safeParse({ ...valid, kind: "ACTION_COMPLETION" }).success).toBe(false);
    expect(recordFormSchema.safeParse({ ...valid, kind: "CIVIC_CONTEXT_REFERENCE" }).success).toBe(false);
  });

  it("converts the local IST time to an offset timestamp and drops empty optional fields", () => {
    const body = toApiBody(recordFormSchema.parse(valid));
    expect(body.occurred_at).toBe("2026-09-20T19:30:00+05:30");
    expect(body.evidence_reference).toBe("patrol log 12");
    expect(body.location).toBeNull();
    expect(body.station).toBe("PDY-S01");
  });
});
