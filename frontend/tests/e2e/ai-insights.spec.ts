import { expect, test } from "@playwright/test";

import { mockAuthenticatedSession } from "./support/session";

test("ai insights warns about rows left out of the converted snapshot", async ({
  page,
}) => {
  await mockAuthenticatedSession(page);
  await page.route("**/api/ai/settings", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        selected_provider: "openai",
        privacy_notice: "Insights send a summary.",
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
      }),
    });
  });
  await page.route("**/api/ai/insights", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        provider: "openai",
        model: "gpt-4.1",
        time_range: "quarter",
        generated_at: "2026-09-30T00:00:00+00:00",
        insight_text: "Observations\n- Lunch is the main category.",
        citations: [],
        data_shared: ["amounts and currencies"],
        snapshot: {
          range_key: "quarter",
          language: "en",
          display_currency: "TWD",
          transaction_count: 2,
          date_start: "2026-09-01",
          date_end: "2026-09-30",
          totals: {
            net: 300,
            spend: 300,
            refunds: 0,
            by_original_currency: [
              { currency: "JPY", absolute_total: 1000 },
              { currency: "TWD", absolute_total: 100 },
            ],
            unconverted_transaction_count: 1,
            unconverted_currencies: ["JPY"],
          },
          top_categories: [{ label: "Dining", amount: 300, share: 1, count: 2 }],
          top_merchants: [{ label: "TWD LUNCH", amount: 100, share: 0.3333, count: 1 }],
          recurring_merchants: [],
          monthly_totals: [],
          largest_expenses: [],
          merchant_concentration: {
            distinct_merchants: 2,
            top_5_share_of_spend: 1,
          },
        },
      }),
    });
  });

  await page.goto("/ai-insights");
  await page.getByRole("button", { name: "Generate insights" }).click();

  await expect(page.getByTestId("unconverted-transactions-warning")).toContainText(
    "1 transactions are not in these totals because they are not USD or TWD and have no exchange rate into TWD (JPY)."
  );
  await expect(page.getByText("NT$300.00", { exact: true })).toBeVisible();
  await expect(page.getByText("JPY: JPY 1,000")).toBeVisible();
});

test("ai insights settings appear on their own after the first load fails", async ({
  page,
}) => {
  let open = false;

  await mockAuthenticatedSession(page);
  await page.route("**/api/ai/settings", async (route) => {
    if (!open) {
      await route.abort("failed");
      return;
    }
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        selected_provider: "openai",
        privacy_notice: "Insights send a summary.",
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
      }),
    });
  });

  await page.goto("/ai-insights");
  await expect(page.getByTestId("ai-insights-initial-recovery")).toContainText(
    "Unable to load data right now. Retrying automatically…",
    { timeout: 15_000 }
  );
  await expect(page.getByRole("button", { name: "Retry now" })).toBeVisible();
  await expect(
    page.getByText("Configure a provider in Settings before generating insights.")
  ).toHaveCount(0);
  open = true;
  await expect(page.getByRole("button", { name: "Generate insights" })).toBeEnabled({
    timeout: 15_000,
  });
  await expect(page.getByTestId("ai-insights-initial-recovery")).toHaveCount(0);
});

test("a failed insight request is not repeated automatically", async ({ page }) => {
  let insightPosts = 0;

  await mockAuthenticatedSession(page);
  await page.route("**/api/ai/settings", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        selected_provider: "openai",
        privacy_notice: "Insights send a summary.",
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
      }),
    });
  });
  await page.route("**/api/ai/insights", async (route) => {
    insightPosts += 1;
    await route.abort("failed");
  });

  await page.goto("/ai-insights");
  await page.getByRole("button", { name: "Generate insights" }).click();
  await expect(page.getByText("Failed to fetch")).toBeVisible();
  await page.waitForTimeout(4_000);
  expect(insightPosts).toBe(1);
});
