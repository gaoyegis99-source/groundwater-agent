import { defineConfig } from "@playwright/test";
import { fileURLToPath } from "node:url";
import { validationOutput } from "../../../packages/ui-boundary/build-path.ts";

const sourceRoot = fileURLToPath(new URL("../../..", import.meta.url));

export default defineConfig({
  testDir: "./e2e",
  testMatch: "knowledge-ui.spec.ts",
  workers: 1,
  retries: 0,
  outputDir: validationOutput(sourceRoot, "knowledge-build-e2e"),
  use: { baseURL: "http://127.0.0.1:18114", browserName: "chromium" },
  webServer: {
    command: "npm run build && npm exec vite preview -- --host 127.0.0.1 --port 18114 --strictPort",
    url: "http://127.0.0.1:18114/knowledge/",
    reuseExistingServer: false,
  },
});
