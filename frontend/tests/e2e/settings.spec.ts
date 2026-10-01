import { expect, test } from "@playwright/test";

import { mockAuthenticatedSession } from "./support/session";

const settingsPayload = {
  selected_provider: "openai",
  privacy_notice: "Keys stay on this server.",
  providers: [
    {
      provider: "openai",
      display_name: "OpenAI",
      configured: true,
      selected: true,
      masked_api_key: "••••1234",
      model: "gpt-4.1",
      created_at: "2026-09-01T00:00:00+00:00",
      updated_at: "2026-09-01T00:00:00+00:00",
    },
  ],
};

test("settings appear on their own after the first load fails", async ({ page }) => {
  let open = false;

  await mockAuthenticatedSession(page);
  await page.route("**/api/ai/settings", async (route) => {
    if (route.request().method() !== "GET") {
      await route.fallback();
      return;
    }
    if (!open) {
      await route.abort("failed");
      return;
    }
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(settingsPayload),
    });
  });

  await page.goto("/settings");
  await expect(page.getByTestId("settings-initial-recovery")).toContainText(
    "Unable to load data right now. Retrying automatically…",
    { timeout: 15_000 }
  );
  await expect(page.getByRole("button", { name: "Retry now" })).toBeVisible();
  await expect(page.getByText("Not configured")).toHaveCount(0);
  open = true;
  await expect(page.getByText("Saved key: ••••1234")).toBeVisible({
    timeout: 15_000,
  });
  await expect(page.getByTestId("settings-initial-recovery")).toHaveCount(0);
});

test("saving a provider is not repeated automatically", async ({ page }) => {
  let saves = 0;

  await mockAuthenticatedSession(page);
  await page.route("**/api/ai/settings", async (route) => {
    if (route.request().method() !== "GET") {
      await route.fallback();
      return;
    }
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(settingsPayload),
    });
  });
  await page.route("**/api/ai/settings/openai", async (route) => {
    saves += 1;
    await route.abort("failed");
  });

  await page.goto("/settings");
  await expect(page.getByText("Saved key: ••••1234")).toBeVisible();
  await page.getByRole("button", { name: "Save and use" }).click();
  await expect(page.getByText("Failed to fetch")).toBeVisible();
  await page.waitForTimeout(4_000);
  expect(saves).toBe(1);
});
