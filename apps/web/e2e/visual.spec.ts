import { expect, test, type Page } from "@playwright/test";
import { existsSync } from "node:fs";
import { statePath, type DemoUser } from "./global-setup";

/**
 * Visual regression for the key layouts (spec QA) at the desktop size the spec designs for (1440 px).
 * Baselines live in e2e/__screenshots__ and were created from the Phase 8 build on the development
 * machine; refresh them deliberately with `--update-snapshots` after an intended layout change.
 * Masked: the WebGL map canvas (renderer-dependent), live counts, times and badges.
 */
async function open(browser: import("@playwright/test").Browser, user: DemoUser, path: string): Promise<Page> {
  test.skip(!existsSync(statePath(user)), `blocked: no session for ${user}`);
  const context = await browser.newContext({ storageState: statePath(user), viewport: { width: 1440, height: 900 } });
  const page = await context.newPage();
  await page.goto(path);
  await page.waitForLoadState("networkidle");
  await page.evaluate(() => document.fonts.ready);
  return page;
}

const dynamic = (page: Page) => [
  page.locator("canvas"),
  page.getByText(/Data refreshed/),
  page.getByRole("status"),
  page.locator("header nav span.rounded-full"),
  page.getByRole("region", { name: "Legend" }),
];

// Fixed dates: the default period is relative to today and would change every day.
async function period(page: Page, apply: boolean): Promise<void> {
  await page.getByRole("textbox", { name: "From" }).fill("2026-09-01");
  await page.getByRole("textbox", { name: "To" }).fill("2026-09-30");
  if (apply) await page.getByRole("button", { name: "Apply" }).click();
  await page.waitForLoadState("networkidle");
  await expect(page.getByText(/2026-09-01 to 2026-09-30/).first()).toBeVisible();
  await page.evaluate(() => (document.activeElement as HTMLElement | null)?.blur()); // no focus ring
}

test("visual: Home Map layout", async ({ browser }) => {
  const page = await open(browser, "demo.analyst", "/");
  await period(page, true);
  await expect(page).toHaveScreenshot("home-map.png", { mask: dynamic(page), fullPage: false });
});

test("visual: PY Dashboard layout", async ({ browser }) => {
  const page = await open(browser, "demo.supervisor", "/dashboard");
  await period(page, false);
  await expect(page).toHaveScreenshot("py-dashboard.png", { mask: [...dynamic(page), page.getByRole("list", { name: "Stations" })] });
});

test("visual: Analytics Workbench layout", async ({ browser }) => {
  const page = await open(browser, "demo.analyst", "/analytics?section=overview&region=PDY&period_from=2026-07-01&period_to=2026-09-30");
  await expect(page).toHaveScreenshot("analytics-overview.png", { mask: [...dynamic(page), page.getByRole("tabpanel")] });
});
