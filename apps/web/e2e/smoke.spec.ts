import { expect, test } from "@playwright/test";
import { statePath } from "./global-setup";

test.use({ storageState: statePath("demo.analyst") });

test("a signed-in analyst reaches the Home Map with the demo banner", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByText(/DEMO \/ SYNTHETIC DATA/i).first()).toBeVisible();
});
