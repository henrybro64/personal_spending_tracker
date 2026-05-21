import { expect, test, type Page } from "@playwright/test";


const sampleRun = {
  id: 7,
  imported_at: "2026-05-17T10:00:00+00:00",
  statement_count: 2,
  transaction_count: 3,
  needs_review_count: 1,
  csv_output_path: "/app/batch/data/batch_transactions.csv",
  sqlite_output_path: "/app/batch/data/batch_imports.sqlite3",
  pdf_source_dir: "/app/batch/pdf",
  text_source_dir: "/app/batch/text",
};

const sampleTransactions = [
  {
    id: 11,
    import_run_id: 7,
    statement_file: "2026-01-29.pdf",
    bank: "AMEX",
    card_last4: "1001",
    transaction_date: "2026-01-05",
    posted_date: null,
    merchant_raw: "STARBUCKS",
    amount: 5.75,
    currency: "USD",
    transaction_type: "purchase",
    category: "Dining / Food",
    exclude_from_spending: false,
    needs_review: false,
  },
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
];

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


test("batch review page loads queue, runs import, and syncs to dashboard", async ({
  page,
}) => {
  let mergePayload: Record<string, unknown> | null = null;

  await mockAuthenticatedSession(page);

  await page.route("**/api/batch-imports/latest", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ run: sampleRun }),
    });
  });

  await page.route("**/api/batch-imports/transactions**", async (route) => {
    const url = new URL(route.request().url());
    const reviewOnly = url.searchParams.get("needs_review_only") === "true";
    const transactions = reviewOnly
      ? sampleTransactions.filter((transaction) => transaction.needs_review)
      : sampleTransactions;

    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ transactions }),
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

  await page.route("**/api/batch-imports/run", async (route) => {
    await route.fulfill({
      status: 201,
      contentType: "application/json",
      body: JSON.stringify({
        run: {
          ...sampleRun,
          transaction_count: 4,
          needs_review_count: 2,
        },
      }),
    });
  });

  await page.route("**/api/batch-imports/merge", async (route) => {
    mergePayload = route.request().postDataJSON() as Record<string, unknown>;
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        run_id: sampleRun.id,
        merged_count: 1,
        skipped_count: 1,
      }),
    });
  });

  await page.goto("/batch-review");

  await expect(page.getByText("Batch Statement Review")).toBeVisible();

  const reviewTable = page.getByRole("table", {
    name: "Batch import review queue",
  });
  await expect(
    reviewTable.getByRole("textbox", { name: "Merchant for transaction 12" })
  ).toHaveValue("UNKNOWN MERCHANT");

  await page.getByRole("switch", { name: "Show only flagged rows" }).uncheck();
  await expect(
    reviewTable.getByRole("textbox", { name: "Merchant for transaction 11" })
  ).toHaveValue("STARBUCKS");

  await page.getByRole("button", { name: "Run batch import" }).click();
  await expect(
    page.getByText("Imported 4 transactions from 2 statements.")
  ).toBeVisible();

  await page.getByRole("button", { name: "Sync to dashboard" }).click();
  await expect(page.getByText("Synced 1 transaction(s) to the dashboard.")).toBeVisible();
  expect(mergePayload).toMatchObject({ run_id: sampleRun.id });
});
