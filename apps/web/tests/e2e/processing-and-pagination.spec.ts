import { expect, test } from "@playwright/test";

const API_ORIGIN = process.env.PLAYWRIGHT_API_ORIGIN ?? "http://localhost:8100";

async function uploadCsvRecording(page: import("@playwright/test").Page) {
  await page.goto("/upload");
  await page.getByRole("button", { name: "Continue locally" }).click();
  await page.getByRole("button", { name: /Delimited text/ }).click();
  await page.locator('input[type="file"]').setInputFiles({
    name: "pagination-check.csv",
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
  const recordingId = new URL(page.url()).pathname.split("/").pop();
  if (!recordingId) throw new Error("could not read recording id from URL");
  return recordingId;
}

test("processing an average reference produces a derived version the viewer can show", async ({
  page,
}) => {
  await uploadCsvRecording(page);
  const canvas = page.locator("canvas[aria-label*='signal channels']");
  await expect(canvas).toBeVisible();

  await page.getByRole("tab", { name: /Processing/ }).click();
  await expect(
    page.getByText(/No processing runs yet for this recording/i),
  ).toBeVisible();

  await page.getByRole("button", { name: /Run average reference/ }).click();
  // The processing panel's own job status view must reach a terminal,
  // legible state -- not leave the user staring at "Queued" forever, and
  // not require them to guess whether the click did anything.
  await expect(page.locator(".job-status-pill")).toHaveText(/Succeeded/i, {
    timeout: 30_000,
  });

  // The banner is the explicit, one-click path to "navigate to the derived
  // version" -- the recording is not silently swapped out from under the
  // user without them asking for it.
  const banner = page.getByText(/Processing finished/i);
  await expect(banner).toBeVisible();
  await page.getByRole("button", { name: "View updated recording" }).click();
  await expect(canvas).toBeVisible();

  await page.getByRole("tab", { name: /Processing/ }).click();
  await expect(page.getByText("average_reference")).toBeVisible();
  await expect(page.locator(".processing-run-row .status-succeeded")).toBeVisible();
});

test("annotations Load more follows next_cursor without duplicating or losing items", async ({
  page,
}) => {
  test.setTimeout(90_000);
  const recordingId = await uploadCsvRecording(page);

  // Seed enough annotations to exceed the viewer's first page (200) --
  // done directly against the API (no ingestion involved, so this is fast)
  // rather than through 201 UI interactions.
  const total = 201;
  for (let index = 0; index < total; index += 1) {
    const response = await page.request.post(
      `${API_ORIGIN}/api/recordings/${recordingId}/annotations`,
      {
        data: {
          onset_seconds: Number((index * 0.01).toFixed(3)),
          duration_seconds: 0.001,
          label: `seed-${index}`,
          description: "",
        },
      },
    );
    expect(response.status()).toBe(201);
  }

  await page.reload();
  await page.getByRole("tab", { name: /Annotations/ }).click();
  await expect(page.getByText("seed-0")).toBeVisible();

  const loadMore = page.getByRole("button", { name: /load more annotations/i });
  await expect(loadMore).toBeVisible();
  // The 201st item is on the second page and must not be visible yet.
  await expect(page.getByText(`seed-${total - 1}`)).not.toBeVisible();

  await loadMore.click();
  await expect(page.getByText(`seed-${total - 1}`)).toBeVisible();
  // The server reported no further page: the affordance must not linger
  // and invite a request that would return nothing.
  await expect(loadMore).not.toBeVisible();

  // No duplicates: every seeded label appears exactly once.
  const firstLabelCount = await page.getByText("seed-0", { exact: true }).count();
  expect(firstLabelCount).toBe(1);
});
