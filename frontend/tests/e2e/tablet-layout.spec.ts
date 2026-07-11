import { expect, test, type Locator, type Page } from "@playwright/test";

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

async function countGridColumns(locator: Locator) {
  return locator.evaluate((element) => {
    return getComputedStyle(element).gridTemplateColumns.trim().split(/\s+/).length;
  });
}

async function mockDashboardData(page: Page) {
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
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        active_currency: "USD",
        summary: {
          total_spending: 732.4,
          number_of_transactions: 4,
          average_transaction_amount: 183.1,
          number_of_statements: 2,
        },
        transactions: [
          {
            date: "2025-04-19",
            merchant: "STARBUCKS STORE 1234",
            amount: 5.75,
            category: "Dining",
            currency: "USD",
            original_amount: 5.75,
            original_currency: "USD",
            display_amount: 5.75,
            display_currency: "USD",
            is_converted: false,
          },
          {
            date: "2025-04-20",
            merchant: "UBER TRIP",
            amount: 18.9,
            category: "Transport",
            currency: "USD",
            original_amount: 605,
            original_currency: "TWD",
            display_amount: 18.9,
            display_currency: "USD",
            is_converted: true,
          },
          {
            date: "2025-04-21",
            merchant: "OPENAI API",
            amount: 20,
            category: "Digital Services",
            currency: "USD",
            original_amount: 20,
            original_currency: "USD",
            display_amount: 20,
            display_currency: "USD",
            is_converted: false,
          },
        ],
        sorted_transactions: [
          {
            date: "2025-04-21",
            merchant: "OPENAI API",
            amount: 20,
            category: "Digital Services",
            currency: "USD",
            original_amount: 20,
            original_currency: "USD",
            display_amount: 20,
            display_currency: "USD",
            is_converted: false,
          },
        ],
        merchant_breakdown: [
          {
            merchant: "OPENAI API",
            total: 20,
            abs_total: 20,
          },
          {
            merchant: "UBER TRIP",
            total: 18.9,
            abs_total: 18.9,
          },
        ],
        category_breakdown: [
          {
            category: "Digital Services",
            total: 20,
            share: 0.52,
          },
          {
            category: "Transport",
            total: 18.9,
            share: 0.48,
          },
        ],
        date_breakdown: [
          {
            date: "2025-04-20",
            total: 18.9,
            abs_total: 18.9,
          },
          {
            date: "2025-04-21",
            total: 20,
            abs_total: 20,
          },
        ],
        metrics: {
          selection_net: 38.9,
          selection_spend: 38.9,
          selection_refunds: 0,
          selection_average: 12.966,
          upload_net: 38.9,
          largest_transaction_amount: 20,
          selection_coverage: 1,
          model_total_in_active_currency: 4321.09,
        },
      }),
    });
  });
}

async function mockTransactionsData(page: Page) {
  await page.route("**/api/transactions?range=all&limit=5000", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        transactions: [
          {
            id: 1,
            date: "2025-04-21",
            merchant: "OPENAI API",
            amount: 120,
            category: "Digital Services",
            currency: "USD",
          },
          {
            id: 2,
            date: "2025-04-20",
            merchant: "CITY REFUND",
            amount: -70,
            category: "Refunds & Credits",
            currency: "USD",
          },
          {
            id: 3,
            date: "2025-04-19",
            merchant: "UBER TRIP",
            amount: 605,
            category: "Transport",
            currency: "TWD",
          },
        ],
      }),
    });
  });
}

async function mockBatchReviewData(page: Page) {
  await page.route("**/api/batch-imports/latest", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        run: {
          id: 7,
          imported_at: "2026-05-17T10:00:00+00:00",
          statement_count: 2,
          transaction_count: 3,
          needs_review_count: 1,
          csv_output_path: "/app/batch/data/batch_transactions.csv",
          sqlite_output_path: "/app/batch/data/batch_imports.sqlite3",
          pdf_source_dir: "/app/batch/pdf",
          text_source_dir: "/app/batch/text",
        },
      }),
    });
  });

  await page.route("**/api/batch-imports/transactions**", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        transactions: [
          {
            id: 12,
            import_run_id: 7,
            statement_file: "2026-01-29.pdf",
            bank: "AMEX",
            card_last4: "1001",
            transaction_date: "2026-01-08",
            posted_date: null,
            merchant_raw: "UNKNOWN MERCHANT",
            amount: 12.34,
            currency: "USD",
            transaction_type: "purchase",
            category: "Other Spending",
            exclude_from_spending: false,
            needs_review: true,
          },
        ],
      }),
    });
  });

  await page.route("**/api/batch-imports/digit-noise-pending-count**", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ pending_count: 0 }),
    });
  });

  await page.route("**/api/batch-imports/needs-review-merchants**", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        merchants: [
          {
            merchant_raw: "UNKNOWN MERCHANT",
            transaction_count: 1,
            sample_category: "Other Spending",
            sample_excluded: false,
            suggested_category: "Other Spending",
            can_auto_apply: false,
          },
        ],
      }),
    });
  });
}

test("dashboard keeps the desktop shell on common iPad landscape widths", async ({ page }) => {
  await mockAuthenticatedSession(page);
  await mockDashboardData(page);

  for (const viewport of [
    { width: 1024, height: 768 },
    { width: 1180, height: 820 },
    { width: 1194, height: 834 },
    { width: 1366, height: 1024 },
  ]) {
    await page.setViewportSize(viewport);
    await page.goto("/");

    const shell = page.getByTestId("dashboard-shell");
    await expect(shell).toBeVisible();
    await expect(page.getByText("Spending by category")).toBeVisible();
    expect(await countGridColumns(shell)).toBe(2);
  }
});

test("data-entry keeps a two-column manual form on iPad landscape", async ({ page }) => {
  await mockAuthenticatedSession(page);
  await page.setViewportSize({ width: 1024, height: 768 });

  await page.goto("/data-entry");

  const shell = page.getByTestId("data-entry-shell");
  const manualGrid = page.getByTestId("manual-entry-primary-grid");

  await expect(shell).toBeVisible();
  await expect(page.getByRole("button", { name: "Add spending" })).toBeVisible();
  expect(await countGridColumns(shell)).toBe(2);
  expect(await countGridColumns(manualGrid)).toBe(2);
});

test("transactions and batch review remain readable on 1024px tablet landscape", async ({
  page,
}) => {
  await mockAuthenticatedSession(page);
  await mockTransactionsData(page);
  await mockBatchReviewData(page);
  await page.setViewportSize({ width: 1024, height: 768 });

  await page.goto("/transactions");
  await expect(page.getByTestId("transactions-shell")).toBeVisible();
  await expect(page.getByText("Original amount")).toBeVisible();
  expect(await countGridColumns(page.getByTestId("transactions-shell"))).toBe(2);

  await page.goto("/batch-review");
  await expect(page.getByTestId("batch-review-shell")).toBeVisible();
  await expect(page.getByRole("table", { name: "Batch import review queue" })).toBeVisible();
  expect(await countGridColumns(page.getByTestId("batch-review-shell"))).toBe(2);
});
