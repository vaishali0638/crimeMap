import { describe, expect, it } from "vitest";
import { actionsFor, formatIst, queueQuery } from "../lib/dq";

describe("queueQuery", () => {
  it("keeps only known filters with allowed values", () => {
    const q = queueQuery({ state: "OPEN", severity: "HIGH", region: "PDY", evil: "x", issue_type: "DROP TABLE" });
    expect(q.toString()).toBe("state=OPEN&severity=HIGH&region=PDY&limit=25");
  });

  it("defaults to the open queue and passes a well-formed cursor through", () => {
    expect(queueQuery({}).get("state")).toBe("OPEN");
    expect(queueQuery({ state: "ALL" }).has("state")).toBe(false);
    expect(queueQuery({ cursor: "abc_-123" }).get("cursor")).toBe("abc_-123");
    expect(queueQuery({ cursor: "a b/c" }).has("cursor")).toBe(false);
  });

  it("uses the first value when a parameter is repeated", () => {
    expect(queueQuery({ severity: ["LOW", "HIGH"] }).get("severity")).toBe("LOW");
  });
});

describe("actionsFor", () => {
  it("mirrors the API's transitions", () => {
    expect(actionsFor("OPEN")).toEqual(["ASSIGNED", "RESOLVED", "ACCEPTED_AS_IS"]);
    expect(actionsFor("ASSIGNED")).toEqual(["OPEN", "RESOLVED", "ACCEPTED_AS_IS"]);
    expect(actionsFor("RESOLVED")).toEqual(["OPEN"]);
    expect(actionsFor("UNKNOWN")).toEqual([]);
  });
});

describe("formatIst", () => {
  it("shows times in India Standard Time and handles missing values", () => {
    expect(formatIst("2026-10-11T20:30:00Z")).toBe("12 Oct 2026, 02:00 IST");
    expect(formatIst(null)).toBe("—");
  });
});

describe("filterParams", () => {
  it("keeps only whitelisted page filters for links", async () => {
    const { filterParams } = await import("../lib/dq");
    expect(filterParams({ state: "ALL", severity: "HIGH", evil: "<x>", cursor: "abc", mine: "1" }).toString()).toBe(
      "severity=HIGH&mine=1&state=ALL",
    );
  });
});

describe("labels", () => {
  it("come from the message catalogue and pass unknown values through", async () => {
    const { issueLabel, stateLabel } = await import("../lib/dq");
    expect(issueLabel("POSSIBLE_DUPLICATE")).toBe("Possible duplicate");
    expect(stateLabel("ACCEPTED_AS_IS")).toBe("Accepted as is");
    expect(issueLabel("NEW_RULE")).toBe("NEW_RULE");
  });
});
