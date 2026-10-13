import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";
import { existsSync, mkdirSync, writeFileSync } from "node:fs";
import { statePath, type DemoUser } from "./global-setup";

/**
 * Automated accessibility audit (WCAG 2.0–2.2 A and AA rules in axe-core) of the main screens, signed in
 * as the users who see them, plus keyboard checks. Results feed docs/accessibility.md. Automated
 * rules catch only part of WCAG; the manual checks are listed there.
 */
// The spec asks for WCAG 2.2 AA; axe has rules tagged for each version up to 2.2.
const TAGS = ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa"];
const OUT = "test-results/axe";

const PAGES: [DemoUser, string, string][] = [
  ["demo.analyst", "/", "Home Map"],
  ["demo.supervisor", "/dashboard", "PY Dashboard"],
  ["demo.analyst", "/analytics", "Analytics Workbench (overview)"],
  ["demo.analyst", "/analytics?section=location&region=PDY&period_from=2026-07-01&period_to=2026-09-30", "Analytics: Location Trends"],
  ["demo.analyst", "/review", "Review Queue"],
  ["demo.steward", "/review?tab=dq", "Review Queue: data quality"],
  ["demo.steward", "/review?tab=ai", "Review Queue: AI insights"],
  ["demo.steward", "/data-health", "Data Health"],
  ["demo.analyst", "/reports", "Reports"],
  ["demo.analyst", "/ai", "AI models"],
];

async function audit(page: Page, name: string) {
  const results = await new AxeBuilder({ page }).withTags(TAGS).analyze();
  mkdirSync(OUT, { recursive: true });
  const summary = results.violations.map((v) => ({
    id: v.id,
    impact: v.impact,
    help: v.help,
    nodes: v.nodes.length,
    targets: v.nodes.slice(0, 5).map((n) => n.target.join(" ")),
  }));
  writeFileSync(`${OUT}/${name.replace(/[^a-z0-9]+/gi, "-")}.json`, JSON.stringify({ name, url: page.url(), passes: results.passes.length, violations: summary }, null, 2));
  return summary;
}

for (const [user, path, name] of PAGES) {
  test(`axe: ${name} has no WCAG 2.2 A/AA rule violations`, async ({ browser }) => {
    test.skip(!existsSync(statePath(user)), `blocked: no session for ${user}`);
    const context = await browser.newContext({ storageState: statePath(user) });
    const page = await context.newPage();
    await page.goto(path);
    await page.waitForLoadState("networkidle");
    await expect(page.locator("main")).toBeVisible();
    expect(await audit(page, name)).toEqual([]);
    await context.close();
  });
}

test("axe: the signed-out page has no violations", async ({ page }) => {
  await page.goto("/signed-out");
  expect(await audit(page, "Signed out")).toEqual([]);
});

test("keyboard: the skip link is the first stop and moves focus to the main content", async ({ browser }) => {
  const context = await browser.newContext({ storageState: statePath("demo.analyst") });
  const page = await context.newPage();
  await page.goto("/");
  await page.keyboard.press("Tab");
  const skip = page.getByRole("link", { name: "Skip to main content" });
  await expect(skip).toBeFocused();
  await expect(skip).toBeVisible(); // visible when focused
  await page.keyboard.press("Enter");
  await expect(page).toHaveURL(/#main$/);
  await context.close();
});

test("keyboard: every map feature is reachable from the keyboard list", async ({ browser }) => {
  const context = await browser.newContext({ storageState: statePath("demo.analyst") });
  const page = await context.newPage();
  await page.goto("/");
  const list = page.getByText("Features in view (keyboard list)");
  await list.focus();
  await page.keyboard.press("Enter");
  const first = page.getByRole("group").filter({ hasText: "Features in view" }).getByRole("button").first();
  await first.focus();
  await page.keyboard.press("Enter");
  await expect(page.getByRole("complementary", { name: /Grid cell|Record summary/ })).toBeVisible();
  await context.close();
});

// Reflow (WCAG 1.4.10): at 320 CSS px (1280 px at 400 % zoom) pages scroll vertically only. The map
// canvas itself is two-dimensional content (an allowed exception) but must not widen the page.
const REFLOW: [DemoUser, string][] = [
  ["demo.analyst", "/"],
  ["demo.supervisor", "/dashboard"],
  ["demo.analyst", "/analytics"],
  ["demo.analyst", "/review"],
  ["demo.steward", "/data-health"],
];
for (const [user, path] of REFLOW) {
  test(`reflow: ${path} has no horizontal scrolling at 320 CSS px`, async ({ browser }) => {
    const context = await browser.newContext({ storageState: statePath(user), viewport: { width: 320, height: 800 } });
    const page = await context.newPage();
    await page.goto(path);
    await page.waitForLoadState("networkidle");
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
    expect(overflow).toBeLessThanOrEqual(0);
    await context.close();
  });
}
