import { defineConfig } from "@playwright/test";

/**
 * End-to-end, accessibility and visual tests (Phase 8) against the running local stack:
 * Compose (API, Keycloak) plus `pnpm start` on :3100. DEMO / SYNTHETIC DATA only.
 *
 * Uses the installed Google Chrome (`channel: "chrome"`): no browser download. Demo users sign in
 * once in global setup with the demo password (CRIMEMAP_DEMO_PASSWORD, from .env) and their TOTP
 * keys (secrets/demo-totp-keys.json, git-ignored); sessions are saved under e2e/.auth (git-ignored).
 */
export default defineConfig({
  testDir: "./e2e",
  globalSetup: "./e2e/global-setup.ts",
  fullyParallel: false,
  workers: 1,
  retries: 0,
  timeout: 90_000,
  expect: { timeout: 15_000, toHaveScreenshot: { maxDiffPixelRatio: 0.01, animations: "disabled" } },
  reporter: [["list"], ["json", { outputFile: "test-results/e2e-results.json" }]],
  snapshotPathTemplate: "{testDir}/__screenshots__/{testFilePath}/{arg}{ext}",
  use: {
    baseURL: process.env.CRIMEMAP_WEB_BASE_URL ?? "http://localhost:3100",
    channel: "chrome",
    headless: true,
    viewport: { width: 1440, height: 900 },
    locale: "en-IN",
    timezoneId: "Asia/Kolkata",
    trace: "retain-on-failure",
  },
});
