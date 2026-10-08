import { expect, test } from "@playwright/test";

import { mockAuthenticatedSession } from "./support/session";

function dashboardPayload(merchant: string, total: number) {
  return {
    active_currency: "USD",
    summary: {
      total_spending: total,
      number_of_transactions: 1,
      average_transaction_amount: total,
      number_of_statements: 1,
      unconverted_transaction_count: 0,
      statement_count_scope: "all_time",
    },
    sorted_transactions: [
      {
        date: "2025-04-21",
        merchant,
        amount: total,
        category: "Dining",
        currency: "USD",
        original_amount: total,
        original_currency: "USD",
        display_amount: total,
        display_currency: "USD",
        is_converted: false,
      },
    ],
    unconverted_transactions: [],
    merchant_breakdown: [],
    category_breakdown: [],
    date_breakdown: [],
    metrics: {
      selection_net: total,
      selection_spend: total,
      selection_refunds: 0,
      selection_average: total,
      upload_net: total,
      largest_transaction_amount: total,
      selection_coverage: 1,
      model_total_in_active_currency: total,
    },
  };
}

test("adding spending on Data Entry refreshes the dashboard on return", async ({
  page,
}) => {
  let dashboardLoads = 0;

  await mockAuthenticatedSession(page);
  await page.route("**/api/uploads/recent?limit=6", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ uploads: [] }),
    });
  });
  await page.route("**/api/dashboard/view**", async (route) => {
    dashboardLoads += 1;
    const merchant = dashboardLoads === 1 ? "OLD CAFE" : "NIGHT MARKET";
    const total = dashboardLoads === 1 ? 20 : 50;
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(dashboardPayload(merchant, total)),
    });
  });
  await page.route("**/api/transactions/manual", async (route) => {
    await route.fulfill({
      status: 201,
      contentType: "application/json",
      body: JSON.stringify({ transaction: { id: 2, merchant: "NIGHT MARKET" } }),
    });
  });

  await page.goto("/");
  await expect(page.getByText("OLD CAFE")).toBeVisible();

  await page.getByRole("link", { name: "Data Entry" }).first().click();
  await page.getByLabel("Merchant / description").fill("NIGHT MARKET");
  await page.getByLabel("Amount").first().fill("50");
  await page.getByRole("button", { name: "Add spending" }).click();
  await expect(page.getByText("Manual spending added.")).toBeVisible();

  await page.getByRole("link", { name: "Dashboard" }).first().click();
  await expect(page.getByText("NIGHT MARKET")).toBeVisible();
  expect(dashboardLoads).toBeGreaterThanOrEqual(2);
});

test("adding spending on Data Entry refreshes Transactions on return", async ({
  page,
}) => {
  let manualAdded = false;

  await mockAuthenticatedSession(page);
  await page.route("**/api/transactions/manual", async (route) => {
    manualAdded = true;
    await route.fulfill({
      status: 201,
      contentType: "application/json",
      body: JSON.stringify({ transaction: { id: 2, merchant: "NIGHT MARKET" } }),
    });
  });
  await page.route("**/api/transactions?range=all&limit=5000", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        transactions: [
          {
            id: manualAdded ? 2 : 1,
            date: "2025-04-21",
            merchant: manualAdded ? "NIGHT MARKET" : "OLD CAFE",
            amount: manualAdded ? 50 : 20,
            category: "Dining",
            currency: "USD",
          },
        ],
      }),
    });
  });

  await page.goto("/data-entry");
  await page.getByLabel("Merchant / description").fill("NIGHT MARKET");
  await page.getByLabel("Amount").first().fill("50");
  await page.getByRole("button", { name: "Add spending" }).click();
  await expect(page.getByText("Manual spending added.")).toBeVisible();

  await page.getByRole("link", { name: "Transactions" }).first().click();
  await expect(
    page.getByRole("textbox", { name: "Merchant for transaction 2" })
  ).toHaveValue("NIGHT MARKET");
  await expect(
    page.getByRole("textbox", { name: "Merchant for transaction 1" })
  ).toHaveCount(0);
});

test("a failed transactions refresh keeps loaded rows and hides the raw browser error", async ({
  page,
}) => {
  let failReads = false;
  let recovered = false;

  await mockAuthenticatedSession(page);
  await page.route("**/api/transactions?range=all&limit=5000", async (route) => {
    if (failReads) {
      await route.abort("failed");
      return;
    }
    const transactions = [
      {
        id: 1,
        date: "2025-04-21",
        merchant: "KEEP CAFE",
        amount: 20,
        category: "Dining",
        currency: "USD",
      },
    ];
    if (!recovered) {
      transactions.push({
        id: 2,
        date: "2025-04-21",
        merchant: "DELETE ME",
        amount: 30,
        category: "Dining",
        currency: "USD",
      });
    }
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ transactions }),
    });
  });
  await page.route("**/api/transactions/2", async (route) => {
    await route.fulfill({ status: 204, body: "" });
  });

  await page.goto("/transactions");
  await expect(
    page.getByRole("textbox", { name: "Merchant for transaction 1" })
  ).toHaveValue("KEEP CAFE");
  await expect(
    page.getByRole("textbox", { name: "Merchant for transaction 2" })
  ).toHaveValue("DELETE ME");

  failReads = true;
  page.once("dialog", (dialog) => {
    void dialog.accept();
  });
  await page
    .getByRole("row")
    .filter({
      has: page.getByRole("textbox", { name: "Merchant for transaction 2" }),
    })
    .getByRole("button", { name: "Delete" })
    .click();

  await expect(page.getByTestId("transactions-load-warning")).toContainText(
    "Refresh failed. Retrying automatically. Showing the last successfully loaded data.",
    { timeout: 15_000 }
  );
  await expect(
    page.getByRole("textbox", { name: "Merchant for transaction 1" })
  ).toHaveValue("KEEP CAFE");
  await expect(page.getByText("Failed to fetch")).toHaveCount(0);

  failReads = false;
  recovered = true;
  await expect(page.getByTestId("transactions-load-warning")).toHaveCount(0, {
    timeout: 15_000,
  });
  await expect(
    page.getByRole("textbox", { name: "Merchant for transaction 1" })
  ).toHaveValue("KEEP CAFE");
  await expect(
    page.getByRole("textbox", { name: "Merchant for transaction 2" })
  ).toHaveCount(0);
});
