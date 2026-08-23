import { expect, test } from "@playwright/test";

/**
 * Issue #83's literal acceptance criterion: "real browser verification
 * of the full round trip" for message-level feedback (FR-CH-6) — a real
 * register/login, a real streamed reply, a real POST .../feedback, and
 * a real page reload proving the selection was persisted server-side
 * (GET .../messages threads it back), not just held in client state.
 */
test("give thumbs-up feedback on a reply and see it persist across reload", async ({ page }) => {
  const email = `e2e-feedback-${Date.now()}-${Math.floor(Math.random() * 10_000)}@example.com`;
  const password = "s3cret-e2e!!";

  await page.goto("/login");
  await page.getByRole("button", { name: /need an account\?/i }).click();
  await page.getByLabel("Name").fill("E2E Feedback Tester");
  await page.getByLabel("Email").fill(email);
  await page.getByLabel("Password").fill(password);
  await page.getByRole("button", { name: /create account/i }).click();

  await expect(page).toHaveURL(/\/chat$/, { timeout: 15_000 });
  await expect(page.getByText("Setting up your workspace…")).toHaveCount(0, { timeout: 15_000 });

  const composer = page.getByLabel("Message");
  await composer.fill("hello from playwright");
  await page.getByRole("button", { name: "Send" }).click();

  // Wait for the reply to settle before feedback controls exist for it.
  await expect(page.getByRole("button", { name: "Send" })).toBeVisible({ timeout: 10_000 });

  const goodButton = page.getByRole("button", { name: "Good response" });
  await expect(goodButton).toBeVisible();
  await expect(goodButton).toHaveAttribute("aria-pressed", "false");

  await goodButton.click();
  await expect(goodButton).toHaveAttribute("aria-pressed", "true", { timeout: 10_000 });

  // Reload — the selection must come back from the server (GET
  // .../messages), not from any client-side-only state.
  await page.reload();
  await expect(page.getByRole("button", { name: "Send" })).toBeVisible({ timeout: 10_000 });
  await expect(page.getByRole("button", { name: "Good response" })).toHaveAttribute(
    "aria-pressed",
    "true",
    { timeout: 10_000 },
  );
});
