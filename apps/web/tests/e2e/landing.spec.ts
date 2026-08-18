import { expect, test } from "@playwright/test";

test("landing page explains scope and offers both evaluation paths", async ({
  page,
}) => {
  await page.goto("/");
  await expect(
    page.getByRole("heading", { name: /Inspect neural signals/ }),
  ).toBeVisible();
  await expect(
    page.getByRole("button", { name: /Try a sample EEG/ }),
  ).toBeVisible();
  await expect(
    page.getByRole("link", { name: /Import a recording/ }),
  ).toBeVisible();
  await expect(page.getByText(/not a medical device/i)).toBeVisible();
});
