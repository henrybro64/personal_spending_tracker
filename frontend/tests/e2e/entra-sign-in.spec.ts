import { expect, test, type Page } from "@playwright/test";

import { mockAuthenticatedSession, mockProviders } from "./support/session";

const OBJECT_ID = "bbbbbbbb-1111-2222-3333-444444444444";

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

test("Microsoft sign-in starts at the backend with a safe return path", async ({ page }) => {
  await mockSignedOutSession(page);
  await mockProviders(page, { entra_enabled: true, local_registration_enabled: true });
  let startedAt: URL | null = null;
  await page.route("**/api/auth/entra/login**", async (route) => {
    startedAt = new URL(route.request().url());
    await route.fulfill({ status: 200, contentType: "text/html", body: "<p>idp</p>" });
  });

  await page.goto("/transactions?range=month");
  await page.getByRole("button", { name: "Sign in with Microsoft" }).click();

  await expect(page.getByText("idp")).toBeVisible();
  expect(startedAt).not.toBeNull();
  expect(startedAt!.pathname).toBe("/api/auth/entra/login");
  expect(startedAt!.searchParams.get("return_to")).toBe("/transactions?range=month");
});

test("the Microsoft button is hidden when Entra is off", async ({ page }) => {
  await mockSignedOutSession(page);
  await mockProviders(page, { entra_enabled: false, local_registration_enabled: true });
  await page.goto("/");
  await expect(page.getByRole("button", { name: "Sign in", exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "Sign in with Microsoft" })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Create account" })).toBeVisible();
});

test("Create account is hidden when local registration is off", async ({ page }) => {
  await mockSignedOutSession(page);
  await mockProviders(page, { entra_enabled: true, local_registration_enabled: false });
  await page.goto("/");
  await expect(page.getByRole("button", { name: "Sign in with Microsoft" })).toBeVisible();
  await expect(page.getByRole("button", { name: "Create account" })).toHaveCount(0);
});

test("a refused Microsoft account sees its object id and the URL is cleaned", async ({
  page,
}) => {
  await mockSignedOutSession(page);
  await mockProviders(page, { entra_enabled: true, local_registration_enabled: false });
  await page.goto(`/?auth_error=entra_not_allowed&entra_object_id=${OBJECT_ID}`);

  await expect(page.getByText("Microsoft account not allowed")).toBeVisible();
  await expect(page.getByText(`allow object ID ${OBJECT_ID}`)).toBeVisible();
  await expect(page).toHaveURL(/\/$/);
});

test("text that is not an object id is never shown from the URL", async ({ page }) => {
  await mockSignedOutSession(page);
  await mockProviders(page, { entra_enabled: true, local_registration_enabled: true });
  await page.goto(
    "/?auth_error=entra_not_allowed&entra_object_id=" +
      encodeURIComponent("Call 555-0100 to unlock your account")
  );
  await expect(page.getByText("Microsoft account not allowed")).toBeVisible();
  await expect(page.getByText("555-0100")).toHaveCount(0);
});

test("an unknown error code falls back to a generic failure", async ({ page }) => {
  await mockSignedOutSession(page);
  await mockProviders(page, { entra_enabled: true, local_registration_enabled: true });
  await page.goto("/?auth_error=" + encodeURIComponent("<b>pwned</b>"));
  await expect(page.getByText("Microsoft sign-in failed")).toBeVisible();
  await expect(page.getByText("pwned")).toHaveCount(0);
});

test("signing out of a Microsoft account also opens Microsoft sign-out", async ({ page }) => {
  await mockAuthenticatedSession(page);
  await page.route("**/api/ai/settings", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ selected_provider: null, privacy_notice: "", providers: [] }),
    });
  });
  await page.route("**/api/auth/logout", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        authenticated: false,
        user: null,
        claimed_legacy_transactions: 0,
        claimed_legacy_uploads: 0,
        logout_url: "https://login.example.test/logout?client_id=abc",
      }),
    });
  });
  await page.route("https://login.example.test/**", async (route) => {
    await route.fulfill({ status: 200, contentType: "text/html", body: "<p>signed out</p>" });
  });

  await page.goto("/settings");
  await page.getByRole("button", { name: "Sign out" }).click();
  await expect(page).toHaveURL("https://login.example.test/logout?client_id=abc");
});
