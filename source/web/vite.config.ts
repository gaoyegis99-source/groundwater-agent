import { defineConfig } from 'vitest/config';
import { fileURLToPath } from 'node:url';
import { frontendBuildOutput } from '../packages/ui-boundary/build-path.ts';

const sourceRoot = fileURLToPath(new URL('..', import.meta.url));

type BuildLogLevel = 'info' | 'debug' | 'warn' | 'error';
type BuildLog = { code?: string; message: string };
type BuildLogOrString = BuildLog | string;
function onLog(level: Exclude<BuildLogLevel, 'error'>, log: BuildLog, defaultHandler: (level: BuildLogLevel, log: BuildLogOrString) => void) {
  // React Query ships the Next.js-only "use client" directive. It has no
  // meaning in this browser bundle and Rolldown cannot preserve it.
  if (
    level === 'warn'
    && log.code === 'MODULE_LEVEL_DIRECTIVE'
    && log.message?.includes('@tanstack/react-query')
    && log.message.includes('"use client"')
  ) return;
  defaultHandler(level, log);
}

export default defineConfig({
  base: '/',
  // Production-build browser tests must run under the same no-eval policy as
  // the assembled Gateway. This catches dependencies that compile code at runtime.
  preview: {
    headers: {
      'Content-Security-Policy': "default-src 'self'; connect-src 'self'; img-src 'self' blob: data:; style-src 'self'; script-src 'self'; worker-src 'self' blob:; frame-ancestors 'none'; base-uri 'none'; form-action 'self'",
    },
  },
  build: {
    outDir: frontendBuildOutput(sourceRoot, 'web'),
    emptyOutDir: true,
    rolldownOptions: {
      onLog,
      output: {
        codeSplitting: {
          minSize: 10_000,
          groups: [
            { name: 'react', test: /node_modules[\\/](react|react-dom|scheduler)[\\/]/ },
            { name: 'chart-renderer', test: /node_modules[\\/]zrender[\\/]/ },
          ],
        },
      },
    },
  },
  test: {
    include: ['src/**/*.test.{ts,tsx}'],
    environment: 'jsdom',
    setupFiles: ['./src/test-setup.ts'],
    restoreMocks: true,
    maxWorkers: 4,
    testTimeout: 15_000,
    hookTimeout: 15_000,
  },
});
