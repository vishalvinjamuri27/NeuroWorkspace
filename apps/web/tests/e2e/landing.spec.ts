import { expect, test } from "@playwright/test";

const API_ORIGIN = "http://localhost:8100";

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

test("public sample supports the guided viewer path", async ({ page }) => {
  await page.goto("/");
  await page.getByRole("button", { name: /Try a sample EEG/ }).click();
  await expect(page).toHaveURL(/\/recordings\//);
  await expect(
    page.getByRole("complementary", { name: "Sample recording guide" }),
  ).toBeVisible();

  const canvas = page.locator("canvas[aria-label*='signal channels']");
  await expect(canvas).toBeVisible();
  await page.getByTitle("Zoom in (+)").click();
  await page.getByTitle("Pan right (Right arrow)").click();
  const firstChannel = page.locator(".channel-row input[type='checkbox']").first();
  await firstChannel.uncheck();
  await expect(firstChannel).not.toBeChecked();
  await page.getByRole("tab", { name: /QC review/ }).click();
  await expect(page.getByText(/not medical or diagnostic/i)).toBeVisible();
  await expect(page.getByTitle("The public sample is read-only")).toBeDisabled();
});

test("CSV import supports review, annotation lifecycle, QC, and export", async ({
  page,
}) => {
  await page.goto("/upload");
  await page.getByRole("button", { name: "Continue locally" }).click();
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

  await page.goto("/workspace");
  const savedRecording = page.getByRole("link", { name: /playwright-eeg/i });
  await expect(savedRecording).toBeVisible();
  await savedRecording.click();
  await expect(page.locator("canvas[aria-label*='signal channels']")).toBeVisible();
});

test("import failures show a safe reference and recovery path", async ({ page }) => {
  await page.goto("/upload");
  await page.getByRole("button", { name: "Continue locally" }).click();
  await page.getByRole("button", { name: /Delimited text/ }).click();
  await page.locator('input[type="file"]').setInputFiles({
    name: "invalid-config.csv",
    mimeType: "text/csv",
    buffer: Buffer.from("time,C3,C4\n0,1,2\n"),
  });
  await page.getByRole("button", { name: "Review import" }).click();
  await page.getByLabel("Sampling frequency (Hz)").fill("0");
  await page.getByRole("button", { name: "Validate and upload" }).click();

  await expect(page.getByText("Import stopped safely")).toBeVisible();
  await expect(page.getByText(/Error reference:/)).toBeVisible();
  await expect(page.getByRole("button", { name: "Return to files" })).toBeVisible();
});

test("workspace authorization hides another user's project", async ({
  playwright,
}) => {
  const first = await playwright.request.newContext({
    baseURL: API_ORIGIN,
    extraHTTPHeaders: { Origin: "http://localhost:3100" },
  });
  const second = await playwright.request.newContext({
    baseURL: API_ORIGIN,
    extraHTTPHeaders: { Origin: "http://localhost:3100" },
  });
  try {
    await expect(
      (
        await first.post("/api/session/dev", {
          data: { email: "alpha-one@example.test", display_name: "Alpha One" },
        })
      ).status(),
    ).toBe(200);
    const workspaceResponse = await first.post("/api/workspaces", {
      data: { name: "Private alpha workspace" },
    });
    expect(workspaceResponse.status()).toBe(201);
    const workspace = (await workspaceResponse.json()) as { id: string };

    await second.post("/api/session/dev", {
      data: { email: "alpha-two@example.test", display_name: "Alpha Two" },
    });
    const hidden = await second.get(`/api/workspaces/${workspace.id}/projects`);
    expect(hidden.status()).toBe(404);
    expect(await hidden.json()).toMatchObject({
      error: { code: "not_found" },
    });
  } finally {
    await first.dispose();
    await second.dispose();
  }
});
