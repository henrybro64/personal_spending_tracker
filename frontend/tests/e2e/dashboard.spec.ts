import { expect, test } from "@playwright/test";


type DisplayCurrency = "USD" | "TWD";

function buildDashboardView(currency: DisplayCurrency) {
  const isTwd = currency === "TWD";

  return {
    active_currency: currency,
    summary: {
      total_spending: isTwd ? 23456.78 : 732.4,
      number_of_transactions: 4,
      average_transaction_amount: isTwd ? 5864.2 : 183.1,
      number_of_statements: 2,
    },
    transactions: [
      {
        date: "2025-04-19",
        merchant: "STARBUCKS STORE 1234",
        amount: isTwd ? 184 : 5.75,
        category: "Dining",
        currency,
      },
      {
        date: "2025-04-20",
        merchant: "UBER TRIP",
        amount: isTwd ? 605 : 18.9,
        category: "Transport",
        currency,
      },
      {
        date: "2025-04-21",
        merchant: "OPENAI API",
        amount: isTwd ? 640 : 20,
        category: "Digital Services",
        currency,
      },
      {
        date: "2025-04-22",
        merchant: "PAYMENT RECEIVED",
        amount: isTwd ? -3200 : -100,
        category: "Refunds & Credits",
        currency,
      },
    ],
    sorted_transactions: [
      {
        date: "2025-04-21",
        merchant: "OPENAI API",
        amount: isTwd ? 640 : 20,
        category: "Digital Services",
        currency,
      },
      {
        date: "2025-04-20",
        merchant: "UBER TRIP",
        amount: isTwd ? 605 : 18.9,
        category: "Transport",
        currency,
      },
      {
        date: "2025-04-19",
        merchant: "STARBUCKS STORE 1234",
        amount: isTwd ? 184 : 5.75,
        category: "Dining",
        currency,
      },
      {
        date: "2025-04-22",
        merchant: "PAYMENT RECEIVED",
        amount: isTwd ? -3200 : -100,
        category: "Refunds & Credits",
        currency,
      },
    ],
    merchant_breakdown: [
      {
        merchant: "OPENAI API",
        total: isTwd ? 640 : 20,
        abs_total: isTwd ? 640 : 20,
      },
      {
        merchant: "UBER TRIP",
        total: isTwd ? 605 : 18.9,
        abs_total: isTwd ? 605 : 18.9,
      },
      {
        merchant: "STARBUCKS STORE 1234",
        total: isTwd ? 184 : 5.75,
        abs_total: isTwd ? 184 : 5.75,
      },
    ],
    category_breakdown: [
      {
        category: "Digital Services",
        total: isTwd ? 640 : 20,
        share: 0.45,
      },
      {
        category: "Transport",
        total: isTwd ? 605 : 18.9,
        share: 0.42,
      },
      {
        category: "Dining",
        total: isTwd ? 184 : 5.75,
        share: 0.13,
      },
    ],
    date_breakdown: [
      {
        date: "2025-04-19",
        total: isTwd ? 184 : 5.75,
        abs_total: isTwd ? 184 : 5.75,
      },
      {
        date: "2025-04-20",
        total: isTwd ? 605 : 18.9,
        abs_total: isTwd ? 605 : 18.9,
      },
      {
        date: "2025-04-21",
        total: isTwd ? 640 : 20,
        abs_total: isTwd ? 640 : 20,
      },
      {
        date: "2025-04-22",
        total: isTwd ? -3200 : -100,
        abs_total: isTwd ? 3200 : 100,
      },
    ],
    metrics: {
      selection_net: isTwd ? -1771 : -55.35,
      selection_spend: isTwd ? 1429 : 44.65,
      selection_refunds: isTwd ? 3200 : 100,
      selection_average: isTwd ? -442.75 : -13.8375,
      upload_net: isTwd ? -1771 : -55.35,
      largest_transaction_amount: isTwd ? 3200 : 100,
      selection_coverage: 1,
      model_total_in_active_currency: isTwd ? 138274.88 : 4321.09,
    },
  };
}

test("dashboard renders with mocked API data and survives a currency toggle", async ({
  page,
}) => {
  const dashboardRequests: string[] = [];

  await page.route("**/api/uploads/recent?limit=6", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        uploads: [
          {
            file_name: "sample.pdf",
            display_name: "Sample Statement.pdf",
            transaction_count: 4,
            total_amount: 44.65,
            uploaded_at: "2026-05-16T09:30:00+00:00",
          },
        ],
      }),
    });
  });

  await page.route("**/api/dashboard/view**", async (route) => {
    const requestUrl = new URL(route.request().url());
    const currency =
      requestUrl.searchParams.get("currency") === "TWD" ? "TWD" : "USD";
    dashboardRequests.push(route.request().url());

    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(buildDashboardView(currency)),
    });
  });

  await page.goto("/");

  await expect(page.getByText("Personal Spending Tracker")).toBeVisible();
  await expect(page.getByText("Spending Overview")).toBeVisible();
  await expect(page.getByText("Net in view")).toBeVisible();
  await expect(page.getByText("Spend only")).toBeVisible();
  await expect(page.getByText("Rows in view")).toBeVisible();
  await expect(page.getByText("Merchant contribution")).toBeVisible();
  await expect(page.getByText("Spending by category")).toBeVisible();
  await expect(page.getByLabel("Search")).toBeVisible();
  await expect(page.getByRole("combobox").first()).toBeVisible();
  await expect(page.getByText("$4,321.09").first()).toBeVisible();

  const currencySwitch = page.getByRole("switch", {
    name: "Display currency",
  });
  await currencySwitch.click();

  await expect(currencySwitch).toBeChecked();
  await expect(page.getByText("NT$138,274.88").first()).toBeVisible();
  await expect(page.getByText("Personal Spending Tracker")).toBeVisible();

  expect(dashboardRequests.some((url) => url.includes("currency=USD"))).toBe(true);
  expect(dashboardRequests.some((url) => url.includes("currency=TWD"))).toBe(true);
});
