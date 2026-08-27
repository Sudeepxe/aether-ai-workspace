import { expect, test } from "@playwright/test";

/**
 * S12 #125: real footage for the README's demo GIF — a real browser
 * driving a real register -> upload -> grounded, cited answer flow
 * against the real running stack. Not part of the CI-gating e2e suite
 * (see `playwright.demo.config.ts`'s own header comment) — this is a
 * media-generation tool, not an assertion suite, though it still
 * asserts along the way so a broken recording fails loudly instead of
 * silently producing a GIF of an error page.
 *
 * Deliberately doesn't chase a refusal shot in the same session: the
 * local, non-semantic hash-embedding fallback (no real provider key in
 * this environment) can't reliably discriminate an unrelated query
 * from the one chunk already in a single-document KB — a genuine
 * finding while building this, not a fixable bug here. chat.spec.ts's
 * real refusal proof uses a fresh, zero-document workspace instead
 * (Gate 1 short-circuits on an empty KB regardless of embedding
 * quality), which is the honest way to demonstrate that path.
 */
test("demo: upload, grounded cited answer, refusal", async ({ page }) => {
  const email = `demo-${Date.now()}@example.com`;
  const password = "s3cret-demo!!";

  await page.goto("/login");
  await page.getByRole("button", { name: /need an account\?/i }).click();
  await page.getByLabel("Name").fill("Demo User");
  await page.getByLabel("Email").fill(email);
  await page.getByLabel("Password").fill(password);
  await page.getByRole("button", { name: /create account/i }).click();
  await expect(page).toHaveURL(/\/chat$/, { timeout: 15_000 });
  await expect(page.getByText("Setting up your workspace…")).toHaveCount(0, { timeout: 15_000 });
  await page.waitForTimeout(600);

  await page.getByRole("link", { name: "Documents" }).click();
  await expect(page).toHaveURL(/\/documents$/);
  await page.waitForTimeout(400);

  await page.getByLabel("Upload document").setInputFiles({
    name: "aether-policy.md",
    mimeType: "text/markdown",
    buffer: Buffer.from(
      "# Aether Support Policy\n\n" +
        "Aether offers a 45-day money-back guarantee on all annual subscriptions.\n",
    ),
  });
  await expect(page.getByText("Ready")).toBeVisible({ timeout: 60_000 });
  await page.waitForTimeout(800);

  await page.getByRole("link", { name: "Back to chat" }).click();
  await expect(page).toHaveURL(/\/chat$/);
  await page.waitForTimeout(400);

  await page.getByLabel("Message").fill("What is Aether's money-back guarantee period?");
  await page.getByRole("button", { name: "Send" }).click();
  const sources = page.getByRole("list", { name: "Sources" });
  await expect(sources).toBeVisible({ timeout: 15_000 });
  await expect(sources).toContainText("aether-policy.md");
  await page.waitForTimeout(2_000);
});
