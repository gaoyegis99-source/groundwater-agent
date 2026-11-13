import { expect, test } from '@playwright/test';
import { readFileSync } from 'node:fs';
import { capabilitiesForProfile, dataDetail, dataRef, historyFixture, messageReceiptFixture, replyFixture, sessionResultsFixture, sessionSummaryFixture } from '../src/test-fixtures';
import type { Confirmation, TaskContext } from '../src/types';

const profile = JSON.parse(readFileSync(new URL('../../products/groundwater/ui/product-ui.json', import.meta.url), 'utf8'));

test('research overview restores the current Task revision and fits a narrow workspace', async ({ page }, info) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.addInitScript(() => {
    class StableEvents extends EventTarget {
      onopen: (() => void) | null = null;
      constructor() { super(); queueMicrotask(() => this.onopen?.()); }
      close() {}
    }
    Object.assign(window, { EventSource: StableEvents });
  });
  const sessionId = 'monitoring-study';
  const supplement = '两口井的井口高程分别为 83.2 米和 85.7 米。';
  const pendingConfirmation: Confirmation = {
    confirmationId: 'confirmation-current', goalRevision: 2, agentRunId: 'agent-2', status: 'PENDING',
    reason: null, decision: null, decisionState: null, revision: 2, kind: 'EDITED', replacesConfirmationId: 'confirmation-old',
    actions: [{
      toolCallId: 'call-1', tool: 'run_summary', modelId: Object.keys(profile.models)[0], modelVersion: '1.0.0',
      workflowId: 'groundwater.summary', workflowVersion: '1.0.0', arguments: {},
    }],
  };
  let revision = 1;
  let snapshotReads = 0;
  let waitingConfirmation = false;
  const objective = '比较不同补给条件下的监测井季节性水位，并复核高程基准及观测时间。'.repeat(10);
  const task = (): TaskContext => ({
    schemaVersion: 'task-context.v1',
    spec: { sessionId, taskId: 'monitoring-task', revision, objective,
      constraints: [{ id: 'scope', text: '只比较高程基准一致的观测井。' }],
      criteria: [{ id: 'datum', text: '核实高程基准', kind: 'REPLY' }, { id: 'explain', text: '说明变化趋势与数据限制', kind: 'REPLY' }],
      unresolvedItems: revision === 1 ? ['缺少两口监测井的井口高程。'] : [],
      suggestions: ['对不同补给条件的井组分别解释。'],
      steps: [
        { id: 'datum', title: '核对井口高程基准', dependsOn: [], criterionIds: ['datum'], gaps: revision === 1 ? ['井口高程未齐'] : [] },
        { id: 'compare', title: '比较水位季节变化', dependsOn: ['datum'], criterionIds: ['explain'], gaps: [] },
      ] },
    progress: { lifecycle: 'ACTIVE', state: waitingConfirmation ? 'WAITING_CONFIRMATION' : revision === 1 ? 'WAITING_INPUT' : 'ANALYZING',
      criteria: [{ id: 'datum', satisfied: revision > 1, evidence: revision > 1 ? { criterionId: 'datum', agentRunId: 'agent-1' } : null },
        { id: 'explain', satisfied: false, evidence: null }],
      steps: [{ id: 'datum', complete: revision > 1 }, { id: 'compare', complete: false }],
      results: [], blockingReasons: [], metrics: { totalTokens: 0, durationMs: 0 } },
  });
  await page.route('**/product-ui.json', route => route.fulfill({ json: profile }));
  await page.route('**/product-icon.svg', route => route.fulfill({
    contentType: 'image/svg+xml', body: readFileSync(new URL('../../products/groundwater/ui/product-icon.svg', import.meta.url), 'utf8'),
  }));
  await page.route('**/api/v12/**', async route => {
    const url = new URL(route.request().url());
    const path = url.pathname;
    if (path.endsWith('/capabilities')) return route.fulfill({ json: capabilitiesForProfile(profile) });
    if (path.endsWith('/sessions')) return route.fulfill({ json: { items: [
      sessionSummaryFixture({ sessionId, title: '监测井季节性水位研究' }),
      sessionSummaryFixture({ sessionId: 'consultation', title: '地下水术语咨询' }),
    ], nextCursor: null } });
    if (path === `/api/v12/sessions/${sessionId}/messages` && route.request().method() === 'POST') {
      const body = route.request().postDataJSON();
      expect(body.message).toBe(supplement);
      revision = 2;
      return route.fulfill({ status: 202, json: messageReceiptFixture({
        sessionId, goalRevision: 2, agentRunId: 'agent-2', request: body, requestId: body.requestId, routingStatus: 'DECIDED',
      }) });
    }
    if (path.endsWith('/messages')) {
      snapshotReads++;
      const id = path.split('/')[4];
      const supplemented = id === sessionId && revision > 1;
      return route.fulfill({ json: historyFixture({
        sessionId: id, goalRevision: supplemented ? 2 : 1, eventCursor: 10 + revision, task: id === sessionId ? task() : null,
        items: [
          { messageId: 1, goalRevision: 1, role: 'user', content: id === sessionId ? '请解释监测井的季节性水位变化。' : '什么是潜水面？' },
          ...(supplemented ? [{ messageId: 2, goalRevision: 2, role: 'user' as const, content: supplement }] : []),
        ],
        turns: [{ reply: replyFixture(id === sessionId
          ? ['比较趋势前，请补充缺失的井口高程。', ...Array.from({ length: 32 }, (_, i) =>
            `${i + 1}. 核对观测井的测量日期、井口高程和高程基准后，再比较同一时段的水位变化。`)].join('\n\n')
          : '潜水面是潜水含水层的自由水面。') },
          ...(supplemented ? [{ goalRevision: 2, agentRunId: 'agent-2', reply: null,
            status: waitingConfirmation ? 'WAITING_CONFIRMATION' as const : 'RUNNING' as const,
            confirmations: waitingConfirmation ? [{
              ...pendingConfirmation, confirmationId: 'confirmation-old', status: 'SUPERSEDED',
              revision: 1, kind: 'INITIAL' as const, replacesConfirmationId: null,
            }, pendingConfirmation] : [],
          }] : [])],
      }) });
    }
    if (path.endsWith('/results')) return route.fulfill({ json: sessionResultsFixture([], { sessionId: path.split('/')[4] }) });
    if (path.endsWith('/events') || path.endsWith('/preview')) return route.fulfill({ status: 204 });
    throw new Error(`Unexpected task overview request: ${path}`);
  });

  await page.goto(`/#/sessions/${sessionId}`);
  const header = page.getByRole('region', { name: '当前研究任务' });
  await expect(header.getByRole('heading', { name: '监测井季节性水位研究' })).toBeVisible();
  await expect(header.getByText('等待补充信息')).toBeVisible();
  await expect(header.getByText('缺少两口监测井的井口高程。')).toBeVisible();
  const transcript = page.getByTestId('message-list');
  await transcript.evaluate(node => { node.scrollTop = node.scrollHeight; });
  expect(await transcript.evaluate(node => node.scrollTop)).toBeGreaterThan(0);
  await expect(header).toBeInViewport({ ratio: 1 });
  await expect(header.getByRole('button', { name: '查看任务概览' })).toBeInViewport({ ratio: 1 });
  await page.screenshot({ path: info.outputPath('task-header-desktop.png') });
  const composer = page.getByRole('textbox', { name: '消息输入框' });
  await composer.fill('待核实的井口高程记录');
  await header.getByRole('button', { name: '补充任务信息' }).click();
  await expect(composer).toBeFocused();
  await expect(composer).toHaveValue('待核实的井口高程记录');

  await header.getByRole('button', { name: '查看任务概览' }).click();
  await expect(page).toHaveURL(/workbench\/tasks$/);
  const overview = page.getByRole('region', { name: '主任务' });
  await expect(overview.getByText('当前规格 · 修订 1')).toBeVisible();
  await expect(overview.getByText(objective, { exact: true })).toBeVisible();
  await expect(overview.getByText('依赖：核对井口高程基准')).toBeVisible();
  await expect(overview.getByText('说明变化趋势与数据限制', { exact: true })).toBeVisible();
  await page.screenshot({ path: info.outputPath('task-overview-desktop.png') });
  await page.getByRole('button', { name: '返回对话', exact: true }).click();
  await composer.fill(supplement);
  await page.getByRole('button', { name: '发送消息', exact: true }).click();
  await expect(header.getByText('正在分析')).toBeVisible();
  await expect(header.getByText('缺少两口监测井的井口高程。')).toHaveCount(0);
  await header.getByRole('button', { name: '查看任务概览' }).click();
  await expect(overview.getByText('当前规格 · 修订 2')).toBeVisible();
  const readsBeforeReload = snapshotReads;
  await page.reload();
  await expect(overview.getByText('当前规格 · 修订 2')).toBeVisible();
  await expect(overview.getByText('缺少两口监测井的井口高程。')).toHaveCount(0);
  expect(snapshotReads).toBeGreaterThan(readsBeforeReload);

  await page.setViewportSize({ width: 390, height: 844 });
  await expect(overview.getByRole('heading', { name: '原始研究目标' })).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.screenshot({ path: info.outputPath('task-overview-mobile.png') });
  await page.getByRole('button', { name: '返回对话', exact: true }).click();
  await expect(header.getByText('正在分析')).toBeVisible();
  await transcript.evaluate(node => { node.scrollTop = node.scrollHeight; });
  expect(await transcript.evaluate(node => node.scrollTop)).toBeGreaterThan(0);
  await expect(header).toBeInViewport({ ratio: 1 });
  await expect(header.getByRole('button', { name: '查看任务概览' })).toBeInViewport({ ratio: 1 });
  await page.screenshot({ path: info.outputPath('task-header-mobile.png') });
  waitingConfirmation = true;
  await page.reload();
  await expect(header.getByText('等待执行确认')).toBeVisible();
  await expect(header.getByRole('button', { name: '查看任务概览' })).toBeInViewport({ ratio: 1 });
  await expect(header.getByRole('button', { name: '查看当前执行确认' })).toBeInViewport({ ratio: 1 });
  await header.getByRole('button', { name: '查看当前执行确认' }).click();
  await expect(page.locator('#current-confirmation[data-confirmation-id="confirmation-current"]')).toBeFocused();
  await page.getByRole('button', { name: '打开会话导航' }).click();
  await page.getByRole('link', { name: '地下水术语咨询' }).click();
  await expect(page.getByText('潜水面是潜水含水层的自由水面。')).toBeVisible();
  await expect(header).toHaveCount(0);
});

test('reviews exact Task evidence and submits changes through the current conversation', async ({ page }, info) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.addInitScript(() => {
    class StableEvents extends EventTarget {
      onopen: (() => void) | null = null;
      constructor() { super(); queueMicrotask(() => this.onopen?.()); }
      close() {}
    }
    Object.assign(window, { EventSource: StableEvents });
  });

  const sessionId = 'review-study';
  const reviewText = '确认完成：趋势说明可用于研究记录';
  const changes = '请修改趋势说明，补充丰水期的缺测区间。';
  const draft = '已有研究笔记';
  const output = dataRef({ outputName: 'summary' });
  const modelId = Object.keys(profile.models)[0];
  const posts: string[] = [];
  const reviewAttempts: unknown[] = [];
  let reviewed = false;
  const task = (): TaskContext => ({
    schemaVersion: 'task-context.v1',
    spec: { sessionId, taskId: 'water-level-study', revision: reviewed ? 2 : 1, objective: '比较监测井季节性水位，并交付可复核的趋势说明',
      constraints: [{ id: 'range', text: '仅比较 2025 年采用统一高程基准的监测井。' }],
      criteria: [
        { id: 'output', text: '交付水位趋势汇总', kind: 'RESULT', modelId, outputNames: ['summary'] },
        { id: 'review', text: '趋势说明可用于研究记录', kind: 'USER_REVIEW' },
      ],
      unresolvedItems: [], suggestions: [],
      steps: [
        { id: 'compare', title: '比较统一基准后的水位', criterionIds: ['output'], dependsOn: [], gaps: [] },
        { id: 'review', title: '评审趋势说明', criterionIds: ['review'], dependsOn: ['compare'], gaps: [] },
      ],
    },
    progress: { lifecycle: reviewed ? 'COMPLETE' : 'ACTIVE', state: reviewed ? 'COMPLETE' : 'WAITING_REVIEW',
      criteria: [
        { id: 'output', satisfied: true, evidence: { criterionId: 'output', agentRunId: 'agent-1', workflowRunId: 'run-1' } },
        { id: 'review', satisfied: reviewed, evidence: reviewed ? { criterionId: 'review', agentRunId: 'agent-3', reviewQuote: reviewText, reviewGoalRevision: 3 } : null },
      ],
      steps: [{ id: 'compare', complete: true }, { id: 'review', complete: reviewed }],
      results: [output], blockingReasons: [], metrics: { totalTokens: 20, durationMs: 100 },
    },
  });

  await page.route('**/product-ui.json', route => route.fulfill({ json: profile }));
  await page.route('**/product-icon.svg', route => route.fulfill({
    contentType: 'image/svg+xml', body: readFileSync(new URL('../../products/groundwater/ui/product-icon.svg', import.meta.url), 'utf8'),
  }));
  await page.route('**/api/v12/**', async route => {
    const path = new URL(route.request().url()).pathname;
    if (path.endsWith('/capabilities')) return route.fulfill({ json: capabilitiesForProfile(profile) });
    if (path.endsWith('/sessions')) return route.fulfill({ json: { items: [sessionSummaryFixture({ sessionId, title: '监测井水位趋势评审' })], nextCursor: null } });
    if (path.endsWith('/messages') && route.request().method() === 'POST') {
      expect(path).toBe(`/api/v12/sessions/${sessionId}/messages`);
      const body = route.request().postDataJSON();
      if (body.message === reviewText) {
        reviewAttempts.push(body);
        if (reviewAttempts.length === 1) return route.fulfill({ status: 503, json: { code: 'SERVICE_UNAVAILABLE' } });
      }
      posts.push(body.message);
      reviewed = body.message === reviewText;
      const goalRevision = posts.length + 1;
      return route.fulfill({ status: 202, json: messageReceiptFixture({
        sessionId, goalRevision, agentRunId: `agent-${goalRevision}`, request: body, requestId: body.requestId, routingStatus: 'DECIDED',
      }) });
    }
    if (path.endsWith('/messages')) return route.fulfill({ json: historyFixture({
      sessionId, task: task(), goalRevision: posts.length + 1, eventCursor: 10 + posts.length,
      items: [{ messageId: 1, role: 'user', goalRevision: 1, content: '请交付监测井水位趋势说明。' },
        ...posts.map((content, i) => ({ messageId: i + 2, goalRevision: i + 2, role: 'user' as const, content }))],
      turns: [{ reply: replyFixture('水位趋势汇总已可查阅。请评审趋势说明。') },
        ...posts.map((_, i) => ({ goalRevision: i + 2, agentRunId: `agent-${i + 2}`, reply: replyFixture('已记录本轮评审意见。') }))],
    }) });
    if (path.endsWith('/results')) return route.fulfill({ json: sessionResultsFixture([{
      agentRunId: 'agent-1', goalRevision: 1, workflowRunId: 'run-1', modelId, toolCallId: 'call-1', status: 'COMPLETE', remoteOutputs: [output],
    }], { sessionId }) });
    if (path.endsWith('/data/versions/version-1')) return route.fulfill({ json: dataDetail({ title: '水位趋势汇总' }) });
    if (path.endsWith('/objects/object-1/content')) return route.fulfill({ json: { range: '2025' } });
    if (path.endsWith('/events') || path.endsWith('/preview')) return route.fulfill({ status: 204 });
    throw new Error(`Unexpected Task review request: ${path}`);
  });

  await page.goto(`/#/sessions/${sessionId}`);
  const composer = page.getByRole('textbox', { name: '消息输入框' });
  await composer.fill(draft);
  await page.getByRole('button', { name: '查看任务概览' }).click();
  const details = page.getByRole('region', { name: '主任务' });
  const resultCondition = details.getByRole('listitem').filter({ has: page.getByText('交付水位趋势汇总', { exact: true }) }).first();
  await resultCondition.getByText('查看服务端证据').click();
  await expect(resultCondition.getByText('run-1', { exact: true })).toBeVisible();
  await expect(resultCondition.getByRole('button', { name: '查看来源对话' })).toBeVisible();
  await page.screenshot({ path: info.outputPath('task-review-desktop.png') });
  await resultCondition.getByRole('button', { name: '查看来源对话' }).click();
  await expect(page.locator('#turn-1')).toBeFocused();
  await page.getByRole('button', { name: '查看任务概览' }).click();
  await resultCondition.getByRole('button', { name: /JSON/ }).click();
  await expect(page).toHaveURL(/workbench\/results$/);
  await expect(page.getByRole('link', { name: '下载' })).toBeVisible();

  await page.getByRole('tab', { name: '任务', exact: true }).click();
  await page.getByRole('button', { name: '在对话中提出修改' }).click();
  await expect(composer).toBeFocused();
  await expect(composer).toHaveValue(draft);
  expect(posts).toEqual([]);
  await composer.fill(changes);
  await page.getByRole('button', { name: '发送消息', exact: true }).click();
  await expect(page.getByText(changes, { exact: true })).toBeVisible();
  await page.getByRole('button', { name: '查看任务概览' }).click();

  await page.setViewportSize({ width: 390, height: 844 });
  await details.getByRole('heading', { name: '科研交付条件' }).scrollIntoViewIfNeeded();
  await expect(details.getByRole('button', { name: '确认此项满足' })).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.screenshot({ path: info.outputPath('task-review-mobile.png') });
  await page.getByRole('button', { name: '返回对话', exact: true }).click();
  await composer.fill(draft);
  await page.getByRole('button', { name: '查看任务概览' }).click();
  await details.getByRole('button', { name: '确认此项满足' }).click();
  const workspace = page.getByTestId('workspace-panel');
  await expect(workspace.getByRole('button', { name: '重试发送' })).toBeEnabled();
  await page.reload();
  await expect(workspace.getByText('这条消息的发送状态待确认，草稿已保留。')).toBeVisible();
  await expect(details.getByRole('button', { name: '确认此项满足' })).toBeDisabled();
  await workspace.getByRole('button', { name: '重试发送' }).click();
  await expect(page.getByTestId('workspace-panel').getByText('任务已完成', { exact: true })).toBeVisible();
  expect(posts).toEqual([changes, reviewText]);
  expect(reviewAttempts).toHaveLength(2);
  expect(reviewAttempts[1]).toEqual(reviewAttempts[0]);
  await page.reload();
  await expect(details.getByText('当前规格 · 修订 2')).toBeVisible();
  await expect(details.getByText(reviewText, { exact: true })).toBeVisible();
  await expect(details.getByRole('button', { name: '确认此项满足' })).toHaveCount(0);
  await details.getByRole('heading', { name: '科研交付条件' }).scrollIntoViewIfNeeded();
  await page.screenshot({ path: info.outputPath('task-reviewed-mobile.png') });
  await details.getByText('依赖：比较统一基准后的水位').scrollIntoViewIfNeeded();
  await expect(details.getByText('关联条件：趋势说明可用于研究记录')).toBeInViewport();
  await page.screenshot({ path: info.outputPath('task-steps-mobile.png') });
  await page.getByRole('button', { name: '返回对话', exact: true }).click();
  await expect(composer).toHaveValue(draft);
});

test('shows scoped input preparation and verified data quality at desktop and narrow widths', async ({ page }, info) => {
  const sessionId = 'preparation-study';
  const input = dataRef();
  const oldSpatialInput = dataRef({ datasetId: 'spatial-dataset', versionId: 'spatial-old' });
  let replacementReads = 0;
  let revision = 1;
  let releaseOldVersion!: () => void;
  const oldVersionReady = new Promise<void>(resolve => { releaseOldVersion = resolve; });
  let oldResponseFinished = false;
  await page.addInitScript(() => {
    class PreparationEvents extends EventTarget {
      onopen: (() => void) | null = null;
      constructor() {
        super();
        window.addEventListener('test:preparation-revised', this.revise);
        queueMicrotask(() => this.onopen?.());
      }
      revise = () => this.dispatchEvent(new MessageEvent('task.preparation_revised', { lastEventId: '11', data: '{}' }));
      close() { window.removeEventListener('test:preparation-revised', this.revise); }
    }
    Object.assign(window, { EventSource: PreparationEvents });
  });
  await page.route('**/product-ui.json', route => route.fulfill({ json: profile }));
  await page.route('**/product-icon.svg', route => route.fulfill({
    contentType: 'image/svg+xml', body: readFileSync(new URL('../../products/groundwater/ui/product-icon.svg', import.meta.url), 'utf8'),
  }));
  await page.route('**/api/v12/**', async route => {
    const path = new URL(route.request().url()).pathname;
    if (path.endsWith('/capabilities')) return route.fulfill({ json: capabilitiesForProfile(profile) });
    if (path.endsWith('/sessions')) return route.fulfill({ json: { items: [sessionSummaryFixture({ sessionId })], nextCursor: null } });
    if (path.endsWith('/messages')) return route.fulfill({ json: historyFixture({ sessionId, eventCursor: 9 + revision, preparation: {
      schemaVersion: 'task-preparation.v1', preparationId: 'prep-1', revision, sessionId, agentRunId: 'agent-1',
      goalRevision: 1, taskId: null, baseTaskRevision: null, disposition: 'ACTIVE', freshForCurrentTurn: true, status: 'BLOCKED',
      entries: [
        { targetId: 'groundwater.summary', targetVersion: '3.0.0', targetKind: 'EXECUTABLE_MODEL', status: 'READY',
          selectionPolicy: 'PINNED', inputs: { versionId: input }, knowledgeRefs: [], parameters: {}, parameterSources: {},
          checks: [{ kind: 'CRS', input: 'versionId', status: 'PASS', actualCrs: 'EPSG:4547' }], blockingReasons: [] },
        { targetId: 'groundwater.flow-field', targetVersion: '3.0.0', targetKind: 'EXECUTABLE_MODEL', status: 'BLOCKED',
          selectionPolicy: 'CURRENT_REQUIRED', inputs: { versionId: oldSpatialInput }, knowledgeRefs: [], parameters: {}, parameterSources: {},
          checks: [{ kind: 'SELECTION_POLICY', input: 'versionId', status: 'FAIL', versionId: oldSpatialInput.versionId,
            currentVersion: revision === 1 ? 'spatial-current' : 'spatial-latest' }],
          blockingReasons: ['DATA_VERSION_NOT_CURRENT'] },
      ],
    } }) });
    if (path.endsWith('/results')) return route.fulfill({ json: sessionResultsFixture([], { sessionId }) });
    if (path.endsWith('/data/versions/version-1')) return route.fulfill({ json: dataDetail({
      semantics: { crs: 'EPSG:4547', timeRange: '2025-01-01 至 2025-12-31' }, inputVersions: ['survey-2025'],
    }) });
    if (path.endsWith('/data/versions/spatial-old')) return route.fulfill({ json: dataDetail({
      datasetId: oldSpatialInput.datasetId, versionId: oldSpatialInput.versionId, currentVersion: 'spatial-current',
    }) });
    if (path.endsWith('/data/versions/spatial-current')) {
      const attempt = ++replacementReads;
      if (attempt === 2) await oldVersionReady;
      await route.fulfill({ json: dataDetail({
        datasetId: attempt === 1 ? 'unrelated-dataset' : oldSpatialInput.datasetId,
        versionId: 'spatial-current', currentVersion: 'spatial-current',
      }) });
      if (attempt === 2) oldResponseFinished = true;
      return;
    }
    if (path.endsWith('/data/versions/spatial-latest')) return route.fulfill({ json: dataDetail({
      datasetId: oldSpatialInput.datasetId, versionId: 'spatial-latest', currentVersion: 'spatial-latest',
    }) });
    if (path.endsWith('/events') || path.endsWith('/preview')) return route.fulfill({ status: 204 });
    throw new Error(`Unexpected preparation request: ${path}`);
  });
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto(`/#/sessions/${sessionId}/workbench/inputs`);
  await expect(page.getByText('可提案', { exact: true })).toBeVisible();
  await expect(page.getByText('待补充', { exact: true })).toBeVisible();
  const summary = page.locator('.workspace-data-group li').filter({ hasText: input.datasetId });
  await expect(summary.getByText('当前可读取')).toBeVisible();
  await summary.getByText('数据质量与来源').click();
  await expect(page.getByText('survey-2025', { exact: true })).toBeVisible();
  await page.screenshot({ path: info.outputPath('data-preparation-desktop.png') });
  for (const [width, height] of [[1024, 768], [768, 1024], [390, 844]]) {
    await page.setViewportSize({ width, height });
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    await expect(page.getByRole('button', { name: '返回对话' })).toBeInViewport();
    await expect(page.getByRole('heading', { name: '数据', exact: true })).toBeVisible();
    await expect(summary.getByText('当前可读取')).toBeVisible();
    await page.screenshot({ path: info.outputPath(`data-preparation-${width}x${height}.png`) });
  }
  const useCurrentVersion = page.getByRole('button', { name: '使用当前版本' });
  await useCurrentVersion.click();
  await expect(page.getByText('无法附加建议的数据版本')).toBeVisible();
  await expect(page).toHaveURL(/workbench\/inputs$/);
  await expect(page.getByText('可提案', { exact: true })).toBeVisible();
  await useCurrentVersion.click();
  await expect.poll(() => replacementReads).toBe(2);
  revision = 2;
  await page.evaluate(() => window.dispatchEvent(new Event('test:preparation-revised')));
  try {
    await expect(page.getByText(/准备修订 2/)).toBeVisible();
  } finally {
    releaseOldVersion();
  }
  await expect.poll(() => oldResponseFinished).toBe(true);
  await expect(page).toHaveURL(/workbench\/inputs$/);
  await useCurrentVersion.click();
  const pending = page.getByTestId('pending-attachments');
  await expect(pending).toContainText('spatial-latest');
  await expect(pending).not.toContainText('spatial-current');
  await pending.getByText('附件版本详情').click();
  await expect(pending.getByText(oldSpatialInput.datasetId, { exact: true })).toBeVisible();
});
