import { chromium } from "@playwright/test";
import { copyFile, mkdir, mkdtemp } from "node:fs/promises";
import { tmpdir } from "node:os";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const repositoryRoot = resolve(here, "../../..");
const outputRoot = join(repositoryRoot, "docs/assets");
const videoRoot = await mkdtemp(join(tmpdir(), "neuroworkspace-media-"));
const baseURL = process.env.NEUROWORKSPACE_MEDIA_URL ?? "http://localhost:3000";
await mkdir(outputRoot, { recursive: true });

const browser = await chromium.launch({
  args: ["--enable-precise-memory-info"],
});
const context = await browser.newContext({
  viewport: { width: 1440, height: 1000 },
  recordVideo: { dir: videoRoot, size: { width: 1440, height: 1000 } },
});
const page = await context.newPage();
const video = page.video();

try {
  await page.goto(baseURL);
  await page.getByRole("button", { name: /Try a sample EEG/ }).click();
  await page.locator("canvas[aria-label*='signal channels']").waitFor();
  await page.getByTitle("Zoom in (+)").click();
  await page.getByTitle("Pan right (Right arrow)").click();
  await page.locator(".channel-row input[type='checkbox']").first().uncheck();
  await page.getByRole("tab", { name: /QC review/ }).click();
  await page.screenshot({
    path: join(outputRoot, "neuroworkspace-recording-workspace.png"),
    fullPage: true,
  });

  // The shared public sample is intentionally read-only. Continue with a small
  // user-owned fixture so the recording never appears to belong to the viewer.
  await page.goto(baseURL + "/upload");
  const localAccess = page.getByRole("button", { name: "Continue locally" });
  if (await localAccess.isVisible()) await localAccess.click();
  await page.getByRole("button", { name: /Delimited text/ }).click();
  const rows = Array.from(
    { length: 5_120 },
    (_, index) =>
      String(index / 256) + "," + String(index) + "," + String(-index),
  );
  await page.locator('input[type="file"]').setInputFiles({
    name: "media-eeg.csv",
    mimeType: "text/csv",
    buffer: Buffer.from("time,C3,C4\n" + rows.join("\n")),
  });
  await page.getByRole("button", { name: "Review import" }).click();
  await page.getByLabel("Time column (optional)").fill("time");
  await page.getByRole("button", { name: "Validate and upload" }).click();
  await page.waitForURL(/\/recordings\//, { timeout: 45_000 });

  const canvas = page.locator("canvas[aria-label*='signal channels']");
  const bounds = await canvas.boundingBox();
  if (!bounds) throw new Error("waveform canvas did not render");
  await page.getByRole("button", { name: /Annotate/ }).click();
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
  await page.getByRole("tab", { name: /QC review/ }).click();
} finally {
  await context.close();
  await browser.close();
}

if (!video) throw new Error("browser video capture was not available");
await copyFile(
  await video.path(),
  join(outputRoot, "neuroworkspace-alpha-demo.webm"),
);
console.log("Product screenshot and demo video written to docs/assets.");
