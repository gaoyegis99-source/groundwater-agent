import { defineConfig } from '@playwright/test';
import { fileURLToPath } from 'node:url';
import { validationOutput } from '../packages/ui-boundary/build-path.ts';

const sourceRoot = fileURLToPath(new URL('..', import.meta.url));
const output = validationOutput(sourceRoot, 'web-build-e2e');

export default defineConfig({
  testDir: './e2e',
  testMatch: ['build-viewers.spec.ts', 'build-air-quality-product.spec.ts', 'application-accessibility.spec.ts'],
  workers: 1,
  retries: 0,
  timeout: 30_000,
  globalSetup: './e2e/build-global-setup.ts',
  outputDir: `${output}/artifacts`,
  reporter: [['list'], ['json', { outputFile: `${output}/results.json` }]],
  use: { baseURL: 'http://127.0.0.1:28141', browserName: 'chromium' },
  webServer: {
    command: 'npx --no-install vite preview --host 127.0.0.1 --port 28141 --strictPort',
    url: 'http://127.0.0.1:28141',
    reuseExistingServer: false,
  },
});
