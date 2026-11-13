import { spawnSync } from 'node:child_process';
import { resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { validationOutput } from '../../packages/ui-boundary/build-path.ts';

const sourceRoot = fileURLToPath(new URL('../..', import.meta.url));

export default function buildProductFixtures() {
  const result = spawnSync(
    'python',
    [
      'tests/e2e/build_air_quality_web_fixture.py',
      '--output',
      validationOutput(sourceRoot, 'air-quality-fixture'),
    ],
    { cwd: resolve('..'), encoding: 'utf8' },
  );
  if (result.status !== 0) {
    throw new Error(`AIR_QUALITY_FIXTURE_BUILD_FAILED\n${result.stdout}\n${result.stderr}`);
  }
}
