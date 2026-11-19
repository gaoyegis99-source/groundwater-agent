import { createHash } from 'node:crypto';
import { readFile, writeFile } from 'node:fs/promises';
import { createServer, request as forwardRequest } from 'node:http';
import { once } from 'node:events';
import { expect, test, type Locator } from '@playwright/test';
import { capabilitiesForProfile, dataDetail, dataRef, historyFixture, sessionResponseFixture, sessionResultsFixture, taskFixture } from '../src/test-fixtures';
import type { Confirmation } from '../src/types';
import { prepareClipboardReadback } from './clipboard-test-helper';


const fixture = (name: string) => readFile(new URL(`../../packages/operator-pack/catalog/fixtures/data/${name}`, import.meta.url));
const sha256 = (bytes: Buffer) => `sha256:${createHash('sha256').update(bytes).digest('hex')}`;

test('a published transfer backtest explains the baseline comparison and prioritizes business results', async ({ page, browserName }) => {
  await page.setViewportSize({ width: 1024, height: 768 });
  await prepareClipboardReadback(page, browserName);
  const product = JSON.parse(await readFile(new URL('../../products/groundwater/ui/product-ui-porous.v1.json', import.meta.url), 'utf8'));
  const modelId = 'groundwater.transfer-learning-analysis';
  const metricBody = Buffer.from(JSON.stringify({
    schemaVersion: 'regression-metrics.v1', rmse: 0.65, sample_count: 18, unit: 'm', baseline: { rmse: 0.19, sample_count: 18 },
  }));
  const model = product.models[modelId];
  const references = (model.resultOverview.sections as { outputs: string[] }[]).flatMap(section => section.outputs)
    .sort((a, b) => a === 'source-scaler' ? -1 : b === 'source-scaler' ? 1 : 0)
    .map(name => dataRef({ outputName: name, versionId: `version-${name}`, objectId: `object-${name}`,
      ...(name === 'metrics' && { checksum: sha256(metricBody), sizeBytes: metricBody.length }) }));
  const task = taskFixture({ modelId, status: 'SUCCEEDED', stage: null, outputCount: references.length });
  const confirmation: Confirmation = { confirmationId: task.confirmationId, agentRunId: task.agentRunId, goalRevision: 1,
    revision: 1, status: 'CONFIRMED', decision: 'confirm', decisionState: null, reason: null,
    actions: [{ toolCallId: task.toolCallId, tool: 'run_transfer_learning_analysis', modelId, modelVersion: '3.1.0',
      workflowId: modelId, workflowVersion: '1.0.0', arguments: { 'map-month.targetTime': '2024-12-01' } }] };
  await page.route('**/product-ui.json', route => route.fulfill({ json: product }));
  await page.route('**/api/v12/**', route => {
    const { pathname } = new URL(route.request().url());
    if (pathname.endsWith('/capabilities')) return route.fulfill({ json: capabilitiesForProfile(product) });
    if (pathname.endsWith('/sessions')) return route.fulfill({ json: { items: [], nextCursor: null } });
    if (pathname.endsWith('/messages')) return route.fulfill({ json: historyFixture({
      items: [{ messageId: 1, goalRevision: 1, role: 'user', content: '查看迁移学习回测结果' }],
      turns: [{ confirmations: [confirmation], executionTasks: [task] }],
    }) });
    if (pathname.endsWith('/results')) return route.fulfill({ json: sessionResultsFixture([{
      goalRevision: 1, agentRunId: task.agentRunId, toolCallId: task.toolCallId, modelId,
      status: 'COMPLETE', workflowRunId: task.workflowRunId, remoteOutputs: references,
    }]) });
    if (pathname.endsWith('/events') || pathname.endsWith('/preview')) return route.fulfill({ contentType: 'text/event-stream', body: ': ready\n\n' });
    if (pathname.endsWith('/versions/version-metrics/objects/object-metrics/content')) return route.fulfill({ body: metricBody, contentType: 'application/json' });
    const ref = references.find(item => pathname.endsWith(`/data/versions/${item.versionId}`));
    if (ref) return route.fulfill({ json: dataDetail({ versionId: ref.versionId, objects: [
      { objectId: ref.objectId, checksum: ref.checksum, format: ref.format, mediaType: ref.mediaType, size: ref.sizeBytes, role: 'raw' },
    ] }) });
    throw new Error(`Unexpected transfer result request: ${pathname}`);
  });
  await page.goto('/#/sessions/session-1');
  const result = page.getByTestId('result-card');
  await expect(result.getByText('模型的均方根误差高于基线。')).toBeVisible();
  const primary = result.getByRole('region', { name: '主要成果' });
  await expect(primary.locator('.output-heading > button')).toHaveText(['留出回测总体指标', '单步留出回测结果', '逐井留出回测指标', '制图点资料']);
  const history = page.getByText('历史修订 1', { exact: true });
  await expect(history).toBeVisible();
  expect(await result.evaluate(card => Boolean(card.compareDocumentPosition(document.querySelector('.historical-confirmation')!) & Node.DOCUMENT_POSITION_FOLLOWING))).toBe(true);
  await expect(page.getByTestId('confirmation-card')).toHaveCount(0);
  await history.click();
  await expect(page.getByTestId('confirmation-card').getByText('2024-12-01')).toBeVisible();
  await expect(result.getByRole('button', { name: '源域缩放器' })).toBeHidden();
  await result.getByText('训练与数据检查').click();
  await expect(result.getByRole('button', { name: '源域缩放器' })).toBeVisible();
  await result.getByRole('button', { name: '复制结果摘要' }).click();
  const copy = await page.evaluate(() => navigator.clipboard.readText());
  expect(copy).toContain('模型 RMSE 0.65 m · 基线 RMSE 0.19 m · 留出样本数 18');
  expect(copy).toContain('制图点资料');
  expect(copy).not.toMatch(/sha256|object-metrics|源域缩放器/);
  await page.evaluate(() => Object.defineProperty(navigator, 'clipboard', { value: undefined, configurable: true }));
  await result.getByRole('button', { name: '复制结果摘要' }).click();
  const manualCopy = result.getByRole('textbox', { name: '待复制文本' });
  await expect(manualCopy).toBeFocused();
  await expect(manualCopy).toHaveValue(copy);
  expect(await manualCopy.evaluate((node: HTMLTextAreaElement) => node.selectionEnd - node.selectionStart)).toBe(copy.length);
});

test('published result actions preserve identity across the Groundwater output formats', async ({ page, baseURL }, info) => {
  test.setTimeout(120_000);
  const extensions: Record<string, string> = { binary: 'bin', cog: 'tif', geopackage: 'gpkg', geotiff: 'tif', 'pytorch-state-dict': 'pt' };
  const filenameFor = (output: string | undefined, format: string) => `${output}.${extensions[format] ?? format}`;
  await page.setViewportSize({ width: 1440, height: 1000 });
  const product = JSON.parse(await readFile(new URL('../../products/groundwater/ui/product-ui.json', import.meta.url), 'utf8'));
  const raster = await fixture('groundwater-head-plane.tif');
  const geojson = Buffer.from(JSON.stringify({ type: 'FeatureCollection', features: [
    { type: 'Feature', properties: { reading: 42 }, geometry: { type: 'Point', coordinates: [116.4, 39.9] } },
  ] }));
  const table = (panel: Locator) => panel.getByRole('cell', { name: '42', exact: true });
  const metadata = (panel: Locator) => panel.getByText('校验和', { exact: true });
  const rasterControl = (panel: Locator) => panel.getByLabel('栅格探针');
  const cases: [string, string, Buffer, (panel: Locator) => Locator][] = [
    ['csv', 'text/csv', Buffer.from('reading\n42\n'), table],
    ['parquet', 'application/vnd.apache.parquet', await fixture('table-document.parquet'), table],
    ['json', 'application/json', Buffer.from('{"result_marker":"published"}'), panel => panel.getByText('result_marker', { exact: true })],
    ['geojson', 'application/geo+json', geojson, panel => panel.getByLabel('GeoJSON 数值着色')],
    ['geotiff', 'image/tiff', raster, rasterControl],
    ['cog', 'image/tiff', raster, rasterControl],
    ['geopackage', 'application/geopackage+sqlite3', await fixture('point.gpkg'), panel => panel.getByRole('combobox', { name: 'GeoPackage 图层', exact: true })],
    ['png', 'image/png', Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAAC0lEQVR4nGP4DwQACfsD/fteaysAAAAASUVORK5CYII=', 'base64'), panel => panel.locator('.artifact-image img')],
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
  const newSessions: unknown[] = [];
  const now = '2026-10-09T01:00:00Z';
  await page.route('**/product-ui.json', route => route.fulfill({ json: product }));
  await page.route('**/api/v12/**', async route => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    const tail = path.split('/').at(-1);
    const json = (value: unknown) => route.fulfill({ json: value });
    if (tail === 'capabilities') return json(capabilitiesForProfile({ productId: 'groundwater', models: product.models }));
    if (tail === 'sessions' && request.method() === 'POST') {
      newSessions.push(request.postDataJSON());
      return route.fulfill({ status: 201, json: sessionResponseFixture({ sessionId: 'continued-study', goalRevision: 0 }) });
    }
    if (tail === 'sessions') return json({ items: [], nextCursor: null });
    if (tail === 'events' || tail === 'preview') return route.fulfill({ contentType: 'text/event-stream', body: ': ready\n\n' });
    if (tail === 'messages') {
      if (request.method() === 'POST') sentMessages += 1;
      if (path.includes('/continued-study/')) return json(historyFixture({ sessionId: 'continued-study', goalRevision: 0, turns: [] }));
      return json(historyFixture({
        items: [{ messageId: 1, goalRevision: 1, role: 'user', content: '查看发布成果' }],
        turns: [{ executionTasks: [task] }],
      }));
    }
    if (tail === 'results' && path.includes('/continued-study/')) return json(sessionResultsFixture([], { sessionId: 'continued-study' }));
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
        headers: { 'content-disposition': `attachment; filename="${filenameFor(artifact.reference.outputName, artifact.format)}"` } });
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
      result: { kind: 'geopackage_layer', metadata: { layers: [{ tableName: 'wells' }], selectedLayer: 'wells', mapAvailable: true, crs: 'EPSG:4326' },
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
      response.writeHead(200, { 'Content-Type': artifact.reference.mediaType, 'Content-Length': artifact.bytes.length,
        'Content-Disposition': `attachment; filename="${filenameFor(artifact.reference.outputName, artifact.format)}"` });
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
    const inlineImage = resultCard.getByRole('img', { name: 'published png' });
    await expect(inlineImage).toBeVisible();
    await expect(inlineImage).toHaveJSProperty('naturalWidth', 1);
    await expect(resultCard.locator('.output-item').first()).toHaveAttribute('data-resource-id', 'version-png');
    await resultCard.getByRole('button', { name: '查看全部 12 个结果' }).click({ timeout: 10_000 });
    const [firstArtifact] = artifacts;
    const first = firstArtifact.reference;
    const firstOutput = resultCard.locator(`[data-resource-id="${first.versionId}"]`);
    await expect(firstOutput.locator('.output-format')).toHaveText('csv');
    await expect(firstOutput.getByText(first.versionId, { exact: true })).toBeHidden();
    const directDownload = page.waitForEvent('download');
    await firstOutput.getByRole('link', { name: /^下载/ }).click();
    const directReceipt = await directDownload;
    expect(sha256(await readFile((await directReceipt.path())!))).toBe(first.checksum);
    await firstOutput.getByRole('button', { name: '附加到当前草稿' }).click();
    const pendingResult = page.getByTestId('pending-attachments');
    await pendingResult.getByText('附件版本详情').click();
    await expect(pendingResult.getByText(first.versionId, { exact: true })).toBeVisible();
    await expect(pendingResult.getByText(first.checksum, { exact: true })).toBeVisible();
    await pendingResult.getByRole('button', { name: `移除附件 ${first.outputName}` }).click();
    await firstOutput.getByRole('button', { name: '与另一版本比较' }).click();
    await drawer.getByRole('combobox', { name: '比较版本' }).selectOption(firstArtifact.second.versionId);
    await expect(drawer.getByText(`${first.semanticId} → ${firstArtifact.second.semanticId}`)).toBeVisible();
    await drawer.getByRole('button', { name: '关闭数据库' }).click();
    for (const { format, bytes, reference, second, preview } of artifacts) {
      const output = resultCard.locator(`[data-resource-id="${reference.versionId}"]`);
      await output.locator('.output-heading').getByRole('button').click();
      await panel.getByText('查看精确版本与来源', { exact: true }).click();
      await expect(panel.getByText(reference.objectId, { exact: true })).toBeVisible();
      await expect(preview(panel)).toBeVisible();
      if (format === 'geopackage') {
        const help = panel.getByRole('button', { name: '坐标参考系说明' });
        await help.focus();
        await page.keyboard.press('Enter');
        const tooltip = page.getByRole('tooltip');
        await expect(tooltip).toBeVisible();
        await expect(help).toHaveAccessibleDescription(/坐标单位/);
        await page.keyboard.press('Escape');
        await expect(tooltip).toHaveCount(0);
        await help.hover();
        await expect(tooltip).toBeVisible();
        await tooltip.hover();
        // Hovering the explanation must outlast the trigger's close delay.
        await page.waitForTimeout(260);
        await expect(tooltip).toBeVisible();
        await page.keyboard.press('Escape');
        await expect(tooltip).toHaveCount(0);
        const tabs = panel.getByRole('tablist', { name: '空间图层视图' });
        await tabs.getByRole('tab', { name: '地图' }).focus();
        await page.keyboard.press('ArrowRight');
        await expect(tabs.getByRole('tab', { name: '属性表' })).toBeFocused();
        await expect(tabs.getByRole('tab', { name: '属性表' })).toHaveAttribute('aria-selected', 'true');
        await expect(panel.getByRole('tabpanel', { name: '属性表' }).getByRole('cell', { name: '42' })).toBeVisible();
      }
      await expect(panel.getByText('科学验证状态未提供', { exact: true })).toBeVisible();
      const download = page.waitForEvent('download');
      const downloadLink = panel.locator('.result-route-actions').getByRole('link', { name: '下载' });
      await expect(panel.locator('a[download]')).toHaveCount(1);
      const filename = filenameFor(reference.outputName, format);
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
    await firstOutput.locator('.output-heading').getByRole('button').click();
    await expect(panel.getByText('科学验证状态未提供', { exact: true })).toBeVisible();
    for (const [width, height] of [[1440, 900], [1280, 800], [1024, 768]]) {
      await page.setViewportSize({ width, height });
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
      await expect(panel.getByRole('button', { name: '返回对话' })).toBeInViewport();
      await expect(panel.getByText('科学验证状态未提供', { exact: true })).toBeVisible();
      await page.screenshot({ path: info.outputPath(`result-review-${width}x${height}.png`) });
    }
    const returnToResults = panel.getByRole('button', { name: '返回成果列表', exact: true });
    await returnToResults.press('Enter');
    await expect(panel.getByRole('heading', { name: '已发布成果', exact: true })).toBeFocused();
    const resultEntry = panel.getByRole('button', { name: 'published csv CSV · 版本 version-csv', exact: true });
    await resultEntry.press('Enter');
    await expect(panel.locator('.artifact-section .workspace-section-heading h2')).toBeFocused();
    await returnToResults.press('Enter');
    await expect(resultEntry).toBeFocused();
    await panel.getByRole('button', { name: '返回对话' }).click();
    await firstOutput.getByRole('button', { name: '带成果打开新会话' }).click();
    await expect(page).toHaveURL(/sessions\/continued-study$/);
    const attached = page.getByTestId('pending-attachments');
    await attached.getByText('附件版本详情').click();
    await expect(attached.getByText(first.objectId, { exact: true })).toBeVisible();
    await expect(attached.getByText(first.checksum, { exact: true })).toBeVisible();
    await expect(page.getByTestId('confirmation-card')).toHaveCount(0);
    await expect(page.getByTestId('result-card')).toHaveCount(0);
    expect(newSessions).toEqual([{ projectId: 'demo' }]);
    expect(sentMessages).toBe(0);
    await writeFile(info.outputPath('result-format-matrix.json'), JSON.stringify({ fixtureMode: 'browser route fixtures', sentMessages, evidence }, null, 2) + '\n');
  } finally {
    downloadServer.close();
    downloadServer.closeAllConnections();
  }
});

test('current confirmation and published task history share the screen without approving or replacing the current task', async ({ page }, info) => {
  await page.addInitScript(() => {
    class SnapshotEvents extends EventTarget {
      onopen: (() => void) | null = null;
      constructor() { super(); setTimeout(() => this.onopen?.(), 0); }
      close() {}
    }
    Object.defineProperty(window, 'EventSource', { value: SnapshotEvents });
  });
  const product = JSON.parse(await readFile(new URL('../../products/groundwater/ui/product-ui.json', import.meta.url), 'utf8'));
  const modelId = 'groundwater.summary';
  const waitingId = 'current-water-head';
  const pastId = 'previous-statistics';
  const stats = { count: 3, median: 17, q1: 16, q3: 18, nullCount: 0, nonFiniteCount: 0 };
  const contents = Buffer.from(JSON.stringify({ schemaVersion: 'statistics-describe-details.v1',
    selection: { column: 'level', groupField: 'well', unit: 'm' }, series: [{ field: { name: 'level' },
      statistics: stats, groups: [{ key: { type: 'string', value: 'W01' }, statistics: stats }] }] }));
  const output = dataRef({ outputName: 'summary-details', sizeBytes: contents.length, checksum: sha256(contents) });
  const input = dataRef({ versionId: 'b-head', objectId: 'input-table', format: 'csv', mediaType: 'text/csv' });
  const task = taskFixture({ modelId, status: 'SUCCEEDED', stage: null, outputCount: 1 });
  const confirmation: Confirmation = { confirmationId: 'review', agentRunId: task.agentRunId, goalRevision: 1, status: 'PENDING',
    reason: null, decision: null, decisionState: null, actions: [{ modelId, workflowId: modelId, toolCallId: task.toolCallId,
      tool: 'run_summary', modelVersion: '1.0.0', workflowVersion: '1.0.0', arguments: { versionId: input.versionId, column: 'level', unit: 'm', groupField: 'well' }, inputReferences: { versionId: input } }] };
  let historyReads = 0;
  let resultReads = 0;
  const writes: string[] = [];
  await page.route('**/product-ui.json', route => route.fulfill({ json: product }));
  await page.route('**/api/v12/**', route => {
    const url = new URL(route.request().url());
    const path = url.pathname;
    if (route.request().method() !== 'GET') { writes.push(path); return route.fulfill({ status: 403 }); }
    if (path.endsWith('/capabilities')) return route.fulfill({ json: capabilitiesForProfile(product) });
    if (path.endsWith('/sessions')) return route.fulfill({ json: { items: [], nextCursor: null } });
    if (path.endsWith('/task-history')) { historyReads++; return route.fulfill({ json: { items: [{ sessionId: pastId, goalRevision: 1,
      modelId, title: '历史逐井统计', status: 'PUBLISHED', updatedAt: '2025-12-01T12:00:00Z' }], nextCursor: null } }); }
    if (path.endsWith('/messages')) {
      const past = path.includes(pastId);
      return route.fulfill({ json: historyFixture({ sessionId: past ? pastId : waitingId,
        items: [{ messageId: 1, role: 'user', goalRevision: 1, content: past ? '历史逐井统计' : '按井统计观测水头' }],
        turns: [{ status: past ? 'COMPLETE' : 'WAITING_CONFIRMATION', confirmations: [past ? { ...confirmation, status: 'CONFIRMED' } : confirmation],
          progressUpdates: [
            { progressId: 'plan', agentRunId: task.agentRunId, workflowRunId: null, scope: 'TURN', code: 'PLAN_READY', status: 'COMPLETE', title: '准备方案', summary: '已准备本轮统计参数。', facts: {}, createdAt: null, startedAt: '2026-10-09T00:59:48Z', finishedAt: '2026-10-09T00:59:50Z' },
            { progressId: 'review', agentRunId: task.agentRunId, workflowRunId: null, scope: 'TURN', code: 'WAITING_CONFIRMATION', status: 'RUNNING', title: '核对资料', summary: '请核对数据和参数后确认执行。', facts: {}, createdAt: null, startedAt: '2026-10-09T00:59:51Z', finishedAt: null },
          ],
          executionTasks: past ? [task] : [] }] }) });
    }
    if (path.endsWith('/results')) return route.fulfill({ json: sessionResultsFixture(path.includes(pastId) ? [{ goalRevision: 1, agentRunId: task.agentRunId,
      workflowRunId: task.workflowRunId, toolCallId: task.toolCallId, modelId, status: 'COMPLETE', remoteOutputs: [output] }] : []) });
    if (path.endsWith('/preview') || path.endsWith('/events')) return route.fulfill({ contentType: 'text/event-stream', body: ': ready\n\n' });
    if (path.endsWith('/content')) { resultReads++; return route.fulfill({ contentType: 'application/json', body: contents }); }
    for (const ref of [input, output]) if (path.endsWith('/versions/' + ref.versionId)) return route.fulfill({ json: dataDetail({
      versionId: ref.versionId, title: '合成验收6k2e观测表（水头版）', descriptor: { fields: [{ fieldId: 'level', name: 'level', type: 'number' }] },
      semantics: { fields: { level: { meaning: '观测水头高程', unit: 'm' } } },
      objects: [{ objectId: ref.objectId, size: ref.sizeBytes, checksum: ref.checksum, role: 'raw', mediaType: ref.mediaType, format: ref.format }] }) });
    throw new Error('Unexpected history request ' + path);
  });
  await page.goto('/#/sessions/' + waitingId);
  const card = page.getByRole('region', { name: '当前研究任务' });
  await expect(card.getByText('已准备本轮统计参数。').first()).toBeVisible();
  await expect(card.getByText(/level · 观测水头高程/)).toBeVisible();
  await expect(card.locator('.statistics-selection')).toHaveText('统计 level（m），按 well 分组。');
  await expect(card.getByTestId('confirm-execution')).toHaveCount(1);
  await expect(card.getByTestId('confirm-execution')).toHaveText('确认执行');
  await expect(card.getByText('请核对数据和参数后确认执行。')).toBeHidden();
  const record = card.getByRole('button', { name: '执行记录' });
  await record.click();
  await expect(card.getByText('请核对数据和参数后确认执行。')).toBeVisible();
  const milestones = card.getByRole('list', { name: '流程里程碑' });
  await expect(milestones.locator('time').first()).toHaveAttribute('datetime', '2026-10-09T00:59:48.000Z');
  await expect(milestones.locator('time').last()).toHaveAttribute('datetime', '2026-10-09T00:59:51.000Z');
  await card.screenshot({ path: info.outputPath('activity-and-confirmation.png') });
  await record.click();
  const composer = page.getByRole('textbox', { name: '消息输入框' });
  await composer.fill('保留草稿');
  expect(historyReads).toBe(0);
  for (const width of [1440, 1024]) {
    await page.setViewportSize({ width, height: 900 });
    const open = page.getByRole('button', { name: '任务历史', exact: true });
    await open.click();
    const side = page.getByRole('complementary', { name: '项目任务历史' });
    if (width === 1440) expect(resultReads).toBe(0);
    await side.getByRole('button', { name: /已发布/ }).click();
    await expect(side.getByRole('row', { name: /W01 3 17/ })).toBeVisible();
    await expect(side.getByTestId('confirm-execution')).toHaveCount(0);
    await expect(page).toHaveURL(new RegExp(waitingId + '$'));
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    await page.screenshot({ path: info.outputPath('current-and-history-' + width + '.png') });
    await side.getByRole('button', { name: '返回任务列表' }).click();
    await expect(side.getByRole('button', { name: /已发布/ })).toBeFocused();
    await page.keyboard.press('Escape');
    await expect(open).toBeFocused();
    await expect(composer).toHaveValue('保留草稿');
  }
  expect(writes).toEqual([]);
});
