import { defineConfig } from '@playwright/test';
import { fileURLToPath } from 'node:url';
import { validationOutput } from '../packages/ui-boundary/build-path.ts';

const sourceRoot = fileURLToPath(new URL('..', import.meta.url));
const output = validationOutput(sourceRoot, 'web-build-e2e');

export default defineConfig({
  testDir: './e2e',
  testMatch: ['build-viewers.spec.ts', 'build-result-formats.spec.ts', 'build-air-quality-product.spec.ts', 'application-accessibility.spec.ts', 'conversation-polish.spec.ts', 'build-workflow-tasks.spec.ts', 'build-turn-timeline.spec.ts', 'build-task-overview.spec.ts'],
  workers: 1,
  retries: 0,
  timeout: 30_000,
  globalSetup: './e2e/build-global-setup.ts',
  outputDir: `${output}/artifacts`,
  reporter: [['list'], ['json', { outputFile: `${output}/results.json` }]],
  use: { baseURL: 'http://127.0.0.1:28141', browserName: 'chromium' },
  webServer: {
    command: 'npm run build && npx --no-install vite preview --host 127.0.0.1 --port 28141 --strictPort',
    timeout: 120_000,
    url: 'http://127.0.0.1:28141',
    reuseExistingServer: false,
  },
});
