import { describe, expect, it } from "vitest";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { dueText } from "../components/workflow/View";
import { t } from "../lib/i18n";
import { actionDatesError, chooseTab, daysUntil, flashKey, itemHref, parseCellKeys, periodsError, problemExplanation, queueFilters, suggestedPeriods, todayIst, visibleTabs } from "../lib/workflow";
import { en } from "../messages/en";

describe("Review Queue tabs", () => {
  it("shows each role only the queues it can work", () => {
    expect(visibleTabs(["dq.manage"]).map((t) => t.key)).toEqual(["dq", "duplicates"]);
    expect(visibleTabs(["action.complete"]).map((t) => t.key)).toEqual(["actions"]);
    expect(visibleTabs(["pattern_review.decide", "action.approve", "outcome.review", "ai.insight.read"]).map((t) => t.key)).toEqual([
      "patterns",
      "actions",
      "outcomes",
      "ai",
    ]);
    expect(visibleTabs([])).toEqual([]);
  });

  it("falls back to the first enabled tab and never opens a hidden or disabled one", () => {
    expect(chooseTab("outcomes", ["action.complete"])).toBe("actions");
    expect(chooseTab("ai", ["ai.insight.read", "outcome.review"])).toBe("ai"); // Phase 7: AI insights
    expect(chooseTab("ai", ["analytics.read", "outcome.review"])).toBe("outcomes"); // no AI grant
    expect(chooseTab(undefined, ["ai.insight.read"])).toBe("ai");
    expect(chooseTab(undefined, ["analytics.read"])).toBeNull();
    expect(chooseTab("dq", [])).toBeNull();
  });

  it("links outcome items to their completed action", () => {
    expect(itemHref({ type: "PATTERN_REVIEW", id: "a/b" })).toBe("/review/patterns/a%2Fb");
    expect(itemHref({ type: "OUTCOME_DUE", id: "x" })).toBe("/review/actions/x");
  });
});

describe("Workflow dates and fields", () => {
  it("counts days to a due date, negative when overdue", () => {
    expect(daysUntil("2026-07-28", "2026-10-12")).toBe(-76);
    expect(daysUntil("2026-10-12", "2026-10-12")).toBe(0);
    expect(daysUntil("2026-10-20", "2026-10-12")).toBe(8);
  });

  it("uses the IST calendar day", () => {
    expect(todayIst(new Date("2026-10-11T19:00:00Z"))).toBe("2026-10-12"); // 00:30 IST
  });

  it("suggests a baseline of the same length just before the action", () => {
    const p = suggestedPeriods("2026-05-16", "2026-06-30");
    expect(p).toEqual({ baseline_start: "2026-03-31", baseline_end: "2026-05-15", after_start: "2026-05-16", after_end: "2026-06-30" });
    expect(periodsError(p, "2026-05-16", "2026-10-13")).toBeNull();
  });

  it("mirrors the API's outcome period rules (audit: length and action start were not checked)", () => {
    const ok = { baseline_start: "2026-04-01", baseline_end: "2026-05-15", after_start: "2026-05-16", after_end: "2026-06-30" };
    const today = "2026-10-13";
    expect(periodsError(ok, "2026-05-16", today)).toBeNull();
    expect(periodsError({ ...ok, after_end: "" }, "2026-05-16", today)).toBe("wf.period.format");
    expect(periodsError({ ...ok, baseline_end: "2026-05-16" }, "2026-05-16", today)).toBe("wf.period.order");
    expect(periodsError(ok, "2026-05-10", today)).toBe("wf.period.baseline");
    expect(periodsError({ ...ok, baseline_end: "2026-05-20", after_start: "2026-05-21" }, "2026-05-20", today)).toBe("wf.period.baseline");
    expect(periodsError({ ...ok, baseline_end: "2026-05-01", after_start: "2026-05-02" }, "2026-05-10", today)).toBe("wf.period.after");
    expect(periodsError({ ...ok, after_end: "2026-12-31" }, "2026-05-16", today)).toBe("wf.period.future");
    expect(periodsError({ ...ok, baseline_start: "2020-01-01" }, "2026-05-16", today)).toBe("wf.period.length");
  });

  it("checks action dates like the API (at most a year)", () => {
    expect(actionDatesError("2026-09-01", "2026-09-30")).toBeNull();
    expect(actionDatesError("2026-09-30", "2026-09-01")).toBe("wf.hint.dates");
    expect(actionDatesError("2026-01-01", "2027-06-01")).toBe("wf.hint.datesLength");
  });

  it("parses comparison cell keys and refuses malformed or repeated ones", () => {
    expect(parseCellKeys("PDY:500:1:-2, PDY:500:3:4\nPDY:500:5:6")).toEqual(["PDY:500:1:-2", "PDY:500:3:4", "PDY:500:5:6"]);
    expect(parseCellKeys("PDY:500:1:2 PDY:500:1:2")).toBeNull();
    expect(parseCellKeys("PDY:300:1:2")).toBeNull();
    expect(parseCellKeys("<script>")).toBeNull();
  });
});

describe("Workflow messages and errors", () => {
  it("never crashes on a key missing from the catalogue", () => {
    expect(t("wf.reviewType.SOMETHING_NEW" as never)).toBe("wf.reviewType.SOMETHING_NEW");
  });

  it("has a message for every value the API can return", () => {
    const api = JSON.parse(readFileSync(join(__dirname, "../../../packages/types/openapi.json"), "utf8")) as {
      components: { schemas: Record<string, { properties?: Record<string, { enum?: string[] }> }> };
    };
    const enumOf = (schema: string, field: string) => api.components.schemas[schema]?.properties?.[field]?.enum ?? [];
    const expected = [
      ...enumOf("QueueItem", "type").map((v) => `wf.type.${v}`),
      ...enumOf("QueueItem", "priority").map((v) => `wf.priority.${v}`),
      ...enumOf("QueueItem", "next_step").map((v) => `wf.next.${v}`),
      ...enumOf("ActionIn", "action_type").map((v) => `wf.actionType.${v}`),
      ...enumOf("OutcomeIn", "comparison_kind").map((v) => `wf.outcome.comparison.${v}`),
      // States stored by the database (CHECK constraints, migrations 0008, 0010, 0011) and DQ / ops states.
      ...["DRAFT", "SUBMITTED", "PENDING", "APPROVED", "REJECTED", "RETURNED", "REVISED", "PROPOSED", "COMPLETED", "NOT_STARTED", "DUE", "FINAL"].map((v) => `wf.status.${v}`),
      ...["OPEN", "ASSIGNED", "RESOLVED", "ACCEPTED_AS_IS", "NOT_REQUIRED"].map((v) => `wf.status.${v}`),
      ...["HOTSPOT", "EMERGING", "NEAR_REPEAT", "SERIES", "CIVIC_CONTEXT", "DATA_QUALITY"].map((v) => `wf.reviewType.${v}`),
      ...["overview", "location", "case_trends", "comparison", "data_quality"].map((v) => `wf.section.${v}`),
    ];
    expect(enumOf("QueueItem", "next_step").length).toBeGreaterThan(0);
    expect(expected.filter((k) => !(k in en))).toEqual([]);
  });

  it("shows the API's field messages for validation errors (audit: only 'Validation error' was shown)", () => {
    const problem = {
      title: "Validation error",
      errors: [
        { field: "body.description", message: "Value error, describe places, times and records only" },
        { field: "body", message: "Value error, describe places, times and records only" },
      ],
    };
    expect(problemExplanation(problem)).toBe("describe places, times and records only");
    expect(problemExplanation({ title: "x", detail: "Cannot finalise" })).toBe("Cannot finalise");
    expect(problemExplanation(null)).toBeNull();
    expect(problemExplanation({ errors: [{ message: 5 }] })).toBeNull();
  });

  it("uses plural forms for due dates", () => {
    expect(dueText("2026-10-12", "2026-10-13")).toBe("Overdue by 1 day");
    expect(dueText("2026-10-10", "2026-10-13")).toBe("Overdue by 3 days");
    expect(dueText("2026-10-14", "2026-10-13")).toBe("In 1 day");
    expect(dueText("2026-10-13", "2026-10-13")).toBe("Due today");
  });

  it("accepts only known flash keys and valid queue filters", () => {
    expect(flashKey("decided")).toBe("decided");
    expect(flashKey("<script>")).toBeNull();
    expect(queueFilters({ priority: "HIGH", region: "PDY", mine: "1", overdue: "0" }).toString()).toBe("priority=HIGH&region=PDY&mine=true");
    expect(queueFilters({ priority: "URGENT", region: "pdy;drop" }).toString()).toBe("");
  });
});
