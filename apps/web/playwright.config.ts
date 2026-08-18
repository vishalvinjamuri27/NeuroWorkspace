import { defineConfig, devices } from "@playwright/test";
import { mkdtempSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

const e2eRoot = mkdtempSync(join(tmpdir(), "neuroworkspace-e2e-"));
const databaseUrl = `sqlite:///${join(e2eRoot, "e2e.db")}`;
const objectRoot = join(e2eRoot, "objects");

export default defineConfig({
  testDir: "./tests/e2e",
  timeout: 60_000,
  use: { baseURL: "http://localhost:3100", trace: "retain-on-failure" },
  webServer: [
    {
      command:
        `cd ../.. && DATABASE_URL=${databaseUrl} OBJECT_STORE_ROOT=${objectRoot} WORKER_EAGER=true API_PUBLIC_URL=http://localhost:8100 CORS_ORIGINS=http://localhost:3100 uv run python scripts/seed_sample.py && ` +
        `DATABASE_URL=${databaseUrl} OBJECT_STORE_ROOT=${objectRoot} WORKER_EAGER=true API_PUBLIC_URL=http://localhost:8100 CORS_ORIGINS=http://localhost:3100 uv run uvicorn neuro_api.main:app --port 8100`,
      url: "http://localhost:8100/health/ready",
      reuseExistingServer: false,
      timeout: 120_000,
    },
    {
      command:
        "NEXT_PUBLIC_API_URL=http://localhost:8100/api pnpm dev --port 3100",
      url: "http://localhost:3100",
      reuseExistingServer: false,
      timeout: 120_000,
    },
  ],
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
});
