import { defineConfig } from "@playwright/test";
import { fileURLToPath } from "node:url";
import { validationOutput } from "../../../packages/ui-boundary/build-path.ts";

const sourceRoot = fileURLToPath(new URL("../../..", import.meta.url));

const baseURL = process.env.KNOWLEDGE_UI_URL;
if (!baseURL) throw new Error("Set KNOWLEDGE_UI_URL to the dedicated unified Gateway deployment.");
if (!process.env.PLATFORM_E2E_API_TOKEN)
  throw new Error("Set PLATFORM_E2E_API_TOKEN to an ephemeral local API token.");

export default defineConfig({
  testDir: "e2e",
  testIgnore: "knowledge-ui.spec.ts",
  workers: 1,
  retries: 0,
  timeout: 60_000,
  outputDir: validationOutput(sourceRoot, "knowledge-live-e2e"),
  use: {
    baseURL,
    browserName: "chromium",
    headless: true,
    // Do not serialize the ephemeral API token into a retained trace archive.
    trace: "off",
    viewport: { width: 1366, height: 768 },
    extraHTTPHeaders: {
      Authorization: `Bearer ${process.env.PLATFORM_E2E_API_TOKEN}`,
    },
  },
});
