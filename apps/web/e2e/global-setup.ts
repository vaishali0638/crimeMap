import { chromium, type Page } from "@playwright/test";
import { chmodSync, mkdirSync, writeFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { KEYS_FILE, keys, totp } from "./totp";

/** Demo users the suites sign in as. A user without a TOTP key yet is enrolled at first sign-in
 * (the demo realm requires an authenticator) and the key is added to the git-ignored keys file. */
export const USERS = [
  "demo.analyst",
  "demo.steward",
  "demo.supervisor",
  "demo.officer",
  "demo.sho",
  "demo.admin",
  "demo.auditor",
] as const;
export type DemoUser = (typeof USERS)[number];

export const AUTH_DIR = fileURLToPath(new URL("./.auth/", import.meta.url));
export const statePath = (user: DemoUser) => `${AUTH_DIR}${user}.json`;

function password(): string {
  const value = process.env.CRIMEMAP_DEMO_PASSWORD;
  if (!value || value === "change-me") throw new Error("CRIMEMAP_DEMO_PASSWORD is not set (source .env)");
  return value;
}

const lastStep = new Map<string, number>();

async function waitForFreshCode(user: string): Promise<void> {
  // The realm refuses a reused code: wait for a step this user has not used, with 3 s to spare.
  for (;;) {
    const step = Math.floor(Date.now() / 30_000);
    const left = 30_000 - (Date.now() % 30_000);
    if (lastStep.get(user) !== step && left >= 3_000) {
      lastStep.set(user, step);
      return;
    }
    await new Promise((r) => setTimeout(r, left + 500));
  }
}

async function enrol(page: Page, user: string): Promise<string> {
  await page.getByRole("link", { name: /unable to scan/i }).click();
  const secret = (await page.locator("#kc-totp-secret-key").innerText()).replace(/\s/g, "");
  await waitForFreshCode(user);
  await page.locator("#totp").fill(totp(secret));
  const label = page.locator("#userLabel");
  if (await label.count()) await label.fill("crimemap-e2e");
  await page.locator("#saveTOTPBtn, input[type=submit], button[type=submit]").first().click();
  const all = keys();
  all[user] = secret;
  writeFileSync(KEYS_FILE, JSON.stringify(all, null, 2));
  chmodSync(KEYS_FILE, 0o600);
  return secret;
}

/** Signs in; returns true if the user had to enrol an authenticator first. */
export async function signIn(page: Page, user: string): Promise<boolean> {
  await page.goto("/");
  await page.locator("#username").fill(user);
  await page.locator("#password").fill(password());
  await page.locator("#kc-login").click();
  // Either the one-time-code prompt (enrolled) or the authenticator setup (first sign-in).
  const prompt = page.locator("#otp");
  const setup = page.getByRole("link", { name: /unable to scan/i });
  await Promise.race([prompt.waitFor({ timeout: 20_000 }), setup.waitFor({ timeout: 20_000 })]).catch(() => undefined);
  const enrolled = await setup.isVisible();
  if (enrolled) {
    await enrol(page, user);
  } else {
    const secret = keys()[user];
    if (!secret) throw new Error(`no TOTP key for ${user}`);
    await waitForFreshCode(user);
    await prompt.fill(totp(secret));
    await page.locator("#kc-login").click();
  }
  await page.waitForURL((url) => url.port === "3100" && !url.pathname.startsWith("/bff/"), { timeout: 30_000 });
  return enrolled;
}

export default async function globalSetup(): Promise<void> {
  mkdirSync(AUTH_DIR, { recursive: true, mode: 0o700 });
  // Local iteration: reuse saved sessions (they last as long as the BFF session).
  if (process.env.CRIMEMAP_E2E_REUSE_AUTH === "1") return;
  const browser = await chromium.launch({ channel: "chrome", headless: true });
  try {
    for (const user of USERS) {
      const baseURL = process.env.CRIMEMAP_WEB_BASE_URL ?? "http://localhost:3100";
      let context = await browser.newContext({ baseURL });
      try {
        if (await signIn(await context.newPage(), user)) {
          // A session created while enrolling has no OTP in its authentication methods, so
          // MFA-gated grants would not apply: sign in again with a code.
          await context.close();
          context = await browser.newContext({ baseURL });
          await signIn(await context.newPage(), user);
        }
        await context.storageState({ path: statePath(user) });
        chmodSync(statePath(user), 0o600);
      } catch (error) {
        // A user who cannot sign in makes only their tests fail (missing state), not the run.
        console.warn(`sign-in failed for ${user}: ${(error as Error).message.split("\n")[0]}`);
      } finally {
        await context.close();
      }
    }
  } finally {
    await browser.close();
  }
}
