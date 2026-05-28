import { expect, test, type Page } from "@playwright/test";


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
