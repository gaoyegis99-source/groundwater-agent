import { createHash } from 'node:crypto';
import { readFile, writeFile } from 'node:fs/promises';
import { createServer, request as forwardRequest } from 'node:http';
import { once } from 'node:events';
import { expect, test, type Locator } from '@playwright/test';
import { capabilitiesForProfile, dataDetail, dataRef, historyFixture, sessionResultsFixture, taskFixture } from '../src/test-fixtures';

const fixture = (name: string) => readFile(new URL(`../../packages/operator-pack/catalog/fixtures/data/${name}`, import.meta.url));
const sha256 = (bytes: Buffer) => `sha256:${createHash('sha256').update(bytes).digest('hex')}`;

test('published result actions preserve identity across the Groundwater output formats', async ({ page, baseURL }, info) => {
  test.setTimeout(120_000);
  await page.setViewportSize({ width: 1440, height: 1000 });
  const product = JSON.parse(await readFile(new URL('../../products/groundwater/ui/product-ui.json', import.meta.url), 'utf8'));
  const raster = await fixture('groundwater-head-plane.tif');
  const geojson = Buffer.from(JSON.stringify({ type: 'FeatureCollection', features: [
    { type: 'Feature', properties: { reading: 42 }, geometry: { type: 'Point', coordinates: [116.4, 39.9] } },
  ] }));
  const table = (panel: Locator) => panel.getByRole('cell', { name: '42', exact: true });
  const metadata = (panel: Locator) => panel.getByRole('link', { name: '下载原文件' });
  const rasterControl = (panel: Locator) => panel.getByLabel('栅格探针');
  const cases: [string, string, Buffer, (panel: Locator) => Locator][] = [
    ['csv', 'text/csv', Buffer.from('reading\n42\n'), table],
    ['parquet', 'application/vnd.apache.parquet', await fixture('table-document.parquet'), table],
    ['json', 'application/json', Buffer.from('{"result_marker":"published"}'), panel => panel.getByText('result_marker', { exact: true })],
    ['geojson', 'application/geo+json', geojson, panel => panel.getByLabel('GeoJSON 数值着色')],
    ['geotiff', 'image/tiff', raster, rasterControl],
    ['cog', 'image/tiff', raster, rasterControl],
    ['geopackage', 'application/geopackage+sqlite3', await fixture('point.gpkg'), panel => panel.getByLabel('GeoPackage 图层')],
    ['png', 'image/png', Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk/w8AAgAB/2lXWQAAAABJRU5ErkJggg==', 'base64'), panel => panel.locator('.artifact-image img')],
    ['zip', 'application/zip', Buffer.from('504b0506000000000000000000000000000000000000', 'hex'), metadata],
    ['npy', 'application/vnd.numpy', await fixture('transfer-windows-targets.npy'), metadata],
    ['pytorch-state-dict', 'application/vnd.pytorch', Buffer.from('opaque test state'), metadata],
    ['binary', 'application/octet-stream', Buffer.from([0, 17, 42]), metadata],
  ];
  const fields = [{ fieldId: 'f0', name: 'reading', type: 'int64' }];
  const artifacts = cases.map(([format, mediaType, bytes, preview]) => {
    const reference = dataRef({
      datasetId: `dataset-${format}`, versionId: `version-${format}`, semanticId: `semantic-${format}`,
      objectId: `object-${format}`, outputName: `published-${format}`, format, mediaType,
      checksum: sha256(bytes), sizeBytes: bytes.length,
    });
    const detail = dataDetail({ datasetId: reference.datasetId, versionId: reference.versionId, semanticId: reference.semanticId,
      title: `published-${format}`, currentVersion: `second-${format}`,
      inputVersions: [`input-${format}`], descriptor: { fields, policy: ['csv', 'parquet'].includes(format) ? 'FILE_QUERYABLE' : 'FILE_ONLY' },
      objects: [{ objectId: reference.objectId, checksum: reference.checksum, format, mediaType, size: bytes.length, role: 'raw' }],
    });
    const second = dataDetail({ ...detail, versionId: `second-${format}`, semanticId: `second-semantic-${format}`,
      inputVersions: [`second-input-${format}`], objects: [{ ...detail.objects[0], objectId: `second-object-${format}`, checksum: sha256(Buffer.from(format)) }],
    });
    return { format, bytes, preview, reference, detail, second };
  });
  const task = taskFixture({ status: 'SUCCEEDED', stage: null, outputCount: artifacts.length });
  let sentMessages = 0;
  const now = '2026-10-09T01:00:00Z';
  await page.route('**/product-ui.json', route => route.fulfill({ json: product }));
  await page.route('**/api/v12/**', async route => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    const tail = path.split('/').at(-1);
    const json = (value: unknown) => route.fulfill({ json: value });
    if (tail === 'capabilities') return json(capabilitiesForProfile({ productId: 'groundwater', models: product.models }));
    if (tail === 'sessions') return json({ items: [], nextCursor: null });
    if (tail === 'events' || tail === 'preview') return route.fulfill({ contentType: 'text/event-stream', body: ': ready\n\n' });
    if (tail === 'messages') {
      if (request.method() === 'POST') sentMessages += 1;
      return json(historyFixture({
        items: [{ messageId: 1, goalRevision: 1, role: 'user', content: '查看发布成果' }],
        turns: [{ executionTasks: [task] }],
      }));
    }
    if (tail === 'results') return json(sessionResultsFixture([{
      goalRevision: 1, agentRunId: task.agentRunId, toolCallId: task.toolCallId, modelId: task.modelId,
      status: 'COMPLETE', workflowRunId: task.workflowRunId, remoteOutputs: artifacts.map(item => item.reference),
    }]));
    if (tail === 'datasets') return json({ items: [], nextCursor: null, matchBasis: 'catalog' });
    const catalog = artifacts.find(item => path.endsWith(`/data/datasets/${item.reference.datasetId}/versions`));
    if (catalog) return json({ items: [catalog.detail, catalog.second].map(({ versionId, semanticId }) => ({ versionId, semanticId, createdAt: now })), nextCursor: null });
    const detail = artifacts.flatMap(item => [item.detail, item.second]).find(item => path.endsWith(`/data/versions/${item.versionId}`));
    if (detail) return json(detail);
    const artifact = artifacts.find(item => path.endsWith(`/versions/${item.reference.versionId}/objects/${item.reference.objectId}/content`));
    if (artifact) {
      const range = /^bytes=(\d+)-(\d+)$/.exec(request.headers().range ?? '');
      if (range) {
        const start = Number(range[1]), end = Math.min(Number(range[2]), artifact.bytes.length - 1);
        return route.fulfill({ status: 206, contentType: artifact.reference.mediaType, body: artifact.bytes.subarray(start, end + 1),
          headers: { 'content-range': `bytes ${start}-${end}/${artifact.bytes.length}` } });
      }
      return route.fulfill({ contentType: artifact.reference.mediaType, body: artifact.bytes,
        headers: { 'content-disposition': `attachment; filename="result.${artifact.format}"` } });
    }
    if (tail === 'queries') return json({ queryId: 'result-query', operationId: 'result-query' });
    if (tail === 'result-query') return json({
      queryId: tail, state: 'SUCCEEDED', error: null, resultAvailable: true, expiresAt: now,
      receipt: { queryId: tail, inputs: [], plan: {}, planDigest: sha256(Buffer.from('plan')), coverage: { sourceRows: 1, matchedRows: 1, calculation: 'all_matching_rows' }, returnedRows: 1, resultChecksum: sha256(Buffer.from('42')), truncated: false, engine: {} },
      result: { fields, rows: [[42]], nextOffset: null, truncated: false, coverage: {}, engine: {} },
    });
    if (tail === 'previews') return json({ previewId: 'result-preview', operationId: 'result-preview' });
    if (tail === 'result-preview') return json({
      previewId: tail, state: 'SUCCEEDED', error: null, receipt: {}, resultAvailable: true, expiresAt: now,
      result: { kind: 'geopackage_layer', metadata: { layers: [{ tableName: 'wells' }], selectedLayer: 'wells', mapAvailable: false },
        fields, rows: [[42]], features: [], truncated: false, warnings: [], nextOffset: null },
    });
    if (tail === 'cancel' && path.includes('/data/')) return json({ queryId: 'result-query', previewId: 'result-preview', state: 'CANCELLED' });
    throw new Error(`Unexpected result format fixture request ${request.method()} ${path}`);
  });

  // Chromium's native download can bypass Playwright routing. Serve those
  // bytes from the same origin and forward the production shell to preview.
  const downloadServer = createServer((request, response) => {
    const path = new URL(request.url ?? '/', baseURL).pathname;
    const artifact = artifacts.find(item => path === `/api/v12/data/versions/${item.reference.versionId}/objects/${item.reference.objectId}/content`);
    if (artifact) {
      response.writeHead(200, { 'Content-Type': artifact.reference.mediaType, 'Content-Length': artifact.bytes.length });
      response.end(artifact.bytes);
      return;
    }
    const forwarded = forwardRequest(new URL(request.url ?? '/', baseURL), { method: request.method }, upstream => {
      response.writeHead(upstream.statusCode ?? 502, upstream.headers);
      upstream.pipe(response);
    });
    forwarded.on('error', () => { response.writeHead(502); response.end(); });
    request.pipe(forwarded);
  });
  downloadServer.listen(0, '127.0.0.1');
  await once(downloadServer, 'listening');
  const address = downloadServer.address();
  if (!address || typeof address === 'string') throw new Error('Local result fixture address unavailable');
  try {
    await page.goto(`http://127.0.0.1:${address.port}/#/sessions/session-1`);
    const resultCard = page.getByTestId('result-card');
    const panel = page.getByTestId('workspace-panel');
    const drawer = page.getByTestId('database-workbench');
    const evidence = [];
    const extensions: Record<string, string> = { binary: 'bin', cog: 'tif', geopackage: 'gpkg', geotiff: 'tif', 'pytorch-state-dict': 'pt' };
    await resultCard.getByRole('button', { name: '查看全部 12 个结果' }).click({ timeout: 10_000 });
    for (const { format, bytes, reference, second, preview } of artifacts) {
      const output = resultCard.locator(`[data-resource-id="${reference.versionId}"]`);
      await output.locator('.output-heading').getByRole('button').click();
      await expect(panel.getByText(reference.objectId, { exact: true })).toBeVisible();
      await expect(preview(panel)).toBeVisible();
      const download = page.waitForEvent('download');
      const downloadLink = panel.locator('.result-route-actions').getByRole('link', { name: '下载' });
      const filename = `${reference.outputName}.${extensions[format] ?? format}`;
      await expect(downloadLink).toHaveAttribute('download', filename);
      await downloadLink.click();
      const receipt = await download;
      expect(receipt.suggestedFilename()).toBe(filename);
      const downloaded = await readFile((await receipt.path())!);
      expect(downloaded.length).toBe(bytes.length);
      expect(sha256(downloaded)).toBe(reference.checksum);
      await panel.getByRole('button', { name: '与另一版本比较' }).click();
      const compare = drawer.getByRole('combobox', { name: '比较版本' });
      await compare.selectOption(second.versionId);
      await expect(drawer.getByText(`${reference.semanticId} → ${second.semanticId}`)).toBeVisible();
      await expect(drawer.getByText(`${reference.checksum} → raw:${second.objects[0].checksum}`, { exact: false })).toBeVisible();
      await expect(drawer.getByText(`input-${format} → second-input-${format}`)).toBeVisible();
      await drawer.getByRole('button', { name: '关闭数据库' }).click();
      await panel.getByRole('button', { name: '附加到当前草稿' }).click();
      const pending = page.getByTestId('pending-attachments');
      await pending.getByText('附件版本详情').click();
      await expect(pending.getByText(reference.semanticId, { exact: true })).toBeVisible();
      await expect(pending.getByText(reference.objectId, { exact: true })).toBeVisible();
      await expect(pending.getByText(reference.checksum, { exact: true })).toBeVisible();
      await pending.getByRole('button', { name: `移除附件 ${reference.outputName}` }).click();
      evidence.push({ format, filename, versionId: reference.versionId, objectId: reference.objectId, checksum: reference.checksum, comparisonVersionId: second.versionId });
    }
    expect(sentMessages).toBe(0);
    await writeFile(info.outputPath('result-format-matrix.json'), JSON.stringify({ fixtureMode: 'browser route fixtures', sentMessages, evidence }, null, 2) + '\n');
  } finally {
    downloadServer.close();
    downloadServer.closeAllConnections();
  }
});
