import { expect, test } from "@playwright/test";

test("landing page explains scope and offers both evaluation paths", async ({
  page,
}) => {
  const consoleErrors: string[] = [];
  page.on("console", (message) => {
    if (message.type() === "error") consoleErrors.push(message.text());
  });
  await page.goto("/");
  await page.waitForLoadState("networkidle");
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
  expect(consoleErrors).toEqual([]);
});

test("CSV import supports review, annotation lifecycle, QC, and export", async ({
  page,
}) => {
  await page.goto("/upload");
  await page.getByRole("button", { name: "Continue" }).click();
  await page.getByRole("button", { name: /Delimited text/ }).click();
  await page.locator('input[type="file"]').setInputFiles({
    name: "playwright-eeg.csv",
    mimeType: "text/csv",
    buffer: Buffer.from(
      "time,C3,C4\n" +
        Array.from(
          { length: 5_120 },
          (_, index) => `${index / 256},${index},${-index}`,
        ).join("\n"),
    ),
  });
  await page.getByRole("button", { name: "Review import" }).click();
  await page.getByLabel("Time column (optional)").fill("time");
  await page.getByRole("button", { name: "Validate and upload" }).click();
  await expect(page).toHaveURL(/\/recordings\//, { timeout: 45_000 });

  const canvas = page.locator("canvas[aria-label*='signal channels']");
  await expect(canvas).toBeVisible();
  await expect(canvas).toHaveAttribute("aria-label", /2 signal channels/);
  await page.getByTitle("Zoom in (+)").click();
  await expect(canvas).toHaveAttribute("aria-label", /1\.75 to 8\.25 seconds/);
  await page.getByTitle("Pan right (Right arrow)").click();

  await page.getByRole("button", { name: /Annotate/ }).click();
  const bounds = await canvas.boundingBox();
  if (!bounds) throw new Error("waveform canvas has no bounds");
  await page.mouse.move(
    bounds.x + bounds.width * 0.3,
    bounds.y + bounds.height * 0.5,
  );
  await page.mouse.down();
  await page.mouse.move(
    bounds.x + bounds.width * 0.6,
    bounds.y + bounds.height * 0.5,
  );
  await page.mouse.up();
  await page
    .getByRole("dialog", { name: "Create annotation" })
    .getByLabel("Label")
    .fill("Review region");
  await page.getByRole("button", { name: "Save annotation" }).click();

  await page.getByRole("tab", { name: /Annotations/ }).click();
  await expect(page.getByText("Review region")).toBeVisible();
  await page.getByRole("button", { name: "Edit" }).click();
  const editor = page.getByRole("dialog", { name: "Edit annotation" });
  await editor.getByLabel("Label").fill("Reviewed region");
  await editor.getByRole("button", { name: "Save changes" }).click();
  await expect(page.getByText("Reviewed region")).toBeVisible();

  page.once("dialog", (dialog) => dialog.accept());
  await page.getByRole("button", { name: "Delete" }).click();
  await expect(page.getByText(/create the first annotation/i)).toBeVisible();

  await page.locator(".bad-toggle").first().click();
  await expect(page.locator(".bad-toggle").first()).toHaveAttribute(
    "aria-pressed",
    "true",
  );
  await page.getByRole("tab", { name: /QC review/ }).click();
  await expect(page.getByText(/not medical or diagnostic/i)).toBeVisible();

  await page.getByLabel("Export format").selectOption("provenance_json");
  const downloadPromise = page.waitForEvent("download");
  await page.getByRole("button", { name: "Export" }).click();
  const download = await downloadPromise;
  expect(download.suggestedFilename()).toBe("neuroworkspace-export.json");
});
