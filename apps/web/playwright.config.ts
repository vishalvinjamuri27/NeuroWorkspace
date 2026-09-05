import { defineConfig, devices } from "@playwright/test";
import { mkdtempSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

const e2eRoot = mkdtempSync(join(tmpdir(), "neuroworkspace-e2e-"));
const databaseUrl = `sqlite:///${join(e2eRoot, "e2e.db")}`;
const objectRoot = join(e2eRoot, "objects");
const externalBaseURL = process.env.PLAYWRIGHT_BASE_URL?.trim();

export default defineConfig({
  testDir: "./tests/e2e",
  timeout: 60_000,
  use: {
    baseURL: externalBaseURL ?? "http://localhost:3100",
    trace: "retain-on-failure",
  },
  webServer: externalBaseURL
    ? undefined
    : [
        {
          // Explicitly pin every setting this ephemeral backend depends on,
          // rather than only the ones that differ from defaults: the repo
          // root's .env (committed for `docker compose`, pointing at
          // Postgres/MinIO/Redis) is picked up by pydantic-settings for any
          // process run from the repo root, and an env var set here always
          // wins over .env -- so a setting left unset here silently falls
          // through to the Compose stack's values instead of this test's
          // isolated sqlite/local-filesystem sandbox.
          command:
            `cd ../.. && DATABASE_URL=${databaseUrl} OBJECT_STORE_BACKEND=local OBJECT_STORE_ROOT=${objectRoot} WORKER_EAGER=true API_PUBLIC_URL=http://localhost:8100 CORS_ORIGINS=http://localhost:3100 uv run python scripts/seed_sample.py && ` +
            `DATABASE_URL=${databaseUrl} OBJECT_STORE_BACKEND=local OBJECT_STORE_ROOT=${objectRoot} WORKER_EAGER=true API_PUBLIC_URL=http://localhost:8100 CORS_ORIGINS=http://localhost:3100 uv run uvicorn neuro_api.main:app --port 8100`,
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
