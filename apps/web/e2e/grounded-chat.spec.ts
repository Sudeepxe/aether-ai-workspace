import { expect, test } from "@playwright/test";

/**
 * Issue #61's literal acceptance criterion: "manual verification in a
 * real browser driving a real grounded answer... end-to-end." A real
 * document (real MinIO upload, real ClamAV scan, real worker pipeline,
 * real embeddings) is ingested through the actual upload UI, then a
 * matching chat message proves the citation footer (issue #59's
 * provenance fields) renders from real retrieval + Gate 1 (#60), not a
 * fixture. The companion refusal case is covered end-to-end in
 * chat.spec.ts (a fresh, document-less workspace).
 *
 * Needs the full dev stack, not just Postgres/Redis — see ci.yml's e2e
 * job for the minio/clamav/worker services this test depends on.
 */
test("upload a document and see the grounded reply cite it", async ({ page }) => {
  const email = `e2e-grounded-${Date.now()}-${Math.floor(Math.random() * 10_000)}@example.com`;
  const password = "s3cret-e2e!!";

  await page.goto("/login");
  await page.getByRole("button", { name: /need an account\?/i }).click();
  await page.getByLabel("Name").fill("E2E Grounded Tester");
  await page.getByLabel("Email").fill(email);
  await page.getByLabel("Password").fill(password);
  await page.getByRole("button", { name: /create account/i }).click();
  await expect(page).toHaveURL(/\/chat$/, { timeout: 15_000 });
  await expect(page.getByText("Setting up your workspace…")).toHaveCount(0, { timeout: 15_000 });

  await page.getByRole("link", { name: "Documents" }).click();
  await expect(page).toHaveURL(/\/documents$/);

  await page.getByLabel("Upload document").setInputFiles({
    name: "zylonix-policy.md",
    mimeType: "text/markdown",
    buffer: Buffer.from(
      "# Zylonix Support Policy\n\n" +
        "Zylonix offers a 45-day money-back guarantee on all annual subscriptions.\n",
    ),
  });

  // The real pipeline (scan -> parse -> chunk -> embed) running end to
  // end — generous timeout for a cold-started worker/ClamAV in CI.
  await expect(page.getByText("Ready")).toBeVisible({ timeout: 60_000 });

  await page.getByRole("link", { name: "Back to chat" }).click();
  await expect(page).toHaveURL(/\/chat$/);

  await page.getByLabel("Message").fill("What is Zylonix's money-back guarantee period?");
  await page.getByRole("button", { name: "Send" }).click();

  // A real grounded, cited answer — not the refusal string.
  await expect(page.getByText("I don't have information about that")).toHaveCount(0, {
    timeout: 15_000,
  });
  const sources = page.getByRole("list", { name: "Sources" });
  await expect(sources).toBeVisible({ timeout: 15_000 });
  await expect(sources).toContainText("zylonix-policy.md");

  await expect(page.getByRole("button", { name: "Send" })).toBeVisible({ timeout: 10_000 });
});
