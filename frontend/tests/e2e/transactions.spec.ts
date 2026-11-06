import { expect, test, type Page } from "@playwright/test";

import { mockProviders } from "./support/session";

async function mockAuthenticatedSession(page: Page) {
  await mockProviders(page);
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

test("transactions page keeps mixed-currency totals grouped by original currency", async ({
  page,
}) => {
  await mockAuthenticatedSession(page);

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

  await page.goto("/transactions");

  await expect(
    page.getByText(
      "Row amounts use the original statement currency. Mixed-currency totals stay grouped by original currency on this page."
    )
  ).toBeVisible();
  await expect(
    page.getByText(
      "3 records | Net total by original currency: USD: US$50.00 · TWD: NT$605.00"
    )
  ).toBeVisible();
  await expect(
    page.getByText(
      "Subtotal by original currency: USD: US$50.00 · TWD: NT$605.00"
    )
  ).toBeVisible();
  await expect(page.getByText("Original amount")).toBeVisible();
  await expect(page.getByText("US$120.00", { exact: true })).toBeVisible();
  await expect(page.getByText("NT$605.00", { exact: true })).toBeVisible();
  await expect(
    page.getByText("Original statement currency: US$ (USD)").first()
  ).toBeVisible();
  await expect(
    page.getByText("Original statement currency: NT$ (TWD)")
  ).toBeVisible();
});

test("transactions retry automatically and do not show a false zero balance", async ({
  page,
}) => {
  let open = false;
  await mockAuthenticatedSession(page);
  await page.route("**/api/transactions?range=all&limit=5000", async (route) => {
    if (!open) {
      await route.abort("failed");
      return;
    }
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        transactions: [
          {
            id: 1,
            date: "2025-04-21",
            merchant: "RECOVERED CAFE",
            amount: 18,
            category: "Dining",
            currency: "USD",
          },
        ],
      }),
    });
  });

  await page.goto("/transactions");
  await expect(page.getByTestId("transactions-initial-recovery")).toContainText(
    "Unable to load data right now. Retrying automatically…",
    { timeout: 15_000 }
  );
  await expect(page.getByText("US$0.00")).toHaveCount(0);
  await expect(page.getByText(/0 records/)).toHaveCount(0);
  await expect(
    page.getByText("Upload a statement to activate merchant slicers.")
  ).toHaveCount(0);
  open = true;
  await expect(
    page.getByRole("textbox", { name: "Merchant for transaction 1" })
  ).toHaveValue("RECOVERED CAFE", { timeout: 15_000 });
  await expect(page.getByTestId("transactions-initial-recovery")).toHaveCount(0);
});

test("transactions show chronological dates by default with deterministic same-date ordering", async ({ page }) => {
  await mockAuthenticatedSession(page);
  await page.route("**/api/transactions?range=all&limit=5000", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        transactions: [
          { id: 9, date: "2026-10-09", merchant: "OCT 9 B", amount: 900, category: "Shopping", currency: "TWD" },
          { id: 4, date: "2026-10-03", merchant: "OCT 3", amount: 300, category: "Shopping", currency: "TWD" },
          { id: 8, date: "2026-10-09", merchant: "OCT 9 A", amount: 800, category: "Shopping", currency: "TWD" },
          { id: 1, date: "2026-10-01", merchant: "OCT 1", amount: 100, category: "Shopping", currency: "TWD" },
          { id: 6, date: "2026-10-06", merchant: "OCT 6", amount: 600, category: "Shopping", currency: "TWD" },
          { id: 5, date: "2026-10-05", merchant: "OCT 5", amount: 500, category: "Shopping", currency: "TWD" },
        ],
      }),
    });
  });

  await page.goto("/transactions");
  const merchants = page.getByRole("table", { name: "Transactions table" }).getByRole("textbox");
  const expected = ["OCT 1", "OCT 3", "OCT 5", "OCT 6", "OCT 9 A", "OCT 9 B"];
  await expect(merchants).toHaveCount(expected.length);
  for (let i = 0; i < expected.length; i += 1) {
    await expect(merchants.nth(i)).toHaveValue(expected[i]);
  }
});
