import { expect, test, type Page } from "@playwright/test";

async function mockSignedOutSession(page: Page) {
  await page.route("**/api/auth/session", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        authenticated: false,
        user: null,
        claimed_legacy_transactions: 0,
        claimed_legacy_uploads: 0,
      }),
    });
  });
}

async function submitSignIn(page: Page) {
  await page.goto("/");
  await page.getByLabel("Username").fill("tester@example.com");
  await page.getByLabel("Password").fill("wrong-or-right");
  await page.getByRole("button", { name: "Sign in" }).click();
}

for (const status of [502, 503, 504]) {
  test(`sign-in shows server unavailable for HTTP ${status}`, async ({ page }) => {
    await mockSignedOutSession(page);
    await page.route("**/api/auth/login", async (route) => {
      await route.fulfill({
        status,
        contentType: "application/json",
        body: JSON.stringify({
          detail: "API proxy to backend failed: ECONNREFUSED",
        }),
      });
    });

    await submitSignIn(page);

    await expect(page.getByText("Server unavailable")).toBeVisible();
    await expect(
      page.getByText("The app server is not responding. Try again in a moment.")
    ).toBeVisible();
    await expect(page.getByText("Authentication error")).toHaveCount(0);
    await expect(page.getByText("ECONNREFUSED")).toHaveCount(0);
  });
}

test("sign-in keeps authentication error for a wrong password", async ({ page }) => {
  await mockSignedOutSession(page);
  await page.route("**/api/auth/login", async (route) => {
    await route.fulfill({
      status: 401,
      contentType: "application/json",
      body: JSON.stringify({ detail: "Invalid username or password." }),
    });
  });

  await submitSignIn(page);

  await expect(page.getByText("Authentication error")).toBeVisible();
  await expect(page.getByText("Invalid username or password.")).toBeVisible();
  await expect(page.getByText("Server unavailable")).toHaveCount(0);
});
