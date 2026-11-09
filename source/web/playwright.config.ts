import { defineConfig } from '@playwright/test';
import { fileURLToPath } from 'node:url';
import { validationOutput } from '../packages/ui-boundary/build-path.ts';

if (!process.env.PLATFORM_BASE_URL) {
  throw new Error('Set PLATFORM_BASE_URL to an isolated Gateway.');
}
const sourceRoot = fileURLToPath(new URL('..', import.meta.url));
const report = process.env.PLATFORM_WEBUI_REPORT ?? validationOutput(sourceRoot, 'webui-evidence');
const apiToken = process.env.PLATFORM_E2E_API_TOKEN;

export default defineConfig({
  testDir: './e2e',
  fullyParallel: false,
  workers: 1,
  retries: 0,
  timeout: 180_000,
  expect: { timeout: 45_000 },
  reporter: [['list'], ['json', { outputFile: `${report}/results.json` }]],
  outputDir: `${report}/artifacts`,
  use: {
    baseURL: process.env.PLATFORM_BASE_URL,
    browserName: 'chromium',
    viewport: { width: 1440, height: 1000 },
    screenshot: 'only-on-failure',
    trace: 'retain-on-failure',
    acceptDownloads: true,
    extraHTTPHeaders: apiToken ? { Authorization: `Bearer ${apiToken}` } : undefined,
  },
});
