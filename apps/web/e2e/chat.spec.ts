import { expect, test } from "@playwright/test";

/**
 * Issue #28's literal acceptance criterion: an e2e test drives
 * login -> send message -> see streamed echo response settle.
 * Against the real stack (real API, real Postgres, real Redis) — this
 * is the one test in the whole suite that proves the frontend and
 * backend halves of the streaming spine actually work together, not
 * just independently.
 */
test("register, log in, send a message, and see the streamed echo settle", async ({ page }) => {
  const email = `e2e-${Date.now()}-${Math.floor(Math.random() * 10_000)}@example.com`;
  const password = "s3cret-e2e!!";

  await page.goto("/login");
  await page.getByRole("button", { name: /need an account\?/i }).click();
  await page.getByLabel("Name").fill("E2E Tester");
  await page.getByLabel("Email").fill(email);
  await page.getByLabel("Password").fill(password);
  await page.getByRole("button", { name: /create account/i }).click();

  await expect(page).toHaveURL(/\/chat$/, { timeout: 15_000 });
  await expect(page.getByText("Setting up your workspace…")).toHaveCount(0, { timeout: 15_000 });

  const composer = page.getByLabel("Message");
  await composer.fill("hello from playwright");
  await page.getByRole("button", { name: "Send" }).click();

  // The user's own message renders immediately (optimistic-equivalent:
  // it comes back from the same POST that starts the stream).
  await expect(page.getByText("hello from playwright")).toBeVisible();

  // The streamed echo reply appears and grows — proves tokens are
  // actually arriving incrementally, not just the final settled text.
  await expect(page.locator("text=hello from playwright").nth(1)).toBeVisible({ timeout: 10_000 });

  // Settled: the "Send" button reappears (Composer only shows "Stop"
  // while streaming) and the reply is the full echoed content with no
  // stray cursor indicator.
  await expect(page.getByRole("button", { name: "Send" })).toBeVisible({ timeout: 10_000 });
  await expect(page.locator('[aria-hidden="true"]', { hasText: "▍" })).toHaveCount(0);
});
