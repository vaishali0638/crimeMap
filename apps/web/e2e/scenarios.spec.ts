import { expect, test, type Browser, type Page } from "@playwright/test";
import { existsSync } from "node:fs";
import { statePath, type DemoUser } from "./global-setup";

/**
 * Spec "Required end-to-end scenarios" 1–10 against the live DEMO stack. Each scenario signs in as
 * the demo user it names. A user who could not sign in makes their scenarios skip with the reason
 * (never pass silently).
 *
 * These tests write to the demo database (notes, reviews), as a user would. DEMO / SYNTHETIC DATA.
 */

const PERIOD = { from: "2026-09-20", to: "2026-09-30" }; // the synthetic data ends 2026-09-30

async function as(browser: Browser, user: DemoUser): Promise<Page> {
  test.skip(!existsSync(statePath(user)), `blocked: no signed-in session for ${user} (TOTP key unavailable)`);
  const context = await browser.newContext({ storageState: statePath(user) });
  return context.newPage();
}

async function filterHomeMap(page: Page, station: string): Promise<void> {
  await page.goto("/");
  await page.getByRole("combobox", { name: "Station", exact: true }).selectOption({ label: station });
  await page.getByRole("textbox", { name: "From" }).fill(PERIOD.from);
  await page.getByRole("textbox", { name: "To" }).fill(PERIOD.to);
  await page.getByRole("radio", { name: "Points" }).check();
  await page.getByRole("button", { name: "Apply" }).click();
}

test("1. analyst filters the Home Map, opens a tag and its permitted details, and adds a note", async ({ browser }) => {
  const page = await as(browser, "demo.analyst");
  await filterHomeMap(page, "Puducherry Demo Station 01 (Demo)");
  await expect(page.getByText(/selected/).first()).toBeVisible();
  await page.getByText("Features in view (keyboard list)").click();
  const features = page.getByRole("group").filter({ hasText: "Features in view" }).getByRole("button");
  await features.first().click();
  const summary = page.getByRole("complementary", { name: "Record summary" });
  await expect(summary).toBeVisible();
  await expect(summary.getByText("Puducherry Demo Station 01 (Demo)")).toBeVisible();
  await summary.getByRole("button", { name: "Open permitted details" }).click();
  await expect(summary.getByRole("button", { name: "Open permitted details" })).toHaveAttribute("aria-expanded", "true");
  await expect(summary.getByText("Record type")).toBeVisible();
  const note = `E2E scenario 1 note ${Date.now()}`;
  await summary.getByRole("textbox", { name: "Note (stored as plain text)" }).fill(note);
  await summary.getByRole("button", { name: "Add analyst note" }).click();
  await expect(summary.getByText(note)).toBeVisible();
});

test("3. supervisor selects a region and station, opens View Details and the Trends board", async ({ browser }) => {
  const page = await as(browser, "demo.supervisor");
  await page.goto("/dashboard");
  await page.getByRole("complementary", { name: "Stations" }).getByRole("combobox", { name: "Region" }).selectOption({ label: "Karaikal" }); // the demo supervisor covers Karaikal
  const stations = page.getByRole("list", { name: "Stations" });
  await stations.getByRole("button", { name: "Karaikal Demo Station 02 (Demo)", exact: true }).click();
  await stations.getByRole("button", { name: "View details for Karaikal Demo Station 02 (Demo)" }).click();
  const details = page.getByRole("dialog", { name: "Karaikal Demo Station 02 (Demo)" });
  await expect(details.getByRole("region", { name: "Case records by status" })).toBeVisible();
  await expect(details.getByText("DEMO / SYNTHETIC DATA")).toBeVisible();
  await details.getByRole("button", { name: "Back" }).click();
  await expect(details).toBeHidden();
  await page.getByRole("button", { name: "TRENDS" }).click();
  await expect(page.getByRole("heading", { name: /trends/i }).first()).toBeVisible();
});

async function bff(page: Page, method: string, path: string, body?: unknown, csrf = true) {
  return page.evaluate(
    async ({ method, path, body, csrf }) => {
      const token = document.cookie.split("; ").find((c) => c.startsWith("crimemap_csrf="))?.split("=")[1];
      const headers: Record<string, string> = { "content-type": "application/json" };
      if (csrf && token) headers["x-csrf-token"] = decodeURIComponent(token);
      const response = await fetch(`/bff/api/${path}`, {
        method,
        headers,
        body: body === undefined ? undefined : JSON.stringify(body),
      });
      return { status: response.status, requestId: response.headers.get("x-request-id"), body: await response.json().catch(() => null) };
    },
    { method, path, body, csrf },
  );
}

test("9. an unauthorised user's export attempt is denied and audited", async ({ browser }) => {
  const page = await as(browser, "demo.auditor");
  await page.goto("/");
  const attempt = await bff(page, "POST", "reports", {
    format: "CSV",
    section: "overview",
    params: { region: "PDY", period_from: PERIOD.from, period_to: PERIOD.to },
    purpose: "BRIEFING",
  });
  // Refused: 403, or 404 where the caller has no access to the region at all (not revealed).
  expect([403, 404]).toContain(attempt.status);
  // The auditor may read the audit trail: the refused attempt is there.
  const events = await bff(page, "GET", "audit/events?action=AUTHZ_DENIED&limit=20");
  expect(events.status, JSON.stringify(events.body)).toBe(200);
  const mine = (events.body.data as { request_id: string; result: string }[]).filter((e) => e.request_id === attempt.requestId);
  expect(attempt.requestId).toBeTruthy();
  expect(mine.map((e) => e.result)).toEqual(["DENY"]);
});

test("BFF refuses a state-changing request without the CSRF token", async ({ browser }) => {
  const page = await as(browser, "demo.analyst");
  await page.goto("/");
  const response = await bff(page, "POST", "notes", { subject_type: "INCIDENT", note: "x", visibility: "INTERNAL" }, false);
  expect(response.status).toBe(403);
});

const isoDate = (offsetDays: number) => new Date(Date.now() + offsetDays * 86_400_000 + 5.5 * 3_600_000).toISOString().slice(0, 10);

test("4–5. analyst creates a pattern review from evidence; supervisor decides it and approves the proposed action", async ({ browser }) => {
  test.setTimeout(180_000);
  const analyst = await as(browser, "demo.analyst");
  const supervisor = await as(browser, "demo.supervisor");
  const stamp = Date.now();

  // 4. From Location Trends evidence (Karaikal, the supervisor's region).
  await analyst.goto("/analytics?section=location&region=KKL&period_from=2026-07-01&period_to=2026-09-30&time_basis=OCCURRENCE&record_type=CASE&comparison=PREVIOUS");
  await analyst.getByRole("button", { name: "Create Pattern Review" }).click();
  const create = analyst.getByRole("dialog", { name: "Create pattern review" });
  await create.getByRole("group", { name: "Cells (higher recorded counts first)" }).getByRole("checkbox").first().check();
  await create.getByLabel("Analyst narrative (at least 20 characters)").fill(`E2E ${stamp}: recorded property thefts concentrate in this cell over the period.`);
  await create.getByLabel("Recommended next step").fill("Review patrol timing with the station.");
  const created = analyst.waitForResponse((r) => r.url().endsWith("/bff/api/pattern-reviews") && r.request().method() === "POST");
  await create.getByRole("button", { name: "Create draft" }).click();
  const review = (await (await created).json()) as { id: string };
  await expect(create.getByRole("status")).toContainText("Draft saved");

  await analyst.goto(`/review/patterns/${review.id}`);
  // Accessibility: the disabled button says why (hint linked with aria-describedby).
  const submit = analyst.getByRole("button", { name: "Submit for decision" });
  await expect(submit).toBeDisabled();
  await expect(submit).toHaveAccessibleDescription(/assessment/i);
  await analyst.getByLabel("Analyst assessment").fill("The increase is consistent across both comparison months.");
  await expect(submit).toHaveAccessibleDescription("");
  await analyst.getByRole("button", { name: "Submit for decision" }).click();
  await expect(analyst.getByRole("status").filter({ hasText: "Submitted" }).first()).toBeVisible();

  // 5. The supervisor (not the author) approves it, with a rationale and a confirmation step.
  await supervisor.goto(`/review/patterns/${review.id}`);
  await supervisor.getByLabel("Rationale").fill("Evidence and baseline support a review of patrol timing.");
  await supervisor.getByRole("button", { name: "Approve", exact: true }).click();
  await supervisor.getByRole("button", { name: "Yes, Approve" }).click();
  await expect(supervisor.getByRole("status").filter({ hasText: "Decision recorded" }).first()).toBeVisible();

  // The analyst proposes an action from the approved review; the supervisor approves it.
  await analyst.goto(`/review/patterns/${review.id}`);
  await analyst.getByLabel("Description").fill(`E2E ${stamp}: increase visible presence in the selected cell during evening hours.`);
  await expect(analyst.getByRole("group", { name: "Cells (from the pattern review)" }).getByRole("checkbox").first()).toBeChecked();
  const station = analyst.getByLabel("Station", { exact: true });
  await expect(station).toBeEnabled();
  await station.selectOption({ index: 1 });
  await analyst.getByLabel("Starts").fill(isoDate(1));
  await analyst.getByLabel("Ends").fill(isoDate(8));
  const proposed = analyst.waitForResponse((r) => r.url().endsWith("/bff/api/actions") && r.request().method() === "POST");
  await analyst.getByRole("button", { name: "Propose action" }).click();
  const action = (await (await proposed).json()) as { id: string };
  await expect(analyst.getByRole("status").filter({ hasText: "Action proposed" }).first()).toBeVisible();

  await supervisor.goto(`/review/actions/${action.id}`);
  await supervisor.getByLabel("Rationale").fill("Proportionate and time-limited; approved.");
  await supervisor.getByRole("button", { name: "Approve", exact: true }).click();
  await supervisor.getByRole("button", { name: "Yes, Approve" }).click();
  await expect(supervisor.getByRole("status").filter({ hasText: "Action approved" })).toBeVisible();
  await expect(supervisor.getByText("Status: Approved")).toBeVisible();
  await expect(supervisor.getByText(/Approved · demo\.supervisor/)).toBeVisible();
});

test("8. an AI insight is reviewed by a human, and the decision is in its history and the audit log", async ({ browser }) => {
  const steward = await as(browser, "demo.steward");
  await steward.goto("/review?tab=ai");
  await steward.getByRole("link", { name: /Review insight|Open insight/ }).first().click();
  await expect(steward.getByText("AI-assisted; analyst review required.").first()).toBeVisible();
  await expect(steward.getByText("Limitations").first()).toBeVisible();
  const insightId = new URL(steward.url()).pathname.split("/").pop() ?? "";
  expect(insightId).toMatch(/^[0-9a-f-]{36}$/);
  await steward.getByLabel("Review note").fill("E2E scenario 8: checked both records at source; the evidence supports this.");
  await steward.getByRole("button", { name: "Validate", exact: true }).click();
  await steward.getByRole("button", { name: "Yes, Validate" }).click();
  await expect(steward.getByRole("status").filter({ hasText: /validated|recorded/i }).first()).toBeVisible();
  const history = steward.getByRole("region", { name: "History" });
  await expect(history.getByText("demo.steward").first()).toBeVisible();

  // The auditor finds the review in the audit log (metadata only: never the note text).
  const auditor = await as(browser, "demo.auditor");
  await auditor.goto("/");
  const events = await bff(auditor, "GET", "audit/events?action=AI_INSIGHT_REVIEW&limit=50");
  expect(events.status).toBe(200);
  const rows = (events.body.data as { object_id: string; result: string; after_meta?: Record<string, unknown> }[]).filter((e) => e.object_id === insightId);
  expect(rows.map((e) => e.result)).toEqual(["ALLOW"]);
  expect(JSON.stringify(rows)).not.toContain("E2E scenario 8");
});

test("10. a data-quality issue raised at ingestion is reviewed and resolved, with its history", async ({ browser }) => {
  const steward = await as(browser, "demo.steward");
  // The ingested feed is visible in Data Health (the issue's origin).
  await steward.goto("/data-health");
  await expect(steward.getByRole("table", { name: "Source systems" }).getByText("DEMO_SYNTHETIC").first()).toBeVisible();

  await steward.goto("/review?tab=dq");
  const table = steward.getByRole("table", { name: "Data-quality issues" });
  // An incident-level issue raised by an ingestion rule.
  const row = table.getByRole("row").filter({ hasText: /Rule DQ-LOC-\d+/ }).filter({ hasText: "Open" }).first();
  await expect(row).toBeVisible();
  const historyHref = await row.getByRole("link", { name: "History" }).getAttribute("href");
  expect(historyHref).toMatch(/type=DQ_ISSUE&id=/);
  await row.getByRole("button", { name: "Resolve" }).click();
  await steward.getByLabel("Resolution note").fill("E2E scenario 10: location checked against the source record and corrected there.");
  await steward.getByRole("button", { name: "Confirm" }).click();
  await expect(steward.getByRole("status").filter({ hasText: /resolved|saved|recorded/i }).first()).toBeVisible();

  await steward.goto(historyHref!);
  const history = steward.getByRole("region", { name: "History" });
  await expect(history.getByText("demo.steward").first()).toBeVisible();
  await expect(history.getByText(/Resolved/).first()).toBeVisible();
});

test("2. a station officer sees only their assigned station's data", async ({ browser }) => {
  const officer = await as(browser, "demo.officer");
  await officer.goto("/");
  const region = officer.getByRole("combobox", { name: "Region", exact: true });
  await expect(region.getByRole("option")).toHaveText(["Puducherry"]);
  const station = officer.getByRole("combobox", { name: "Station", exact: true });
  const stations = (await station.getByRole("option").allTextContents()).filter((o) => !o.startsWith("All"));
  expect(stations).toEqual(["Puducherry Demo Station 01 (Demo)"]);
  // A record of another station, opened directly, answers like a record that does not exist.
  // Search across the region's references returns only the officer's own station's records.
  const found = await bff(officer, "GET", "search?q=DEMO-PDY-2026&region=PDY");
  expect(found.status).toBe(200);
  const incidents = (found.body?.incidents ?? []) as { station_code: string }[];
  expect(incidents.length).toBeGreaterThan(0);
  expect(new Set(incidents.map((i) => i.station_code))).toEqual(new Set(["PDY-S01"]));
  const guessed = await bff(officer, "GET", `incidents/${"0".repeat(8)}-0000-4000-8000-${"0".repeat(12)}/summary`);
  expect(guessed.status).toBe(404);
});

test("6–7. an officer records completion of an approved action, then the analyst completes the outcome review", async ({ browser }) => {
  test.setTimeout(240_000);
  const analyst = await as(browser, "demo.analyst");
  const sho = await as(browser, "demo.sho");
  const officer = await as(browser, "demo.officer");
  const stamp = Date.now();

  // An approved action at the officer's station (Puducherry Demo Station 01), dated in the past so
  // that its outcome can be reviewed now.
  await analyst.goto("/analytics?section=location&region=PDY&station=PDY-S01&period_from=2026-06-01&period_to=2026-08-31&time_basis=OCCURRENCE&record_type=CASE&comparison=PREVIOUS");
  await analyst.getByRole("button", { name: "Create Pattern Review" }).click();
  const create = analyst.getByRole("dialog", { name: "Create pattern review" });
  await create.getByRole("group", { name: "Cells (higher recorded counts first)" }).getByRole("checkbox").first().check();
  await create.getByLabel("Analyst narrative (at least 20 characters)").fill(`E2E ${stamp}: evening property thefts concentrate in this cell.`);
  await create.getByLabel("Recommended next step").fill("Evening visibility patrols for one month.");
  const created = analyst.waitForResponse((r) => r.url().endsWith("/bff/api/pattern-reviews") && r.request().method() === "POST");
  await create.getByRole("button", { name: "Create draft" }).click();
  const review = (await (await created).json()) as { id: string };
  await analyst.goto(`/review/patterns/${review.id}`);
  await analyst.getByLabel("Analyst assessment").fill("Consistent across the comparison period.");
  await analyst.getByRole("button", { name: "Submit for decision" }).click();
  await expect(analyst.getByRole("status").filter({ hasText: "Submitted" }).first()).toBeVisible();

  await sho.goto(`/review/patterns/${review.id}`);
  await sho.getByLabel("Rationale").fill("Supported by the evidence and the baseline.");
  await sho.getByRole("button", { name: "Approve", exact: true }).click();
  await sho.getByRole("button", { name: "Yes, Approve" }).click();
  await expect(sho.getByRole("status").filter({ hasText: "Decision recorded" }).first()).toBeVisible();

  await analyst.goto(`/review/patterns/${review.id}`);
  await analyst.getByLabel("Description").fill(`E2E ${stamp}: evening visibility patrols in the selected cell.`);
  const station = analyst.getByLabel("Station", { exact: true });
  await expect(station).toBeEnabled();
  await station.selectOption("PDY-S01");
  const assignee = analyst.getByLabel("Assigned officer");
  await expect(assignee).toBeEnabled();
  await assignee.selectOption({ label: "Demo Station Officer" });
  await analyst.getByLabel("Starts").fill("2026-09-01");
  await analyst.getByLabel("Ends").fill("2026-09-30");
  const proposed = analyst.waitForResponse((r) => r.url().endsWith("/bff/api/actions") && r.request().method() === "POST");
  await analyst.getByRole("button", { name: "Propose action" }).click();
  const action = (await (await proposed).json()) as { id: string };
  expect(action.id).toBeTruthy();

  await sho.goto(`/review/actions/${action.id}`);
  await sho.getByLabel("Rationale").fill("Proportionate and time-limited.");
  await sho.getByRole("button", { name: "Approve", exact: true }).click();
  await sho.getByRole("button", { name: "Yes, Approve" }).click();
  await expect(sho.getByText("Status: Approved")).toBeVisible();

  // 6. The assigned officer records completion.
  await officer.goto(`/review/actions/${action.id}`);
  await officer.getByLabel("Completion evidence").fill("Patrols logged in the station diary for each evening.");
  await officer.getByLabel("Reference (e.g. log number)").fill(`DIARY-${stamp}`);
  await officer.getByRole("button", { name: "Record completion" }).click();
  await officer.getByRole("button", { name: "Yes, Record completion" }).click();
  await expect(officer.getByText("Status: Completed")).toBeVisible();

  // 7. The analyst computes the before/after counts and finalises the outcome review.
  await analyst.goto(`/review/actions/${action.id}`);
  await analyst.getByRole("button", { name: "Compute and start" }).click();
  await expect(analyst.getByRole("status").filter({ hasText: "Outcome review started" }).first()).toBeVisible();
  await analyst.getByRole("textbox", { name: "Conclusion", exact: true }).fill("Counts alone show no clear change; continue to monitor the area.");
  await analyst.getByLabel("Reference type").selectOption("DOCUMENT");
  await analyst.getByLabel("Document reference").fill(`DIARY-${stamp}`);
  await analyst.getByRole("button", { name: "Add reference" }).click();
  await analyst.getByRole("button", { name: "Finalise", exact: true }).click();
  await analyst.getByRole("button", { name: "Yes, Finalise" }).click();
  await expect(analyst.getByRole("status").filter({ hasText: "Outcome review finalised" }).first()).toBeVisible();
});
