import { describe, expect, it } from "vitest";
import { tn } from "../lib/i18n";

describe("tn", () => {
  it("chooses the plural form by count", () => {
    expect(tn("review.openCount", 1)).toBe("1 open issue in your scope");
    expect(tn("review.openCount", 2)).toBe("2 open issues in your scope");
    expect(tn("review.openCount", 0)).toBe("0 open issues in your scope");
  });
});
