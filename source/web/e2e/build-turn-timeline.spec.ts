import { expect, test } from '@playwright/test';
import { readFileSync } from 'node:fs';
import { once } from 'node:events';
import { createServer, request } from 'node:http';
import { capabilitiesForProfile, dataDetail, dataRef, historyFixture, instant, replyFixture, sessionResultsFixture, sessionSummaryFixture } from '../src/test-fixtures';
import type { Confirmation } from '../src/types';
import { prepareClipboardReadback } from './clipboard-test-helper';

const profile = JSON.parse(readFileSync(new URL('../../products/groundwater/ui/product-ui.json', import.meta.url), 'utf8'));

test('Turn timeline resumes native SSE, merges an older page and keeps scientific content readable', async ({ page, browserName, baseURL }, info) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await prepareClipboardReadback(page, browserName);
  let final = false;
  let eventConnections = 0;
  let resumeId: string | undefined;
  const rows = Array.from({ length: 12 }, (_, index) => `| W${index + 1} | ${(12.1 + index / 10).toFixed(1)} | 统一高程基准后比较 |`);
  const question = '比较监测井水位，列出计算依据和表格。';
  const legacy = `- 监测井.csv（数据集: wells；版本: exact-wells；语义: wells-semantics；校验和: ${'a'.repeat(64)}）`;
  const markdown = [
    '**水位趋势。** 先核对观测时间、井口高程和缺测区间，再解释井间差异。',
    '对均质介质，沿剖面的达西通量为 $q=-K\\frac{dh}{dx}$。',
    '| 监测井 | 水位（m） | 说明 |\n| --- | ---: | --- |\n' + rows.join('\n'),
    '解释范围限于本次观测井。相邻井出现同步变化时，还需要核查抽水与降水记录。',
    '[聚合序列](/api/v12/data/versions/exact-wells/objects/raw-wells/content)',
    '```json\n{"trace_id":"run-123","version_id":"exact-wells"}\n```',
  ].join('\n\n');
  const sessionId = 'turn-browser';
  const input = dataRef({ versionId: 'frozen-head-observations' });
  const dictionary = { versionId: 'head-dictionary', unitId: 'level-entry', checksum: `sha256:${'b'.repeat(64)}` };
  const args = { groupField: 'well', timeField: 'observed_at', valueField: 'level', frequency: 'month', aggregation: 'mean',
    expectedStart: '2025-01-01', expectedEnd: '2025-12-01', baselineStart: '2025-01-01', baselineEnd: '2025-06-01',
    comparisonStart: '2025-07-01', comparisonEnd: '2025-12-01' };
  const confirmation: Confirmation = { confirmationId: 'trend-approval', revision: 1, goalRevision: 1, agentRunId: 'agent-1',
    status: 'CONFIRMED', decision: 'confirm', decisionState: null, reason: null,
    actions: [{ toolCallId: 'trend-run', tool: 'run_monitoring_trend', modelId: 'groundwater.monitoring-trend', modelVersion: '2.0.0',
      workflowId: 'groundwater.monitoring-trend', workflowVersion: '1.0.0', arguments: { versionId: input.versionId, ...args },
      inputReferences: { versionId: input } }],
  };
  const firstTurn = { goalRevision: 1, agentRunId: 'agent-1', reply: replyFixture('第一轮的正式答复'), confirmations: [confirmation] };
  let inputReads = 0;
  const latest = () => historyFixture({
    sessionId, goalRevision: 2, eventCursor: final ? 12 : 10, nextCursor: 3,
    items: [
      { messageId: 3, role: 'assistant', goalRevision: 1, content: '第一轮的正式答复' },
      { messageId: 4, role: 'user', goalRevision: 2, content: `${question}\n\n附加数据：\n${legacy}` },
      ...(final ? [{ messageId: 5, role: 'assistant', goalRevision: 2, content: '线性副本' }] : []),
    ],
    turns: [
      firstTurn,
      { goalRevision: 2, agentRunId: 'agent-2', status: final ? 'COMPLETE' : 'RUNNING', reply: final ? replyFixture(markdown) : null,
        clarificationRequired: final, interactionOptions: final ? [
          { label: '逐口井比较', description: '保留每口井的观测时间和缺测区间', message: '请逐口井比较水位。' },
          { label: '按监测区比较', description: '先按分区汇总，再检查井间差异', message: '请按监测区比较水位。' },
        ] : [],
        timing: { createdAt: '2026-10-09T01:00:03Z', startedAt: '2026-10-09T01:00:04Z', finishedAt: final ? '2026-10-09T01:00:09Z' : null } },
    ],
    preparation: { schemaVersion: 'task-preparation.v1', preparationId: 'head-preparation', revision: 1, sessionId, agentRunId: 'agent-1',
      goalRevision: 1, taskId: null, baseTaskRevision: null, status: 'READY', freshForCurrentTurn: false, disposition: 'STALE',
      entries: [{ targetKind: 'EXECUTABLE_MODEL', targetId: 'groundwater.monitoring-trend', targetVersion: '2.0.0', status: 'READY',
        selectionPolicy: 'PINNED', inputs: { versionId: input }, parameters: args, parameterSources: {}, checks: [], blockingReasons: [],
        knowledgeRefs: [dictionary] }],
    },
  });
  await page.route('**/product-ui.json', route => route.fulfill({ json: profile }));
  await page.route('**/product-icon.svg', route => route.fulfill({
    contentType: 'image/svg+xml', body: readFileSync(new URL('../../products/groundwater/ui/product-icon.svg', import.meta.url), 'utf8'),
  }));
  await page.route('**/api/v12/**', async route => {
    const url = new URL(route.request().url());
    const path = url.pathname;
    if (path.endsWith('/capabilities')) return route.fulfill({ json: capabilitiesForProfile(profile) });
    if (path.endsWith('/sessions')) return route.fulfill({ json: { items: [sessionSummaryFixture({ sessionId, title: '监测井水位趋势与依据' })], nextCursor: null } });
    if (path.endsWith('/messages') && url.searchParams.has('cursor')) return route.fulfill({ json: historyFixture({
      sessionId, goalRevision: 2, eventCursor: 12, nextCursor: null,
      items: [{ messageId: 1, role: 'user', goalRevision: 1, content: '如何统一监测井高程基准？' }],
      turns: [firstTurn],
    }) });
    if (path.endsWith('/messages')) return route.fulfill({ json: latest() });
    if (path.endsWith('/results')) return route.fulfill({ json: sessionResultsFixture([], { sessionId }) });
    if (path.endsWith('/preview')) return route.fulfill({ status: 204 });
    if (path.endsWith(`/data/versions/${input.versionId}`)) {
      inputReads++;
      return route.fulfill({ json: dataDetail({ versionId: input.versionId, title: '合成验收观测表',
        descriptor: { fields: [{ fieldId: 'f0', name: 'level', type: 'float64' }] },
        semantics: { fields: { f0: { quantityKind: 'hydraulic_head', unit: 'm' } } },
      }) });
    }
    if (path.endsWith('/knowledge/evidence/read')) {
      expect(route.request().postDataJSON().refs).toEqual([dictionary]);
      return route.fulfill({ json: { schemaVersion: 'evidence-bundle.v1', status: 'OK', receiptId: 'head-receipt', knowledgeRevision: 1,
        indexGeneration: 'generation-1', observedAt: instant, coverage: {}, budget: {}, timingsMs: {},
        items: [{ ...dictionary, documentTitle: '观测水头字典', content: 'level 为相对统一基准的观测水头高程。',
          status: 'OK', scope: 'DATA_BOUND', bindingMatch: 'NOT_CHECKED',
          bindings: [{ serviceBinding: 'data-primary', projectId: 'demo', datasetId: input.datasetId, versionId: input.versionId,
            semanticId: input.semanticId, fieldId: 'f0' }],
        }],
      } });
    }
    if (path.endsWith('/events')) return route.continue();
    throw new Error(`Unexpected timeline request: ${path}`);
  });

  // Use actual streamed HTTP so browser reconnect behavior is independent of
  // Playwright's route fulfillment and response buffering.
  let initialCursor: string | null = null;
  const server = createServer((incoming, response) => {
    const url = new URL(incoming.url ?? '/', baseURL);
    if (url.pathname.endsWith('/events')) {
      eventConnections++;
      if (eventConnections > 2) {
        response.writeHead(204).end();
        return;
      }
      response.writeHead(200, { 'Content-Type': 'text/event-stream', 'Cache-Control': 'no-cache' });
      if (eventConnections === 1) {
        initialCursor = url.searchParams.get('afterEventId');
        response.write('retry: 150\nid: 11\nevent: agent.activity\ndata: {"schemaVersion":"session-event.v20","type":"agent.activity","data":{"agentRunId":"agent-2"}}\n\n');
        response.end();
        return;
      }
      const header = incoming.headers['last-event-id'];
      resumeId = typeof header === 'string' ? header : undefined;
      final = true;
      response.end('id: 12\nevent: agent.complete\ndata: {"schemaVersion":"session-event.v20","type":"agent.complete","data":{"agentRunId":"agent-2"}}\n\n');
      return;
    }
    const forward = request(url, { method: incoming.method }, upstream => {
      response.writeHead(upstream.statusCode ?? 502, upstream.headers);
      upstream.pipe(response);
    });
    forward.on('error', () => response.destroy());
    incoming.pipe(forward);
  });
  server.listen(0, '127.0.0.1');
  await once(server, 'listening');
  try {
    const address = server.address();
    if (!address || typeof address === 'string') throw new Error('Local event fixture address unavailable');
    await page.goto(`http://127.0.0.1:${address.port}/#/sessions/turn-browser`);
    const second = page.getByRole('region', { name: '第 2 轮' });
    await expect.poll(() => resumeId).toBe('11');
    expect(initialCursor).toBe('10');
    await expect(second.getByText('水位趋势。')).toBeVisible();
    await expect(second.locator('math')).toHaveCount(1);
    await expect(second.getByRole('table')).toBeVisible();
    const clarification = second.getByRole('region', { name: '下一步选项' });
    await expect(clarification.getByRole('heading', { name: '确认分析方向' })).toBeVisible();
    await expect(clarification.getByRole('button')).toHaveCount(2);
    await expect(clarification.getByRole('button', { name: /逐口井比较/ })).toBeDisabled();
    await clarification.screenshot({ path: info.outputPath('clarification-desktop.png') });
    await page.getByRole('button', { name: '更早消息' }).click();
    const first = page.getByRole('region', { name: '第 1 轮' });
    await expect(first.getByText('如何统一监测井高程基准？')).toBeVisible();
    await expect(first.getByText('第一轮的正式答复')).toBeVisible();
    await expect(page.getByLabel('定位对话轮次')).toBeHidden();
    await page.locator('.turn-index > summary').press('Enter');
    await page.getByLabel('筛选轮次').selectOption('published');
    await expect(page.getByLabel('定位对话轮次')).toBeDisabled();
    await expect(page.getByLabel('定位对话轮次')).toContainText('没有符合条件的轮次');
    await page.getByLabel('筛选轮次').selectOption('all');
    await page.getByLabel('定位对话轮次').selectOption('2');
    await expect(second).toBeFocused();
    await second.getByRole('button', { name: '复制提问' }).click();
    await expect(second.getByText('已复制')).toBeVisible();
    await expect(second.locator('.copy-feedback[data-copied="true"]')).toHaveCSS('animation-name', 'workspace-reveal');
    expect(await page.evaluate(() => navigator.clipboard.readText())).toBe(`${question}\n\n附加资料\n- 监测井.csv`);
    await second.getByRole('button', { name: '复制回复' }).click();
    await expect(second.getByText('已复制')).toHaveCount(2);
    const copied = await page.evaluate(() => navigator.clipboard.readText());
    expect(copied).toContain('聚合序列');
    expect(copied).toContain('| W1 |');
    expect(copied).not.toMatch(/\/api\/v12|exact-wells|run-123/);
    await page.getByLabel('定位对话轮次').selectOption('2');
    await page.screenshot({ path: info.outputPath('turn-timeline-desktop.png') });

    await page.setViewportSize({ width: 1024, height: 768 });
    await page.getByLabel('定位对话轮次').selectOption('2');
    await expect(second).toBeFocused();
    const region = second.getByRole('region', { name: '分析表格，可横向滚动' });
    await expect(region).toHaveAttribute('tabindex', '0');
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    await page.screenshot({ path: info.outputPath('turn-timeline-desktop-1024.png') });
    await clarification.screenshot({ path: info.outputPath('clarification-desktop-1024.png') });

    expect(inputReads).toBe(0);
    await first.getByText('历史修订 1', { exact: true }).click();
    const card = first.getByTestId('confirmation-card');
    await expect(card.getByText('level · 水头高程（米）')).toBeVisible();
    await expect(card.getByText('统计周期 month · 聚合方式 mean')).toBeVisible();
    await expect(card.getByText(input.versionId, { exact: true })).toBeHidden();
    await card.getByRole('button', { name: '观测水头字典', exact: true }).click();
    await expect(page.getByRole('dialog', { name: '知识库' }).getByText('level 为相对统一基准的观测水头高程。')).toBeVisible();
  } finally {
    server.closeAllConnections();
    await new Promise<void>((resolve, reject) => server.close(error => error ? reject(error) : resolve()));
  }
});
