import { expect, test, type Page } from '@playwright/test';
import { readFileSync } from 'node:fs';
import { capabilitiesForProfile, historyFixture, replyFixture, turnFixture } from '../src/test-fixtures';
import type { History, MessageRequest, MessageReceipt } from '../src/types';

const assembledProductUi = JSON.parse(
  readFileSync(new URL('../../products/groundwater/ui/product-ui.json', import.meta.url), 'utf8'),
) as { productId: string; models: Record<string, unknown> };
const assembledCapabilities = capabilitiesForProfile(assembledProductUi, {
  projectId: 'demo',
  currentUser: { subject: 'test-user', displayName: '测试用户', roles: ['researcher'] },
});

// Browser presentation and delivery recovery with explicit API fault injection.
// Real routing/provider behavior is covered by the backend and Gateway suites.
async function fixture(page: Page, { holdAutoRouting = false, clarification = false, debug = false } = {}) {
  // This fixture drives snapshots directly. Keep the SSE connection stable so
  // the production freshness guard can distinguish it from a real disconnect.
  await page.addInitScript(() => {
    class FixtureEventSource extends EventTarget {
      onopen: (() => void) | null = null;
      private closed = false;
      constructor() {
        super();
        queueMicrotask(() => { if (!this.closed) this.onopen?.(); });
      }
      close() { this.closed = true; }
    }
    Object.assign(window, { EventSource: FixtureEventSource });
  });
  let history: History = historyFixture({ sessionId: 'routing-browser', goalRevision: 0, turns: [] });
  const posts: MessageRequest[] = [];
  let fail = false;
  await page.route('**/product-ui.json', route => route.fulfill({ json: assembledProductUi }));
  await page.route('**/api/v12/**', async route => {
    const url = new URL(route.request().url());
    const reply = (value: unknown, status = 200) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(value) });
    if (url.pathname.endsWith('/capabilities')) return reply(assembledCapabilities);
    if (url.pathname.endsWith('/sessions')) return reply({ items: [], nextCursor: null });
    if (url.pathname.endsWith('/events') || url.pathname.endsWith('/preview')) return route.fulfill({ contentType: 'text/event-stream', body: ': test connection\n\n' });
    if (url.pathname.endsWith('/results')) return reply({ items: [], nextCursor: null });
    if (url.pathname.endsWith('/trace')) return reply({});
    if (url.pathname.endsWith('/messages') && route.request().method() === 'GET') return reply(history);
    if (url.pathname.endsWith('/messages') && route.request().method() === 'POST') {
      const body = route.request().postDataJSON() as MessageRequest; posts.push(body);
      if (fail) return route.abort('connectionreset');
      const existing = history.turns.find(turn => turn.requestId === body.requestId);
      const revision = existing?.goalRevision ?? history.goalRevision + 1;
      const mode = body.requestedMode === 'AUTO' ? 'DIALOGUE' : body.requestedMode;
      const routing = holdAutoRouting && body.requestedMode === 'AUTO';
      const turn = existing ?? turnFixture({ goalRevision: revision, requestId: body.requestId,
        requestedMode: body.requestedMode, requestMode: mode,
        routingSource: body.requestedMode === 'AUTO' ? 'AUTO' : 'EXPLICIT',
        resolvedMode: routing ? null : mode,
        status: routing ? 'ROUTING' : 'COMPLETE',
        reply: routing ? null : replyFixture(`第 ${revision} 轮已回复`),
        ...(routing ? { timing: { createdAt: '2026-10-09T01:00:00Z', startedAt: null, finishedAt: null } } : {}),
        routingStatus: routing ? 'ROUTING_RUNNING' : body.requestedMode === 'AUTO' ? 'START_REQUESTED' : 'DECIDED',
        intentDecision: routing ? null : body.requestedMode === 'AUTO' ? clarification ? 'NEEDS_CLARIFICATION' : 'DIALOGUE' : null,
        clarificationRequired: !routing && body.requestedMode === 'AUTO' && clarification,
        correctsRequestId: body.correctsRequestId ?? null,
        interactionOptions: clarification ? [
          { label: '整理处理流程', description: '形成可复用流程', message: '请整理当前任务的可复用工作流。' },
          { label: '普通说明', description: '只解释可行方法', message: '请只说明当前可行的方法。' },
        ] : [] });
      if (!existing) history = { ...history, goalRevision: revision, eventCursor: revision, turns: [...history.turns, turn], items: [...history.items,
        { messageId: revision * 2 - 1, goalRevision: revision, role: 'user', content: body.message },
        ...(routing ? [] : [{ messageId: revision * 2, goalRevision: revision, role: 'assistant', content: `第 ${revision} 轮已回复` }])] };
      const receipt: MessageReceipt = { schemaVersion: 'message-request.v5', request: {
        requestId: body.requestId, message: body.message, requestedMode: body.requestedMode,
        correctsRequestId: body.correctsRequestId ?? null,
        inputReferences: (body.inputReferences ?? []).map(item => ({
          kind: item.kind, service: item.service, datasetId: item.datasetId,
          versionId: item.versionId, semanticId: item.semanticId,
          objectId: item.objectId, mediaType: item.mediaType,
          format: item.format, sizeBytes: item.sizeBytes, checksum: item.checksum,
          ...(item.outputName ? { outputName: item.outputName } : {}),
        })),
      }, requestId: body.requestId,
        sessionId: history.sessionId, goalRevision: revision, agentRunId: turn.agentRunId,
        requestedMode: body.requestedMode, routingSource: body.requestedMode === 'AUTO' ? 'AUTO' : 'EXPLICIT', routingStatus: body.requestedMode === 'AUTO' ? 'ROUTING_REQUESTED' : 'DECIDED', intentDecision: body.requestedMode === 'AUTO' ? null : turn.intentDecision, dialoguePolicy: body.requestedMode === 'DIALOGUE' ? 'READ_ONLY' : null,
        intent: body.requestedMode === 'AUTO' ? null : { schemaVersion: 'intent-decision.v4', responseKind: turn.intentDecision ?? 'DIALOGUE', semanticIntent: 'UNSPECIFIED', taskAction: 'NONE', dialoguePolicy: body.requestedMode === 'DIALOGUE' ? 'READ_ONLY' : null, controlIntent: null, clarificationReason: null },
        semanticIntent: body.requestedMode === 'AUTO' ? null : 'UNSPECIFIED', taskAction: body.requestedMode === 'AUTO' ? null : 'NONE', controlIntent: null, clarificationReason: null,
        resolvedMode: body.requestedMode === 'AUTO' ? null : mode, clarificationRequired: false, errorCode: null,
        workflowDraftId: null, correctsRequestId: body.correctsRequestId ?? null };
      return reply(receipt, 202);
    }
    throw new Error(`Unexpected browser fixture request ${route.request().method()} ${url.pathname}`);
  });
  await page.goto(debug ? '/?debug=1#/sessions/routing-browser' : '/#/sessions/routing-browser');
  await expect(page.getByRole('textbox', { name: '消息输入框' })).toBeEnabled();
  return { posts, setFailure: (value: boolean) => { fail = value; }, history: () => history,
    resolveAutoRouting: () => {
      const turn = history.turns.find(item => item.requestedMode === 'AUTO' && item.status === 'ROUTING');
      if (!turn) throw new Error('No pending AUTO turn');
      history = { ...history, eventCursor: history.eventCursor + 1,
        turns: history.turns.map(item => item === turn ? { ...item, status: 'COMPLETE', routingStatus: 'START_REQUESTED',
          intentDecision: 'DIALOGUE', resolvedMode: 'DIALOGUE', timing: { ...item.timing, finishedAt: '2026-10-09T01:00:01Z' },
          reply: replyFixture(`第 ${item.goalRevision} 轮已回复`),
          routingMetrics: { provider: 'test-router', model: 'test-router-model', requestCount: 1,
            inputTokens: 23, outputTokens: 5, totalTokens: 28, durationMs: 37, unknownAttempts: 0 } } : item),
        items: [...history.items, { messageId: turn.goalRevision * 2, goalRevision: turn.goalRevision,
          role: 'assistant', content: `第 ${turn.goalRevision} 轮已回复` }] };
    } };
}

test('desktop composer exposes no mode override and submits former slash commands as AUTO text', async ({ page }) => {
  const state = await fixture(page);
  await page.setViewportSize({ width: 1440, height: 400 });
  const input = page.getByRole('textbox', { name: '消息输入框' });
  await expect(page.getByRole('button', { name: '高级发送选项' })).toHaveCount(0);
  await expect(page.getByRole('listbox')).toHaveCount(0);
  await input.fill('/draft 自动处理 W01'); await input.press('Enter');
  await expect(page.getByText('第 1 轮已回复')).toBeVisible();
  expect(state.posts[0]).toMatchObject({ message: '/draft 自动处理 W01', requestedMode: 'AUTO' });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
});

test('AUTO shows routing progress and keeps the resolved decision and usage inside debug details', async ({ page }) => {
  const state = await fixture(page, { holdAutoRouting: true, debug: true });
  const input = page.getByRole('textbox', { name: '消息输入框' });
  await input.fill('解释监测数据 W01'); await input.press('Enter');
  await expect(page.getByRole('status')).toContainText('正在判断需求');

  state.resolveAutoRouting();
  await page.reload();
  await expect(page.getByText('第 1 轮已回复')).toBeVisible();
  await expect(page.getByText('正在判断需求', { exact: true })).toHaveCount(0);
  await expect(page.getByText(state.posts[0].requestId, { exact: false })).not.toBeVisible();

  await page.getByText('调试信息', { exact: true }).click();
  await page.getByText('需求判断与请求记录', { exact: true }).click();
  const details = page.locator('.debug-content');
  await expect(details).toContainText(`"requestId": "${state.posts[0].requestId}"`);
  await expect(details).toContainText('"intentDecision": "DIALOGUE"');
  await expect(details).toContainText('"durationMs": 37');
  await expect(details).toContainText('"totalTokens": 28');
});

test('reload restores an uncertain request and retries its exact identity and body once', async ({ page }) => {
  const state = await fixture(page); state.setFailure(true);
  const input = page.getByRole('textbox', { name: '消息输入框' });
  await input.fill('先排序，再统计 W01'); await input.press('Enter');
  await expect(page.getByRole('button', { name: '重试发送' })).toBeVisible();
  const original = state.posts[0];
  await page.reload();
  await expect(input).toHaveValue('先排序，再统计 W01');
  await expect(page.getByRole('button', { name: '重试发送' })).toBeEnabled();
  state.setFailure(false); await page.getByRole('button', { name: '重试发送' }).click();
  await expect(page.getByText('第 1 轮已回复')).toBeVisible();
  expect(state.posts).toEqual([original, original]);
  await expect(input).toHaveValue('');
  expect(state.history().turns).toHaveLength(1);
});

test('clarification offers contextual choices that remain ordinary AUTO messages', async ({ page }) => {
  const state = await fixture(page, { clarification: true });
  const input = page.getByRole('textbox', { name: '消息输入框' });
  await input.fill('帮我整理一个可复用的工作流模板'); await input.press('Enter');
  await expect(page.getByText('第 1 轮已回复')).toBeVisible();
  await expect(page.getByText('处理方式不对', { exact: true })).toHaveCount(0);
  const options = page.getByRole('region', { name: '下一步选项' });
  await expect(options.getByRole('button').first()).toContainText('整理处理流程');
  await options.getByRole('button', { name: /整理处理流程/ }).click();
  await expect(page.getByText('第 2 轮已回复')).toBeVisible();
  await expect(page.getByText('第 1 轮已回复')).toBeVisible();
  expect(state.posts[1]).toEqual(expect.objectContaining({ message: '请整理当前任务的可复用工作流。', requestedMode: 'AUTO' }));
  expect(state.posts[1]).not.toHaveProperty('correctsRequestId');
  expect(state.posts[1].requestId).not.toBe(state.posts[0].requestId);
  await page.reload();
  await expect(page.getByText('第 1 轮已回复')).toBeVisible();
  await expect(page.getByText('已按你调整的方式重新处理，原记录保留在上方。')).toHaveCount(0);
});

test('structured replies keep evidence folded, open the exact source, and reset disclosure after reload', async ({ page }) => {
  const checksum = `sha256:${'a'.repeat(64)}`;
  const knowledgeId = 'method-card.v2';
  const history = historyFixture({
    sessionId: 'structured-reply',
    items: [
      { messageId: 1, goalRevision: 1, role: 'user', content: '给我简洁结论' },
      { messageId: 2, goalRevision: 1, role: 'assistant', content: '不应展示的线性全文' },
    ],
    turns: [{ reply: {
      conclusion: { markdown: '首屏结构化结论', sourceIndexes: [0] },
      details: [{ markdown: '折叠的证据说明', sourceIndexes: [0] }],
      executionSummary: null, unresolvedItems: [],
      sources: [{ title: '方法说明', versionId: knowledgeId, unitId: 'unit-2', checksum }],
    } }],
  });
  await page.route('**/product-ui.json', route => route.fulfill({ json: assembledProductUi }));
  await page.route('**/api/v12/**', async route => {
    const url = new URL(route.request().url());
    const reply = (value: unknown) => route.fulfill({ contentType: 'application/json', body: JSON.stringify(value) });
    if (url.pathname.endsWith('/capabilities')) return reply(assembledCapabilities);
    if (url.pathname.endsWith('/sessions')) return reply({ items: [], nextCursor: null });
    if (url.pathname.endsWith('/events') || url.pathname.endsWith('/preview')) return route.fulfill({ contentType: 'text/event-stream', body: ': connected\n\n' });
    if (url.pathname.endsWith('/results')) return reply({ items: [], nextCursor: null });
    if (url.pathname.endsWith('/messages')) return reply(history);
    if (url.pathname.endsWith('/knowledge/evidence/read') && route.request().method() === 'POST') return reply({
      schemaVersion: 'evidence-bundle.v1', status: 'OK', receiptId: 'receipt-1', knowledgeRevision: 1,
      indexGeneration: 'generation-1', observedAt: '2026-10-09T01:00:00Z', coverage: {}, budget: {}, timingsMs: {},
      items: [{ status: 'OK', versionId: knowledgeId, unitId: 'unit-2', checksum,
        knowledgeId: '方法说明', content: '精确读取的知识正文', scope: 'GENERAL', bindingMatch: 'GENERAL', sources: [] }],
    });
    throw new Error(`Unexpected browser fixture request ${route.request().method()} ${url.pathname}`);
  });

  await page.goto('/#/sessions/structured-reply');
  const turn = page.locator('[data-goal-revision="1"]');
  await expect(turn.getByText('首屏结构化结论')).toBeVisible();
  await expect(turn.getByText('不应展示的线性全文')).toHaveCount(0);
  const evidence = turn.locator('details.reply-details');
  await expect(evidence).not.toHaveAttribute('open', '');
  await evidence.locator('summary').press('Enter');
  await expect(turn.getByText('折叠的证据说明')).toBeVisible();
  const sourceButton = turn.getByRole('button', { name: '打开依据：方法说明' });
  await expect(sourceButton).toHaveCount(1);
  await expect(sourceButton).not.toContainText('unit-2');
  const exactRead = page.waitForRequest(request => new URL(request.url()).pathname.endsWith('/knowledge/evidence/read'));
  await sourceButton.click();
  const read = await exactRead;
  expect(read.method()).toBe('POST');
  expect(read.postDataJSON()).toEqual({ refs: [{
    versionId: knowledgeId, unitId: 'unit-2', checksum,
  }] });
  await expect(page.getByTestId('knowledge-drawer')).toContainText('精确读取的知识正文');
  await page.getByRole('button', { name: '关闭知识库' }).click();
  expect(await turn.locator('.assistant-body').evaluate(node => {
    const children = [...node.children];
    return children.indexOf(node.querySelector('.activity-panel')!) < children.indexOf(node.querySelector('.reply-text')!);
  })).toBe(true);
  await page.reload();
  await expect(page.locator('details.reply-details')).not.toHaveAttribute('open', '');
});
