import { expect, test, type Page } from "@playwright/test";

type DashboardQuery = {
  range: string;
  search: string;
  amountFilter: string;
  sort: string;
  selectedMerchant: string;
  currency: DisplayCurrency;
};

type DisplayCurrency = "USD" | "TWD";

async function mockAuthenticatedSession(page: Page) {
  await page.route("**/api/auth/session", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        authenticated: true,
        user: {
          id: 1,
          email: "tester@example.com",
          display_name: "Test User",
          created_at: "2026-05-18T00:00:00+00:00",
        },
        claimed_legacy_transactions: 0,
        claimed_legacy_uploads: 0,
      }),
    });
  });
}

function buildDashboardView(currency: DisplayCurrency) {
  const isTwd = currency === "TWD";
  const openaiAmount = isTwd ? 640 : 20;
  const uberAmount = isTwd ? 605 : 18.9;
  const starbucksAmount = isTwd ? 184 : 5.75;
  const paymentAmount = isTwd ? -3200 : -100;

  return {
    active_currency: currency,
    summary: {
      total_spending: isTwd ? 23456.78 : 732.4,
      number_of_transactions: 4,
      average_transaction_amount: isTwd ? 5864.2 : 183.1,
      number_of_statements: 2,
      unconverted_transaction_count: 0,
      statement_count_scope: "all_time" as const,
    },
    unconverted_transactions: [] as Array<{
      id: number;
      date: string;
      merchant: string;
      original_amount: number;
      original_currency: string;
      reason: string;
    }>,
    transactions: [
      {
        date: "2025-04-19",
        merchant: "STARBUCKS STORE 1234",
        amount: starbucksAmount,
        category: "Dining",
        currency,
        original_amount: 5.75,
        original_currency: "USD",
        display_amount: starbucksAmount,
        display_currency: currency,
        is_converted: isTwd,
      },
      {
        date: "2025-04-20",
        merchant: "UBER TRIP",
        amount: uberAmount,
        category: "Transport",
        currency,
        original_amount: 605,
        original_currency: "TWD",
        display_amount: uberAmount,
        display_currency: currency,
        is_converted: !isTwd,
      },
      {
        date: "2025-04-21",
        merchant: "OPENAI API",
        amount: openaiAmount,
        category: "Digital Services",
        currency,
        original_amount: 20,
        original_currency: "USD",
        display_amount: openaiAmount,
        display_currency: currency,
        is_converted: isTwd,
      },
      {
        date: "2025-04-22",
        merchant: "PAYMENT RECEIVED",
        amount: paymentAmount,
        category: "Refunds & Credits",
        currency,
        original_amount: -100,
        original_currency: "USD",
        display_amount: paymentAmount,
        display_currency: currency,
        is_converted: isTwd,
      },
    ],
    sorted_transactions: [
      {
        date: "2025-04-21",
        merchant: "OPENAI API",
        amount: openaiAmount,
        category: "Digital Services",
        currency,
        original_amount: 20,
        original_currency: "USD",
        display_amount: openaiAmount,
        display_currency: currency,
        is_converted: isTwd,
      },
      {
        date: "2025-04-20",
        merchant: "UBER TRIP",
        amount: uberAmount,
        category: "Transport",
        currency,
        original_amount: 605,
        original_currency: "TWD",
        display_amount: uberAmount,
        display_currency: currency,
        is_converted: !isTwd,
      },
      {
        date: "2025-04-19",
        merchant: "STARBUCKS STORE 1234",
        amount: starbucksAmount,
        category: "Dining",
        currency,
        original_amount: 5.75,
        original_currency: "USD",
        display_amount: starbucksAmount,
        display_currency: currency,
        is_converted: isTwd,
      },
      {
        date: "2025-04-22",
        merchant: "PAYMENT RECEIVED",
        amount: paymentAmount,
        category: "Refunds & Credits",
        currency,
        original_amount: -100,
        original_currency: "USD",
        display_amount: paymentAmount,
        display_currency: currency,
        is_converted: isTwd,
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

  await mockAuthenticatedSession(page);

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
            currency_totals: [
              {
                currency: "USD",
                total_amount: -74.25,
                transaction_count: 3,
              },
              {
                currency: "TWD",
                total_amount: 605,
                transaction_count: 1,
              },
            ],
            uploaded_at: "2026-05-16T09:30:00+00:00",
            parse_status: "parsed",
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
  await expect(page.getByText("Recent statements")).toBeVisible();
  await expect(page.getByText("Original totals")).toBeVisible();
  await expect(
    page.getByText("USD: -US$74.25 · TWD: NT$605.00")
  ).toBeVisible();
  await expect(page.getByLabel("Search")).toBeVisible();
  await expect(page.getByRole("combobox").first()).toBeVisible();
  await expect(page.getByText("US$4,321.09").first()).toBeVisible();
  await expect(
    page.getByText(
      "Dashboard cards and charts use US$ (USD) as the display currency. USD and TWD are converted for comparison. Other currencies need an exchange rate into TWD before they join these totals."
    )
  ).toBeVisible();
  await expect(page.getByText("Display currency: US$ (USD)").first()).toBeVisible();
  await expect(
    page.getByText(
      "Rows are shown in US$ (USD) for dashboard comparison. Converted rows keep the original statement amount underneath."
    )
  ).toBeVisible();
  await expect(page.getByText("Original: NT$605.00")).toBeVisible();

  const currencySwitch = page.getByRole("switch", {
    name: "Display currency",
  });
  await currencySwitch.click();

  await expect(currencySwitch).toBeChecked();
  await expect(page.getByText("NT$138,274.88").first()).toBeVisible();
  await expect(page.getByText("Personal Spending Tracker")).toBeVisible();
  await expect(
    page.getByText(
      "Dashboard cards and charts use NT$ (TWD) as the display currency. USD and TWD are converted for comparison. Other currencies need an exchange rate into TWD before they join these totals."
    )
  ).toBeVisible();
  await expect(page.getByText("Display currency: NT$ (TWD)").first()).toBeVisible();
  await expect(page.getByText("Original: US$20.00")).toBeVisible();

  expect(dashboardRequests.some((url) => url.includes("currency=USD"))).toBe(true);
  expect(dashboardRequests.some((url) => url.includes("currency=TWD"))).toBe(true);
});

test("an older dashboard response cannot overwrite a newer currency", async ({
  page,
}) => {
  let releaseStale = () => {};
  let markStaleSettled = () => {};
  const staleGate = new Promise<void>((resolve) => {
    releaseStale = resolve;
  });
  const staleSettled = new Promise<void>((resolve) => {
    markStaleSettled = resolve;
  });
  let heldStaleRequest = false;

  await mockAuthenticatedSession(page);

  await page.route("**/api/uploads/recent?limit=6", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ uploads: [] }),
    });
  });

  await page.route("**/api/dashboard/view**", async (route) => {
    const currency =
      new URL(route.request().url()).searchParams.get("currency") === "TWD"
        ? "TWD"
        : "USD";
    if (currency === "TWD" && !heldStaleRequest) {
      heldStaleRequest = true;
      try {
        await staleGate;
        await route.fulfill({
          status: 500,
          contentType: "application/json",
          body: JSON.stringify({ detail: "stale failure" }),
        });
      } catch {
        // The page aborts this request once a newer filter is selected.
      } finally {
        markStaleSettled();
      }
      return;
    }

    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(buildDashboardView(currency)),
    });
  });

  await page.goto("/");
  await expect(page.getByText("US$4,321.09").first()).toBeVisible();

  const currencySwitch = page.getByRole("switch", { name: "Display currency" });
  await currencySwitch.click();
  await expect.poll(() => heldStaleRequest).toBe(true);

  await currencySwitch.click();
  await expect(page.getByText("US$4,321.09").first()).toBeVisible();
  await expect(page.getByTestId("dashboard-shell")).toHaveAttribute(
    "data-dashboard-loading",
    "false"
  );

  releaseStale();
  await staleSettled;
  await expect(page.getByText("US$4,321.09").first()).toBeVisible();
  await expect(page.getByText("NT$138,274.88")).toHaveCount(0);
  await expect(page.getByText("stale failure")).toHaveCount(0);
  await expect(page.getByText("Dashboard data is unavailable.")).toHaveCount(0);
  await expect(page.getByTestId("dashboard-shell")).toHaveAttribute(
    "data-dashboard-loading",
    "false"
  );
});

test("clearing slicers requests the reset filters once and drops the previous search", async ({
  page,
}) => {
  const queries: DashboardQuery[] = [];

  await mockAuthenticatedSession(page);
  await page.route("**/api/uploads/recent?limit=6", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ uploads: [] }),
    });
  });
  await page.route("**/api/dashboard/view**", async (route) => {
    const requestUrl = new URL(route.request().url());
    const currency: DisplayCurrency =
      requestUrl.searchParams.get("currency") === "TWD" ? "TWD" : "USD";
    queries.push({
      range: requestUrl.searchParams.get("range") ?? "",
      search: requestUrl.searchParams.get("search") ?? "",
      amountFilter: requestUrl.searchParams.get("amount_filter") ?? "",
      sort: requestUrl.searchParams.get("sort") ?? "",
      selectedMerchant: requestUrl.searchParams.get("selected_merchant") ?? "",
      currency,
    });
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(buildDashboardView(currency)),
    });
  });

  await page.goto("/");
  await expect(page.getByText("US$4,321.09").first()).toBeVisible();

  const filters = page.getByRole("complementary");
  await filters.getByRole("button", { name: "Past month" }).click();
  await filters.getByLabel("Search").fill("uber");
  await filters.getByRole("button", { name: "Spend", exact: true }).click();
  await filters.locator("select").selectOption("date");
  await filters.getByRole("button", { name: "UBER TRIP" }).click();

  await expect
    .poll(() =>
      queries.some(
        (query) =>
          query.range === "month" &&
          query.search === "uber" &&
          query.amountFilter === "spend" &&
          query.sort === "date" &&
          query.selectedMerchant === "UBER TRIP" &&
          query.currency === "USD"
      )
    )
    .toBe(true);

  const requestCountBeforeClear = queries.length;
  await filters.getByRole("button", { name: "Clear slicers" }).click();
  await expect
    .poll(() =>
      queries.slice(requestCountBeforeClear).some(isClearedMonthQuery)
    )
    .toBe(true);
  await expect(page.getByTestId("dashboard-shell")).toHaveAttribute(
    "data-dashboard-loading",
    "false"
  );

  const requestsAfterClear = queries.slice(requestCountBeforeClear);
  expect(requestsAfterClear.length).toBeGreaterThan(0);
  expect(requestsAfterClear.every(isClearedMonthQuery)).toBe(true);
});

test("dashboard warns when transactions cannot be converted", async ({ page }) => {
  await mockAuthenticatedSession(page);
  await page.route("**/api/uploads/recent?limit=6", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ uploads: [] }),
    });
  });
  await page.route("**/api/dashboard/view**", async (route) => {
    const view = buildDashboardView("USD");
    view.summary = {
      ...view.summary,
      unconverted_transaction_count: 2,
      statement_count_scope: "all_time",
    };
    view.unconverted_transactions = [
      {
        id: 9,
        date: "2025-04-18",
        merchant: "JPY CAFE",
        original_amount: 500,
        original_currency: "JPY",
        reason: "unsupported_display_conversion",
      },
      {
        id: 10,
        date: "2025-04-17",
        merchant: "EUR BOOKS",
        original_amount: 8,
        original_currency: "EUR",
        reason: "unsupported_display_conversion",
      },
    ];
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(view),
    });
  });

  await page.goto("/");
  await expect(page.getByTestId("unconverted-transactions-warning")).toContainText(
    "2 transactions are not in these totals because they are not USD or TWD and have no exchange rate into TWD (EUR, JPY)."
  );
  await expect(
    page.getByText(
      "2 statements stored (all time). This total ignores search, merchant, and amount filters. It still follows the date range."
    )
  ).toBeVisible();
});

test("a failed refresh keeps the last dashboard numbers and can recover", async ({
  page,
}) => {
  let phase: "ok" | "fail" | "recovered" = "ok";

  await mockAuthenticatedSession(page);
  await page.route("**/api/uploads/recent?limit=6", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ uploads: [] }),
    });
  });
  await page.route("**/api/dashboard/view**", async (route) => {
    if (phase === "fail") {
      await route.abort("failed");
      return;
    }
    const currency =
      new URL(route.request().url()).searchParams.get("currency") === "TWD"
        ? "TWD"
        : "USD";
    const view = buildDashboardView(currency);
    if (phase === "recovered") {
      view.metrics.model_total_in_active_currency = 222.22;
    }
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(view),
    });
  });

  await page.goto("/");
  await expect(page.getByText("US$4,321.09").first()).toBeVisible();

  phase = "fail";
  await page.getByRole("switch", { name: "Display currency" }).click();
  await expect(page.getByTestId("dashboard-stale-warning")).toContainText(
    "Refresh failed. Retrying automatically. Showing the last successfully loaded data.",
    { timeout: 15_000 }
  );
  await expect(page.getByText("US$4,321.09").first()).toBeVisible();
  await expect(page.getByText("Failed to fetch")).toHaveCount(0);

  phase = "recovered";
  await page.getByRole("switch", { name: "Display currency" }).click();
  await expect(page.getByText("US$222.22").first()).toBeVisible({
    timeout: 15_000,
  });
  await expect(page.getByTestId("dashboard-stale-warning")).toHaveCount(0);
});

test("dashboard data appears on its own after the first load fails", async ({
  page,
}) => {
  let open = false;

  await mockAuthenticatedSession(page);
  await page.route("**/api/uploads/recent?limit=6", async (route) => {
    if (!open) {
      await route.abort("failed");
      return;
    }
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ uploads: [] }),
    });
  });
  await page.route("**/api/dashboard/view**", async (route) => {
    if (!open) {
      await route.abort("failed");
      return;
    }
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(buildDashboardView("USD")),
    });
  });

  await page.goto("/");
  await expect(page.getByTestId("dashboard-initial-recovery")).toContainText(
    "Unable to load data right now. Retrying automatically…",
    { timeout: 15_000 }
  );
  await expect(page.getByRole("button", { name: "Retry now" })).toBeVisible();
  await expect(page.getByText("US$0.00")).toHaveCount(0);
  await expect(page.getByText("No uploaded statements yet.")).toHaveCount(0);
  await expect(
    page.getByText("Upload a statement to activate merchant slicers.")
  ).toHaveCount(0);
  open = true;
  await expect(page.getByText("US$4,321.09").first()).toBeVisible({
    timeout: 15_000,
  });
  await expect(page.getByTestId("dashboard-initial-recovery")).toHaveCount(0);
});

test("a loaded dashboard recovers automatically after an outage", async ({
  page,
}) => {
  let phase: "ok" | "fail" | "recovered" = "ok";

  await mockAuthenticatedSession(page);
  await page.route("**/api/uploads/recent?limit=6", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ uploads: [] }),
    });
  });
  await page.route("**/api/dashboard/view**", async (route) => {
    if (phase === "fail") {
      await route.abort("failed");
      return;
    }
    const currency =
      new URL(route.request().url()).searchParams.get("currency") === "TWD"
        ? "TWD"
        : "USD";
    const view = buildDashboardView(currency);
    if (phase === "recovered") {
      view.metrics.model_total_in_active_currency = 7777;
    }
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(view),
    });
  });

  await page.goto("/");
  await expect(page.getByText("US$4,321.09").first()).toBeVisible();

  phase = "fail";
  await page.getByRole("switch", { name: "Display currency" }).click();
  await expect(page.getByTestId("dashboard-stale-warning")).toBeVisible({
    timeout: 15_000,
  });
  await expect(page.getByText("US$4,321.09").first()).toBeVisible();

  phase = "recovered";
  await expect(page.getByText("NT$7,777.00").first()).toBeVisible({
    timeout: 15_000,
  });
  await expect(page.getByTestId("dashboard-stale-warning")).toHaveCount(0);
});

function isClearedMonthQuery(query: DashboardQuery): boolean {
  return (
    query.range === "month" &&
    query.search === "" &&
    query.amountFilter === "all" &&
    query.sort === "amount_desc" &&
    query.selectedMerchant === "" &&
    query.currency === "USD"
  );
}
