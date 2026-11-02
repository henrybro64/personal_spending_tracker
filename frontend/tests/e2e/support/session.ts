import type { Page } from "@playwright/test";

export type MockProviders = {
  entra_enabled: boolean;
  local_registration_enabled: boolean;
};

// Every page asks /api/auth/providers; answer it so no test hits a missing backend.
export async function mockProviders(
  page: Page,
  providers: MockProviders = { entra_enabled: false, local_registration_enabled: true }
) {
  await page.route("**/api/auth/providers", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(providers),
    });
  });
}

export async function mockAuthenticatedSession(page: Page) {
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
