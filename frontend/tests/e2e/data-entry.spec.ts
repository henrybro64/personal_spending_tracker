import path from "node:path";

import { expect, test, type Page } from "@playwright/test";


const samplePdfPath = path.join(
  process.cwd(),
  "tests",
  "e2e",
  "fixtures",
  "sample.pdf"
);

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

test("manual transaction form validates client-side and posts on success", async ({
  page,
}) => {
  let manualPayload: Record<string, unknown> | null = null;

  await mockAuthenticatedSession(page);

  await page.route("**/api/transactions/manual", async (route) => {
    manualPayload = route.request().postDataJSON() as Record<string, unknown>;
    await route.fulfill({
      status: 201,
      contentType: "application/json",
      body: JSON.stringify({
        transaction: {
          date: "2026-05-16",
          merchant: "Coffee Project",
          amount: 12.5,
          category: "Dining",
          currency: "USD",
          payment_method: "Cash",
          file_name: "manual-entry",
          original_file_name: "Manual entry",
        },
      }),
    });
  });

  await page.goto("/data-entry");

  await page.getByRole("button", { name: "Add spending" }).click();
  await expect(
    page.getByText("Merchant/description is required.")
  ).toBeVisible();

  await page.getByLabel("Merchant / description").fill("Coffee Project");
  await page.getByLabel("Amount").fill("0");
  await page.getByRole("button", { name: "Add spending" }).click();
  await expect(
    page.getByText("Amount must be a non-zero number.")
  ).toBeVisible();

  await page.getByLabel("Amount").fill("12.50");
  await page.getByRole("button", { name: "Add spending" }).click();

  await expect(page.getByText("Manual spending added.")).toBeVisible();
  expect(manualPayload).toMatchObject({
    merchant: "Coffee Project",
    amount: 12.5,
  });
});

test("statement upload validates file selection and shows a mocked success state", async ({
  page,
}) => {
  let sawUploadRequest = false;

  await mockAuthenticatedSession(page);

  await page.route("**/api/upload", async (route) => {
    sawUploadRequest = true;
    await route.fulfill({
      status: 201,
      contentType: "application/json",
      body: JSON.stringify({
        file_path: "/tmp/sample.pdf",
        original_file_name: "sample.pdf",
      }),
    });
  });

  await page.goto("/data-entry");

  await page.getByRole("button", { name: "Upload statement" }).click();
  await expect(page.getByText("Select a PDF file first.")).toBeVisible();

  const fileInput = page.getByLabel("Statement PDF");
  const passwordInput = page.getByLabel("PDF password (if the file is protected)");

  await fileInput.setInputFiles(samplePdfPath);
  await expect(page.getByRole("button", { name: "sample.pdf" })).toBeVisible();

  await passwordInput.fill("secret-password");
  await expect(passwordInput).toHaveValue("secret-password");

  await page.getByRole("button", { name: "Upload statement" }).click();

  await expect(page.getByText("Uploaded sample.pdf")).toBeVisible();
  await expect(passwordInput).toHaveValue("");
  expect(sawUploadRequest).toBe(true);
});
