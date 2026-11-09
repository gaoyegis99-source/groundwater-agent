import { defineConfig } from "@playwright/test";
import { fileURLToPath } from "node:url";
import { validationOutput } from "../../../packages/ui-boundary/build-path.ts";

const sourceRoot = fileURLToPath(new URL("../../..", import.meta.url));
if (!process.env.DATA_E2E_URL)
  throw new Error("Set DATA_E2E_URL to the dedicated unified Gateway deployment.");
if (!process.env.PLATFORM_E2E_API_TOKEN)
  throw new Error("Set PLATFORM_E2E_API_TOKEN to an ephemeral local API token.");
export default defineConfig({
  testDir: "./e2e",
  testIgnore: "data-ui.spec.ts",
  workers: 1,
  retries: 0,
  timeout: 90000,
  expect: { timeout: 30000 },
  reporter: [["list"]],
  outputDir: validationOutput(sourceRoot, "data-live-e2e"),
  use: {
    baseURL: process.env.DATA_E2E_URL,
    browserName: "chromium",
    acceptDownloads: true,
    screenshot: "only-on-failure",
    // Authenticated network traces can retain Authorization headers.  The
    // live gate keeps screenshots and JSON counts but never persists secrets.
    trace: "off",
    viewport: { width: 1350, height: 1000 },
    extraHTTPHeaders: {
      Authorization: `Bearer ${process.env.PLATFORM_E2E_API_TOKEN}`,
    },
  },
});
