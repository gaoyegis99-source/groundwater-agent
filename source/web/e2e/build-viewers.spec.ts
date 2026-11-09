import { expect, test } from '@playwright/test';
import { readFileSync } from 'node:fs';
import { capabilitiesForProfile, dataDetail } from '../src/test-fixtures';

const groundwaterUi = JSON.parse(readFileSync(new URL('../../products/groundwater/ui/product-ui.json', import.meta.url), 'utf8')) as { models: Record<string, unknown> };

test('production chunks load the database and all chart types without loading charts for tables', async ({ page }) => {
  const errors: string[] = [];
  const scripts: string[] = [];
  page.on('pageerror', error => errors.push(error.message));
  page.on('console', message => { if (message.type() === 'error') errors.push(message.text()); });
  page.on('request', request => { if (request.resourceType() === 'script') scripts.push(request.url()); });
  const fields = [
    { fieldId: 'f0', name: 'month', type: 'int64' },
    { fieldId: 'f1', name: 'level', type: 'double' },
    { fieldId: 'f2', name: 'station', type: 'string' },
  ];
  const version = dataDetail({
    title: '模拟监测表', descriptor: { policy: 'FILE_QUERYABLE', rowCount: 3, fields },
    objects: [{ objectId: 'object-1', size: 64, checksum: `sha256:${'a'.repeat(64)}`, role: 'raw', format: 'csv', mediaType: 'text/csv' }],
  });
  await page.route('**/product-ui.json', route => route.fulfill({ json: groundwaterUi }));
  // Every API request is handled locally: no running candidate or Provider is used.
  await page.route('**/api/v12/**', async route => {
    const url = new URL(route.request().url());
    let body: unknown;
    if (url.pathname.endsWith('/capabilities')) body = capabilitiesForProfile(
      { productId: 'groundwater', models: groundwaterUi.models },
    );
    else if (url.pathname.endsWith('/sessions')) body = { items: [], nextCursor: null };
    else if (url.pathname.endsWith('/data/datasets')) body = {
      items: [{ datasetId: version.datasetId, project: 'demo', title: version.title, tags: [], currentVersion: version.versionId, createdAt: '2026-10-13T00:00:00Z', semantics: {}, formats: ['csv'] }], nextCursor: null, matchBasis: 'catalog',
    };
    else if (url.pathname.endsWith('/data/datasets/dataset-1/versions')) body = {
      items: [{ versionId: version.versionId, semanticId: version.semanticId, createdAt: '2026-10-13T00:00:00Z' }], nextCursor: null,
    };
    else if (url.pathname.endsWith('/data/versions/version-1')) body = version;
    else if (url.pathname.endsWith('/data/queries') && route.request().method() === 'POST') body = { queryId: 'query-1', operationId: 'operation-1' };
    else if (url.pathname.endsWith('/data/queries/query-1')) body = {
      queryId: 'query-1', state: 'SUCCEEDED', error: null, resultAvailable: true,
      expiresAt: '2026-10-13T01:00:00Z',
      receipt: { queryId: 'query-1', inputs: [], plan: {}, planDigest: `sha256:${'c'.repeat(64)}`, coverage: { sourceRows: 3, matchedRows: 3, calculation: 'all_matching_rows' }, returnedRows: 3, resultChecksum: `sha256:${'b'.repeat(64)}`, truncated: false, engine: { name: 'test' } },
      result: { fields, rows: [[1, 4, 'A'], [2, 7, 'A'], [3, 5, 'B']], nextOffset: null, truncated: false, coverage: { sourceRows: 3 }, engine: { name: 'test' } },
    };
    else throw new Error(`Unexpected build-test API request: ${url.pathname}`);
    await route.fulfill({ json: body });
  });
  await page.goto('/');
  await page.getByRole('button', { name: '数据库', exact: true }).click();
  await page.getByRole('button', { name: /模拟监测表/ }).click();
  await expect(page.locator('.table-workspace .table-scroll')).toContainText('7');
  expect(scripts.some(url => /(?:ChartView|chart-renderer)-/.test(url))).toBe(false);
  await page.getByRole('button', { name: '图表', exact: true }).click();
  await page.getByLabel('纵轴字段').selectOption('f1');
  await page.getByLabel('分组字段').selectOption('f2');
  for (const kind of ['line', 'bar', 'scatter']) {
    await page.getByLabel('图表类型').selectOption(kind);
    const canvas = page.locator('.artifact-chart canvas').first();
    await expect(canvas).toBeVisible();
    await expect.poll(() => canvas.evaluate((node: HTMLCanvasElement) => {
      const bytes = node.getContext('2d')!.getImageData(0, 0, node.width, node.height).data;
      return bytes.some((value, index) => index % 4 === 3 && value > 0);
    })).toBe(true);
  }
  expect(scripts.some(url => /ChartView-/.test(url))).toBe(true);
  expect(scripts.some(url => /chart-renderer-/.test(url))).toBe(true);
  expect(errors).toEqual([]);
});
