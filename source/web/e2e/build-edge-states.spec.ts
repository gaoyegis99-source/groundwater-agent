import { expect, test, type Page } from '@playwright/test';
import { readFileSync } from 'node:fs';
import { capabilitiesForProfile, dataDetail, historyFixture, instant, replyFixture, sessionResultsFixture, sessionSummaryFixture } from '../src/test-fixtures';
import type { WorkflowDraft, WorkflowDefinition } from '../src/types';
import { prepareClipboardReadback } from './clipboard-test-helper';

const viewports = [
  { width: 1440, height: 900 }, { width: 1280, height: 800 }, { width: 1024, height: 768 },
];
const profile = JSON.parse(readFileSync(new URL('../../products/groundwater/ui/product-ui.json', import.meta.url), 'utf8'));
const mark = readFileSync(new URL('../../products/groundwater/ui/product-icon.svg', import.meta.url), 'utf8');
const sessionId = 'edge-state-session';
const session = `/#/sessions/${sessionId}`;
const english = 'GroundwaterObservationQualityIdentifier'.repeat(12);
const chinese = '监测井长期水位数据核查记录及分析说明'.repeat(30);

async function noPageOverflow(page: Page) {
  await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth - innerWidth)).toBeLessThanOrEqual(1);
}

async function mockShell(page: Page, options: { history?: ReturnType<typeof historyFixture>; draft?: WorkflowDraft } = {}) {
  const history = options.history ?? historyFixture({ sessionId });
  await page.addInitScript(() => {
    class ConnectedEvents extends EventTarget {
      onopen: (() => void) | null = null;
      constructor() { super(); queueMicrotask(() => this.onopen?.()); }
      close() {}
    }
    Object.assign(window, { EventSource: ConnectedEvents });
  });
  await page.route('**/product-ui.json', route => route.fulfill({ json: profile }));
  await page.route('**/product-icon.svg', route => route.fulfill({ contentType: 'image/svg+xml', body: mark }));
  await page.route('**/api/v12/**', route => {
    const { pathname } = new URL(route.request().url());
    if (pathname.endsWith('/capabilities')) return route.fulfill({ json: capabilitiesForProfile(profile) });
    if (pathname.endsWith('/sessions')) return route.fulfill({ json: { items: [sessionSummaryFixture({ sessionId, title: chinese, goalRevision: history.goalRevision })], nextCursor: null } });
    if (pathname.endsWith('/messages')) return route.fulfill({ json: history });
    if (pathname.endsWith('/results')) return route.fulfill({ json: sessionResultsFixture([], { sessionId }) });
    if (options.draft && pathname.endsWith(`/workflow-drafts/${options.draft.draftId}`)) return route.fulfill({ json: options.draft });
    if (pathname.endsWith('/preview') || pathname.endsWith('/events')) return route.fulfill({ status: 204 });
    if (/^\/api\/v12\/sessions\/[^/]+\/work-item$/.test(pathname) && route.request().method() === 'GET') return route.fulfill({ json: { context: null } });
    throw new Error(`Unexpected browser fixture request: ${pathname}`);
  });
}

test('startup failure and first conversation network failure can be retried in place', async ({ page }, info) => {
  await page.setViewportSize(viewports.at(-1)!);
  await mockShell(page, { history: historyFixture({ sessionId, items: [{ messageId: 1, goalRevision: 1, role: 'user', content: '重新读取后的观测问题' }] }) });
  let profileReads = 0;
  let historyReads = 0;
  await page.route('**/product-ui.json', route => ++profileReads === 1 ? route.fulfill({ status: 503 }) : route.fallback());
  await page.route(`**/sessions/${sessionId}/messages`, route => ++historyReads === 1 ? route.abort('internetdisconnected') : route.fallback());
  await page.goto(session);
  await expect(page.getByRole('heading', { name: '产品配置不可用' })).toBeVisible();
  await expect(page.getByTestId('chat-app')).toHaveCount(0);
  await page.screenshot({ path: info.outputPath('startup-load-failure.png') });
  await page.getByRole('button', { name: '重新加载' }).press('Enter');
  const retry = page.getByRole('button', { name: '重新读取对话' });
  await expect(retry).toBeVisible();
  await expect(page.getByTestId('message-list').getByRole('alert')).toBeVisible();
  await noPageOverflow(page);
  await retry.press('Enter');
  await expect(page.getByText('重新读取后的观测问题')).toBeVisible();
  await expect(retry).toHaveCount(0);
  await expect(page.getByRole('textbox', { name: '消息输入框' })).toBeFocused();
  expect(profileReads).toBe(2);
  expect(historyReads).toBe(2);
  await expect(page).toHaveURL(new RegExp(`sessions/${sessionId}$`));
});

test('large JSON keeps a bounded preview, can search off-page data and copies original bytes as text', async ({ page, browserName }, info) => {
  await page.setViewportSize(viewports[0]);
  await prepareClipboardReadback(page, browserName);
  await mockShell(page);
  const size = 12_000;
  const records = Array.from({ length: size }, (_, i) => ({ reading: i, note: i === size - 1 ? '末条监测记录' : '正常水位观测' }));
  const json = `{"rawInteger":9223372036854775807,"fullText":${JSON.stringify(english + chinese)},"records":${JSON.stringify(records)}}`;
  await page.route(/\/data\/datasets(?:\?.*)?$/, route => route.fulfill({ json: { items: [{ datasetId: 'dataset-1', project: 'demo', title: '长 JSON 监测记录', tags: [], currentVersion: 'version-1', createdAt: instant, semantics: {}, formats: ['json'] }], nextCursor: null, matchBasis: 'catalog' } }));
  await page.route('**/data/datasets/dataset-1/versions*', route => route.fulfill({ json: { items: [{ versionId: 'version-1', semanticId: 'semantics-1', createdAt: instant }], nextCursor: null } }));
  await page.route('**/data/versions/version-1', route => route.fulfill({ json: dataDetail({ title: '长 JSON 监测记录' }) }));
  let reads = 0;
  await page.route('**/versions/version-1/objects/object-1/content', route => ++reads === 1 ? route.fulfill({ status: 502 }) : route.fulfill({ contentType: 'application/json', body: json }));
  await page.goto('/');
  const opener = page.getByRole('button', { name: '选择已有资料', exact: true });
  await opener.click();
  const drawer = page.getByRole('dialog', { name: '数据库' });
  await drawer.getByRole('button', { name: /长 JSON 监测记录/ }).click();
  await drawer.getByRole('button', { name: '重新读取 JSON' }).click();
  const tree = drawer.getByRole('region', { name: 'JSON 字段树' });
  await expect(tree.getByText('records', { exact: false }).first()).toBeVisible();
  expect(await tree.locator('summary').count()).toBeLessThan(10);
  await tree.locator('summary').filter({ hasText: 'records' }).click();
  const paging = tree.getByRole('group', { name: '$.records 分页' });
  await expect(paging).toContainText('1–40 / 12000');
  await paging.getByRole('button', { name: '下一页' }).click();
  await expect(paging).toContainText('41–80 / 12000');
  await expect(paging.getByRole('button', { name: '下一页' })).toBeFocused();
  expect(await tree.locator('summary').count()).toBeLessThan(50);
  await tree.locator('summary').filter({ hasText: 'records' }).click();
  await expect.poll(() => tree.locator('summary').count()).toBeLessThan(10);
  const search = drawer.getByRole('searchbox', { name: '搜索 JSON' });
  await search.fill('末条监测记录');
  await expect(drawer.locator('.json-workspace pre')).toContainText('末条监测记录');
  await search.fill('正常水位观测');
  await expect(drawer.getByText(/显示前 60 条匹配/)).toBeVisible();
  await search.fill('不存在的观测描述');
  await expect(drawer.getByText('没有匹配内容。请调整搜索关键词。')).toBeVisible();
  await search.clear();
  for (const viewport of viewports) {
    await page.setViewportSize(viewport);
    await expect(tree).toBeVisible();
    await noPageOverflow(page);
    await expect(drawer.getByRole('button', { name: '关闭数据库' })).toBeInViewport();
  }
  await drawer.getByRole('button', { name: '复制 JSON' }).click();
  await expect(drawer.getByText('已复制 JSON。')).toBeVisible();
  const copied = await page.evaluate(() => navigator.clipboard.readText());
  expect(copied).toBe(json);
  await page.screenshot({ path: info.outputPath('large-json-desktop-1024.png') });
  await page.evaluate(() => Object.defineProperty(navigator, 'clipboard', { configurable: true, value: undefined }));
  await drawer.getByRole('button', { name: '复制路径 $.rawInteger', exact: true }).click();
  const manualCopy = drawer.getByRole('textbox', { name: '待复制内容' });
  await expect(manualCopy).toBeFocused();
  await expect(manualCopy).toHaveValue('$.rawInteger');
  expect(await manualCopy.evaluate((input: HTMLTextAreaElement) => input.selectionEnd - input.selectionStart)).toBe('$.rawInteger'.length);
  await page.keyboard.press('Escape');
  await expect(drawer).toHaveCount(0);
});

test('long transcripts and a twenty-four-node draft retain readable and keyboard reachable content', async ({ page }, info) => {
  const nodes: WorkflowDefinition['nodes'] = Array.from({ length: 24 }, (_, index) => ({
    nodeId: `node-${index + 1}`, operatorId: 'table.summary', operatorVersion: '1.0.0', parameters: {},
    inputs: { data: index ? { nodeOutput: { nodeId: `node-${index}`, port: 'table' } } : { workflowInput: 'observations' } },
  }));
  const draft: WorkflowDraft = {
    schemaVersion: 'workflow-draft-resource.v1', draftId: 'edge-draft', sessionId, goalRevision: 28, agentRunId: 'agent-28',
    status: 'COMPLETE', diagnostics: null, errorCode: null,
    payload: { schemaVersion: 'workflow-draft.v1', title: `监测工作流 ${english}`, summary: chinese,
      workflow: { schemaVersion: 'workflow.v6', workflowId: 'edge.audit', version: '1.0.0', parameters: [],
        inputs: [{ name: 'observations', cardinality: 'one', formats: ['csv'], mediaTypes: ['text/csv'] }],
        nodes, outputs: [{ name: 'summary', nodeOutput: { nodeId: 'node-24', port: 'table' } }] },
      operators: nodes.map(({ nodeId, operatorId, operatorVersion }, index) => ({
        nodeId, operatorId, operatorVersion, implementationRevision: 'rev-1', runtime: 'native', title: `观测步骤 ${index + 1}`,
      })),
      workflowChecksum: `sha256:${'a'.repeat(64)}`, operatorCatalogDigest: `sha256:${'b'.repeat(64)}`,
      executionAllowed: false, parameterValues: {}, parameterSources: [], dataBindings: [], missingInputs: [], knowledgeRefs: [], unresolvedItems: [], limitations: [],
    },
  };
  const states = ['COMPLETE', 'FAILED', 'TIMED_OUT', 'CANCELLED'] as const;
  const turns = Array.from({ length: 28 }, (_, index) => ({ goalRevision: index + 1, agentRunId: `agent-${index + 1}`,
    status: index === 27 ? 'COMPLETE' as const : states[index % states.length], reply: replyFixture(`第 ${index + 1} 轮核查结果\n\n${english}`),
    workflowDraftId: index === 27 ? draft.draftId : null,
  }));
  const history = historyFixture({ sessionId, goalRevision: 28, turns, items: turns.map((turn, index) => ({
    messageId: index + 1, goalRevision: turn.goalRevision, role: 'user', content: `${turn.goalRevision}. ${chinese}`,
  })) });
  await mockShell(page, { history, draft });
  await page.setViewportSize(viewports[0]);
  await page.goto(session);
  await expect(page.locator('.turn')).toHaveCount(28);
  const badges = await page.locator('.turn-heading .gw-badge').allTextContents();
  expect(new Set(badges).size).toBeGreaterThanOrEqual(4);
  await page.locator('.turn-index > summary').click();
  await page.getByLabel('定位对话轮次').selectOption('1');
  await expect(page.getByRole('region', { name: '第 1 轮' })).toBeFocused();
  for (const viewport of viewports) {
    await page.setViewportSize(viewport);
    const input = page.getByRole('textbox', { name: '消息输入框' });
    await expect(input).toBeInViewport({ ratio: 1 });
    await noPageOverflow(page);
  }
  await page.getByRole('link', { name: '草稿', exact: true }).click();
  const workbench = page.getByRole('main', { name: '任务工作台' });
  await expect(workbench.getByTestId('draft-nodes').getByRole('listitem')).toHaveCount(24);
  await workbench.getByText('查看工作流依赖图').click();
  const graph = workbench.getByRole('group', { name: '工作流依赖图' });
  const last = graph.getByRole('button', { name: /查看观测步骤 24节点详情/ });
  await last.focus();
  await last.press('Enter');
  await expect(workbench.getByTestId('draft-node-detail')).toContainText('观测步骤 24');
  for (const viewport of viewports) {
    await page.setViewportSize(viewport);
    await noPageOverflow(page);
    await expect(workbench.getByRole('button', { name: '返回对话' })).toBeInViewport();
  }
  await last.scrollIntoViewIfNeeded();
  await page.screenshot({ path: info.outputPath('wide-workflow-360.png') });
});
