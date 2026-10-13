import { z } from "zod";

/** "+ Add Operational Record": the six specified kinds. There is deliberately no FIR option. */
export const RECORD_KINDS = [
  "ANALYST_NOTE",
  "LOCATION_CORRECTION_REQUEST",
  "APPROVED_EVENT_MARKER",
  "OPERATIONAL_OBSERVATION",
  "ACTION_COMPLETION",
  "CIVIC_CONTEXT_REFERENCE",
] as const;

export const REVIEWED_KINDS = new Set(["LOCATION_CORRECTION_REQUEST", "APPROVED_EVENT_MARKER", "CIVIC_CONTEXT_REFERENCE"]);

/**
 * Kinds listed (the spec names all six) but not yet accepted by the API: action completion needs an
 * approved action (Phase 6); civic-context references are behind a feature flag that is off.
 */
export const UNAVAILABLE_KINDS = new Set(["ACTION_COMPLETION", "CIVIC_CONTEXT_REFERENCE"]);

export const recordFormSchema = z
  .object({
    kind: z.enum(RECORD_KINDS).refine((k) => !UNAVAILABLE_KINDS.has(k), { message: "This type is not available yet" }),
    title: z.string().trim().min(3).max(200),
    occurredAt: z.string().regex(/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}$/),
    locality: z.string().trim().max(200),
    point: z.object({ lat: z.number().min(-90).max(90), lon: z.number().min(-180).max(180) }).nullable(),
    region: z.string().regex(/^[A-Z]{2,5}$/),
    station: z.string(),
    precision: z.enum(["EXACT", "APPROXIMATE", "GRID", "UNKNOWN"]),
    confidence: z.number().min(0).max(1),
    reason: z.string().trim().min(3).max(2000),
    // Required by the spec ("evidence/source reference"): e.g. a patrol log or message reference.
    evidence: z.string().trim().min(3, { message: "Give an evidence or source reference" }).max(500),
    visibility: z.enum(["INTERNAL", "RESTRICTED"]),
    subjectIncidentId: z.string().uuid().nullable(),
  })
  .refine((v) => v.point !== null || v.locality.length > 0, { path: ["locality"], message: "Give a map point or a locality" })
  .refine((v) => v.kind !== "LOCATION_CORRECTION_REQUEST" || (v.subjectIncidentId !== null && v.point !== null), {
    path: ["point"],
    message: "A location correction needs the record and the corrected map point",
  });

export type RecordForm = z.infer<typeof recordFormSchema>;

export function toApiBody(v: RecordForm) {
  return {
    kind: v.kind,
    title: v.title,
    occurred_at: `${v.occurredAt}:00+05:30`,
    location: v.point,
    locality: v.locality || null,
    region: v.region,
    station: v.station || null,
    precision: v.precision,
    confidence: v.confidence,
    reason: v.reason,
    evidence_reference: v.evidence,
    visibility: v.visibility,
    subject_incident_id: v.subjectIncidentId,
  };
}
