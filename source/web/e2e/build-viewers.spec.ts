import { createHash } from 'node:crypto';
import { expect, test } from '@playwright/test';
import { readFileSync } from 'node:fs';
import { capabilitiesForProfile, dataDetail, dataRef, historyFixture, sessionResultsFixture, taskFixture } from '../src/test-fixtures';

const groundwaterUi = JSON.parse(readFileSync(new URL('../../products/groundwater/ui/product-ui.json', import.meta.url), 'utf8')) as { models: Record<string, unknown> };

test('production GeoJSON renders 150,000 features with the full color range', async ({ page, browser }, testInfo) => {
  test.setTimeout(60_000);
  await page.setViewportSize({ width: 1440, height: 1000 });
  const featureCount = 150_000;
  const geojson = JSON.stringify({
    type: 'FeatureCollection',
    features: Array.from({ length: featureCount }, (_, index) => ({
      type: 'Feature',
      geometry: { type: 'Point', coordinates: [100 + (index % 500) * 0.00001, 30 + Math.floor(index / 500) * 0.00001] },
      properties: { reading: index },
    })),
  });
  const sizeBytes = Buffer.byteLength(geojson);
  const version = dataDetail({
    title: '大型 GeoJSON 样本',
    objects: [{
      objectId: 'object-1', size: sizeBytes, checksum: 'sha256:' + createHash('sha256').update(geojson).digest('hex'),
      role: 'raw', format: 'geojson', mediaType: 'application/geo+json',
    }],
  });
  const pageErrors: string[] = [];
  page.on('pageerror', error => pageErrors.push(error.message));
  await page.route('**/product-ui.json', route => route.fulfill({ json: groundwaterUi }));
  await page.route('**/product-icon.svg', route => route.fulfill({
    contentType: 'image/svg+xml',
    body: readFileSync(new URL('../../products/groundwater/ui/product-icon.svg', import.meta.url), 'utf8'),
  }));
  await page.route('**/api/v12/**', route => {
    const path = new URL(route.request().url()).pathname;
    if (path.endsWith('/capabilities')) return route.fulfill({ json: capabilitiesForProfile({ productId: 'groundwater', models: groundwaterUi.models }) });
    if (path.endsWith('/sessions')) return route.fulfill({ json: { items: [], nextCursor: null } });
    if (path.endsWith('/data/datasets')) return route.fulfill({ json: {
      items: [{ datasetId: version.datasetId, project: 'demo', title: version.title, tags: [], currentVersion: version.versionId,
        createdAt: version.createdAt, semantics: {}, formats: ['geojson'] }], nextCursor: null, matchBasis: 'catalog',
    } });
    if (path.endsWith('/data/datasets/dataset-1/versions')) return route.fulfill({ json: {
      items: [{ versionId: version.versionId, semanticId: version.semanticId, createdAt: version.createdAt }], nextCursor: null,
    } });
    if (path.endsWith('/data/versions/version-1')) return route.fulfill({ json: version });
    if (path.endsWith('/objects/object-1/content')) return route.fulfill({ contentType: 'application/geo+json', body: geojson });
    throw new Error('Unexpected GeoJSON benchmark request: ' + path);
  });
  await page.goto('/');
  await page.getByRole('button', { name: '数据库', exact: true }).click();
  const database = page.getByTestId('database-workbench');
  const dataset = database.getByRole('button', { name: /大型 GeoJSON 样本/ });
  await expect(dataset).toBeVisible();
  const started = performance.now();
  await dataset.click();
  await expect(database.locator('.map-legend')).toHaveText('0 – 149999', { timeout: 30_000 });
  await expect(database.locator('.artifact-map canvas').first()).toBeVisible({ timeout: 30_000 });
  const metrics = {
    featureCount, sizeBytes, browser: browser.version(),
    datasetClickToLegendAndCanvasMs: Math.round(performance.now() - started),
  };
  expect(pageErrors).toEqual([]);
  await testInfo.attach('geojson-performance', { body: JSON.stringify(metrics), contentType: 'application/json' });
  console.log('GEOJSON_PERF', JSON.stringify(metrics));
});

test('production GeoTIFF maps render and probe the centre pixel in results and the database', async ({ page }, testInfo) => {
  await page.setViewportSize({ width: 1440, height: 1000 });
  const raster = readFileSync(new URL('../../packages/operator-pack/catalog/fixtures/data/groundwater-head-plane.tif', import.meta.url));
  const checksum = `sha256:${createHash('sha256').update(raster).digest('hex')}`;
  const version = dataDetail({
    title: '测试水头栅格',
    semanticId: 'semantics-current',
    objects: [{ objectId: 'object-1', size: raster.length, checksum, role: 'raw', format: 'geotiff', mediaType: 'image/tiff' }],
  });
  const output = dataRef({ outputName: 'kriging-prediction', format: 'geotiff', mediaType: 'image/tiff', sizeBytes: raster.length, checksum });
  const comparisonVersion = dataDetail({ versionId: 'version-2', semanticId: 'semantics-2-updated', semantics: { description: '选择之后写入的语义' } });
  const task = taskFixture({ modelId: 'groundwater.kriging', status: 'SUCCEEDED', stage: null, outputCount: 1 });
  let resultsDelay: Promise<void> | undefined;
  await page.route('**/product-ui.json', route => route.fulfill({ json: groundwaterUi }));
  await page.route('**/product-icon.svg', route => route.fulfill({
    contentType: 'image/svg+xml',
    body: readFileSync(new URL('../../products/groundwater/ui/product-icon.svg', import.meta.url), 'utf8'),
  }));
  await page.route('**/api/v12/**', async route => {
    const path = new URL(route.request().url()).pathname;
    if (path.endsWith('/capabilities')) return route.fulfill({ json: capabilitiesForProfile({ productId: 'groundwater', models: groundwaterUi.models }) });
    if (path.endsWith('/sessions')) return route.fulfill({ json: { items: [], nextCursor: null } });
    if (path.endsWith('/messages')) return route.fulfill({ json: historyFixture({
      items: [{ messageId: 1, goalRevision: 1, role: 'user', content: '查看水头栅格' }],
      turns: [{ executionTasks: [task] }],
    }) });
    if (path.endsWith('/results')) {
      await resultsDelay;
      return route.fulfill({ json: sessionResultsFixture([{
        goalRevision: 1, agentRunId: task.agentRunId, toolCallId: task.toolCallId, modelId: task.modelId,
        status: 'COMPLETE', workflowRunId: task.workflowRunId, remoteOutputs: [output],
      }]) });
    }
    if (path.endsWith('/events') || path.endsWith('/preview')) return route.fulfill({ contentType: 'text/event-stream', body: ': ready\n\n' });
    if (path.endsWith('/data/datasets')) return route.fulfill({ json: {
      items: [{ datasetId: version.datasetId, project: 'demo', title: version.title, tags: [], currentVersion: version.versionId,
        createdAt: version.createdAt, semantics: {}, formats: ['geotiff'] }], nextCursor: null, matchBasis: 'catalog',
    } });
    if (path.endsWith('/data/datasets/dataset-1/versions')) return route.fulfill({ json: {
      items: [
        { versionId: version.versionId, semanticId: version.semanticId, createdAt: version.createdAt },
        { versionId: comparisonVersion.versionId, semanticId: 'semantics-2-listed', createdAt: comparisonVersion.createdAt },
      ], nextCursor: null,
    } });
    if (path.endsWith('/data/versions/version-1')) return route.fulfill({ json: version });
    if (path.endsWith('/data/versions/version-2')) return route.fulfill({ json: comparisonVersion });
    if (path.endsWith('/data/versions/version-2/semantics/semantics-2-listed')) return route.fulfill({ json: {
      versionId: comparisonVersion.versionId, semanticId: 'semantics-2-listed', body: { description: '选中时的语义' },
      actor: {}, createdAt: comparisonVersion.createdAt,
    } });
    if (path.endsWith('/data/versions/version-1/semantics/semantics-1')) return route.fulfill({ json: {
      versionId: version.versionId, semanticId: output.semanticId, body: { description: '发布时语义' },
      actor: {}, createdAt: version.createdAt,
    } });
    if (path.endsWith('/objects/object-1/content')) {
      const range = /^bytes=(\d+)-(\d+)$/.exec(route.request().headers().range ?? '');
      if (!range) return route.fulfill({ contentType: 'image/tiff', body: raster });
      const start = Number(range[1]);
      const end = Math.min(Number(range[2]), raster.length - 1);
      return route.fulfill({ status: 206, contentType: 'image/tiff', body: raster.subarray(start, end + 1),
        headers: { 'content-range': `bytes ${start}-${end}/${raster.length}` } });
    }
    throw new Error(`Unexpected raster test request: ${path}`);
  });
  await page.goto('/#/sessions/session-1');
  const readFrozenSemantics = page.waitForRequest('**/data/versions/version-1/semantics/semantics-1');
  await page.getByTestId('result-card').getByRole('button', { name: '克里金预测栅格' }).click();
  await readFrozenSemantics;
  const resultMap = page.getByTestId('workspace-panel');
  const probeMap = async (panel: typeof resultMap) => {
    const probe = panel.getByLabel('栅格探针');
    await expect(probe).toHaveText('单击地图探查物理值');
    const viewport = panel.locator('.artifact-map .ol-viewport');
    await expect(viewport).toBeVisible();
    await viewport.click();
    await expect(probe).toHaveText(/像元 \[2, 2\].*物理值 96/);
  };
  await probeMap(resultMap);
  await page.screenshot({ path: testInfo.outputPath('result-raster-probe.png') });

  let releaseResults!: () => void;
  resultsDelay = new Promise(resolve => { releaseResults = resolve; });
  try {
    const pendingResults = page.waitForRequest(request => new URL(request.url()).pathname.endsWith('/results'));
    await page.reload();
    await pendingResults;
    await expect(resultMap.getByText(output.semanticId, { exact: true })).toBeVisible();
    await expect(resultMap.getByText(version.semanticId, { exact: true })).toHaveCount(0);
    await probeMap(resultMap);
    await page.screenshot({ path: testInfo.outputPath('restored-exact-result.png') });
  } finally {
    releaseResults();
  }

  await page.getByRole('button', { name: '数据库', exact: true }).click();
  const database = page.getByTestId('database-workbench');
  await database.getByRole('button', { name: /测试水头栅格/ }).click();
  await probeMap(database.getByRole('region', { name: '数据预览' }));
  await page.screenshot({ path: testInfo.outputPath('database-raster-probe.png') });
  await database.getByRole('button', { name: '关闭数据库' }).click();
  await page.getByTestId('result-card').getByRole('button', { name: '与另一版本比较' }).click();
  const comparison = database.getByRole('combobox', { name: '比较版本' });
  await expect(comparison.getByRole('option', { name: 'version-2' })).toHaveCount(1);
  await comparison.selectOption('version-2');
  await expect(database.getByText('semantics-1 → semantics-2-listed')).toBeVisible();
  await expect(database.getByText(/发布时语义.*选中时的语义/)).toBeVisible();
  await expect(database.getByText(/选择之后写入的语义/)).toHaveCount(0);
  await page.screenshot({ path: testInfo.outputPath('result-version-comparison.png') });
});

test('production chunks load the database and all chart types without loading charts for tables', async ({ page }, testInfo) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  const errors: string[] = [];
  const scripts: string[] = [];
  const querySubmissions: (string | null)[] = [];
  page.on('pageerror', error => errors.push(error.message));
  page.on('console', message => {
    if (message.type() === 'error' && !message.text().includes('net::ERR_CONNECTION_RESET')) errors.push(message.text());
  });
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
  await page.route('**/product-icon.svg', route => route.fulfill({
    contentType: 'image/svg+xml',
    body: readFileSync(new URL('../../products/groundwater/ui/product-icon.svg', import.meta.url), 'utf8'),
  }));
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
    else if (url.pathname.endsWith('/data/queries') && route.request().method() === 'POST') {
      querySubmissions.push(route.request().postData());
      if (querySubmissions.length === 1) return route.abort('connectionreset');
      body = { queryId: 'query-1', operationId: 'query-1' };
    }
    else if (url.pathname.endsWith('/data/queries/query-1')) body = {
      queryId: 'query-1', state: 'SUCCEEDED', error: null, resultAvailable: true,
      expiresAt: '2026-10-13T01:00:00Z',
      receipt: { queryId: 'query-1', inputs: [], plan: {}, planDigest: `sha256:${'c'.repeat(64)}`, coverage: { sourceRows: 3, matchedRows: 3, calculation: 'all_matching_rows' }, returnedRows: 3, resultChecksum: `sha256:${'b'.repeat(64)}`, truncated: false, engine: { name: 'test' } },
      result: { fields, rows: [[1, 4, 'A'], [2, 7, 'A'], [3, 5, 'B']], nextOffset: null, truncated: false, coverage: { sourceRows: 3 }, engine: { name: 'test' } },
    };
    else if (url.pathname.endsWith('/data/queries/query-1/cancel')) body = { queryId: 'query-1', state: 'SUCCEEDED' };
    else throw new Error(`Unexpected build-test API request: ${url.pathname}`);
    await route.fulfill({ json: body });
  });
  await page.goto('/');
  await page.getByRole('button', { name: '数据库', exact: true }).click();
  await page.getByRole('button', { name: /模拟监测表/ }).click();
  await expect(page.locator('.table-workspace .table-scroll')).toContainText('7');
  expect(querySubmissions).toHaveLength(2);
  expect(querySubmissions[1]).toBe(querySubmissions[0]);
  await page.screenshot({ path: testInfo.outputPath('database-desktop.png'), fullPage: true });
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
  await page.setViewportSize({ width: 1280, height: 800 });
  await expect(page.getByTestId('workspace-panel')).toHaveClass(/is-collapsed/);
  await expect(page.getByTestId('workspace-panel')).not.toHaveAttribute('role', 'dialog');
  await expect(page.getByRole('region', { name: '数据详情', exact: true })).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await expect(page.locator('.artifact-chart canvas').first()).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.screenshot({ path: testInfo.outputPath('database-compact-desktop.png'), fullPage: true });
  await page.keyboard.press('Escape');
  await expect(page.getByTestId('database-workbench')).toHaveCount(0);
  await page.setViewportSize({ width: 1440, height: 900 });
  await expect(page.getByTestId('workspace-panel')).not.toHaveClass(/is-collapsed/);
  expect(errors).toEqual([]);
});
