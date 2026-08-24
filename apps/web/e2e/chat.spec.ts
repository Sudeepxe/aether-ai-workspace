import { expect, test } from "@playwright/test";

/**
 * Issue #28's literal acceptance criterion: an e2e test drives
 * login -> send message -> see the streamed response settle. Against
 * the real stack (real API, real Postgres, real Redis) — this is the
 * one test in the whole suite that proves the frontend and backend
 * halves of the streaming spine actually work together, not just
 * independently.
 *
 * Since issue #60 (S6), every chat turn is grounded, and a freshly
 * created workspace has no ingested documents — so the real reply here
 * is Gate 1's refusal (ADR-6.4), not an echo. That's still a genuine,
 * real round trip through the whole streaming spine (SSE grammar,
 * persistence, settlement); it's just no longer literally an echo of
 * the user's own text. The refusal string must match
 * ports.chat.NOT_IN_KNOWLEDGE_BASE_REPLY on the backend.
 */
const NOT_IN_KNOWLEDGE_BASE_REPLY = "I don't have information about that in the knowledge base.";

test("register, log in, send a message, and see the streamed refusal settle", async ({ page }) => {
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

  // CreateWorkspace provisions a default $5.00/mo budget (issue #34) —
  // proves GET /budget renders through a real browser, not just a unit
  // test against a fake. EchoGenerator's cost is always zero, so this
  // stays $0.00 even after the message below settles.
  await expect(page.getByText("$0.00 / $5.00")).toBeVisible({ timeout: 10_000 });

  const composer = page.getByLabel("Message");
  await composer.fill("hello from playwright");
  await page.getByRole("button", { name: "Send" }).click();

  // The user's own message renders (seq order puts it before the reply).
  await expect(page.getByText("hello from playwright")).toBeVisible();

  // The streamed refusal reply appears — a real round trip through the
  // whole streaming spine (Gate 1 short-circuit, SSE meta/token/usage/done,
  // persistence), even though it's a single token rather than several.
  await expect(page.getByText(NOT_IN_KNOWLEDGE_BASE_REPLY)).toBeVisible({ timeout: 10_000 });

  // Settled: the "Send" button reappears (Composer only shows "Stop"
  // while streaming) and the reply has no stray cursor indicator.
  await expect(page.getByRole("button", { name: "Send" })).toBeVisible({ timeout: 10_000 });
  await expect(page.locator('[aria-hidden="true"]', { hasText: "▍" })).toHaveCount(0);

  // Issue #83's literal acceptance criterion: "real browser verification
  // of the full round trip" for message-level feedback (FR-CH-6) — a
  // real POST .../feedback and a real reload proving the selection was
  // persisted server-side (GET .../messages threads it back), not just
  // held in client state. Reuses this test's already-authenticated
  // session rather than a separate register+login e2e spec, since the
  // real AUTH endpoint is rate-limited (10 req/60s per IP, §7.5) and a
  // third register+login flow in the same CI job tips the whole e2e
  // suite over that budget (see issue #83's PR history).
  const goodButton = page.getByRole("button", { name: "Good response" });
  await expect(goodButton).toBeVisible();
  await expect(goodButton).toHaveAttribute("aria-pressed", "false");

  await goodButton.click();
  await expect(goodButton).toHaveAttribute("aria-pressed", "true", { timeout: 10_000 });

  await page.reload();
  await expect(page.getByRole("button", { name: "Send" })).toBeVisible({ timeout: 10_000 });
  await expect(page.getByRole("button", { name: "Good response" })).toHaveAttribute(
    "aria-pressed",
    "true",
    { timeout: 10_000 },
  );
});
