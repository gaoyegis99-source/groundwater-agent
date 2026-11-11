import AxeBuilder from '@axe-core/playwright';
import { expect, test, type Page } from '@playwright/test';
import { readFileSync } from 'node:fs';
import { capabilitiesForProfile, dataDetail, dataRef, historyFixture, replyFixture, taskFixture, turnFixture } from '../src/test-fixtures';
import type { Confirmation } from '../src/types';

const profile = JSON.parse(
  readFileSync(new URL('../../products/groundwater/ui/product-ui.json', import.meta.url), 'utf8'),
) as { productId: string; models: Record<string, unknown> };
const currentUser = { subject: 'researcher-1', displayName: '分析用户一', roles: ['researcher'] };

async function noHighImpactViolations(page: Page) {
  const result = await new AxeBuilder({ page }).withTags([
    'wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa',
  ]).analyze();
  const violations = result.violations.filter(item =>
    item.impact === 'critical' || item.impact === 'serious');
  expect(violations, JSON.stringify(violations, null, 2)).toEqual([]);
}

async function mockApplication(page: Page, withSession = false, analysis?: string) {
  const reference = dataRef({ outputName: 'summary', format: 'json', mediaType: 'application/json' });
  const confirmation: Confirmation = {
    confirmationId: 'confirmation-1', goalRevision: 1, status: 'PENDING', agentRunId: 'agent-1',
    reason: '', decision: null, decisionState: null, revision: 1, kind: 'INITIAL',
    actions: [{
      toolCallId: 'tool-1', tool: 'run_summary', modelId: 'groundwater.summary', modelVersion: '1.0.0',
      workflowId: 'groundwater.summary', workflowVersion: '1.0.0', arguments: { resourceId: 'version-1' },
      inputReferences: { resource: reference },
    }],
  };
  const task = taskFixture({ status: 'SUCCEEDED', stage: null, outputCount: 1, finishedAt: '2026-10-17T01:01:00Z' });
  const history = historyFixture({
    sessionId: 'application-session', goalRevision: 1,
    items: [{ messageId: 1, goalRevision: 1, role: 'user', content: '应用可访问性检查' }],
    turns: [turnFixture({ goalRevision: 1, status: analysis ? 'COMPLETE' : 'WAITING_EXECUTION', confirmations: [confirmation], executionTasks: [task], ...(analysis ? { reply: replyFixture(analysis) } : {}) })],
  });
  await page.route('**/product-ui.json', route => route.fulfill({ json: profile }));
  await page.route('**/product-icon.svg', route => route.fulfill({
    contentType: 'image/svg+xml',
    body: readFileSync(new URL('../../products/groundwater/ui/product-icon.svg', import.meta.url), 'utf8'),
  }));
  await page.route('**/api/v12/**', route => {
    const url = new URL(route.request().url());
    const reply = (value: unknown) => route.fulfill({ json: value });
    if (url.pathname.endsWith('/capabilities') && !url.pathname.includes('/data/')) return reply(
      capabilitiesForProfile(profile, { currentUser }),
    );
    if (url.pathname.endsWith('/data/capabilities')) return reply({
      version: '1', productId: profile.productId, project: 'demo', currentUser,
      permissions: {
        catalogRead: true, queryCreate: true, previewCreate: true, objectDownload: true,
        userUploadCreate: true, managedPublicationWrite: false, semanticWrite: false,
        accessWrite: true, governanceRead: false, qualityWrite: false, mlopsWrite: false,
      },
      identityMode: 'gateway', writeEnabled: true,
      formats: ['csv'], formatMediaTypes: { csv: 'text/csv' }, query: {}, preview: {},
      limits: { uploadBytes: 1073741824 }, contracts: {}, openapi: '/openapi.json',
    });
    if (url.pathname.endsWith('/sessions')) return reply({
      items: withSession ? [{
        sessionId: 'application-session', projectId: 'demo', title: '应用可访问性检查', createdAt: '2026-10-17T01:00:00Z',
        goalRevision: 1, agentRunId: 'agent-1', pendingAgentRunId: null, workflowDraftId: null, archived: false,
      }] : [], nextCursor: null,
    });
    if (url.pathname.endsWith('/messages')) return reply(history);
    if (url.pathname.endsWith('/results')) return reply({
      items: [{ goalRevision: 1, agentRunId: 'agent-1', toolCallId: 'tool-1', modelId: 'groundwater.summary', status: 'SUCCEEDED', workflowRunId: task.workflowRunId, remoteOutputs: [reference] }],
      nextCursor: null,
    });
    if (url.pathname.endsWith('/data/versions/version-1')) return reply(dataDetail());
    if (url.pathname.endsWith('/events') || url.pathname.endsWith('/preview')) {
      return route.fulfill({ contentType: 'text/event-stream', body: ': connected\n\n' });
    }
    if (url.pathname.endsWith('/data/datasets')) return reply({ items: [], nextCursor: null });
    if (url.pathname.endsWith('/trace')) return reply({});
    throw new Error(`Unexpected application accessibility request: ${url.pathname}`);
  });
}

for (const viewport of [
  { width: 1440, height: 900 },
  { width: 1366, height: 768 },
  { width: 720, height: 450 }, // 1440x900 at 200% CSS-pixel scale.
  { width: 1280, height: 800 },
]) {
  test(`application home reflows and has no serious accessibility findings at ${viewport.width}x${viewport.height}`, async ({ page }, testInfo) => {
    await page.setViewportSize(viewport);
    await mockApplication(page);
    await page.goto('/');
    await expect(page.getByText('结果须结合输入、参数、lineage 和验收报告解释。')).toBeVisible();
    await expect(page.getByTestId('workspace-panel')).toBeAttached();
    await expect(page.getByText('正在读取会话', { exact: true })).toHaveCount(0);
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    await page.keyboard.press('Tab');
    await expect(page.locator(':focus')).toBeVisible();
    await noHighImpactViolations(page);
    await page.screenshot({ path: testInfo.outputPath('home.png'), fullPage: true });
  });
}

for (const width of [1280, 1366, 1440]) {
  test(`task workbench opens and supports keyboard tabs at ${width}px`, async ({ page }, testInfo) => {
    await page.setViewportSize({ width, height: 900 });
    await mockApplication(page);
    await page.goto('/');
    await expect(page.getByTestId('workspace-panel')).toBeVisible();
    const collapse = page.getByRole('button', { name: '折叠任务工作台', exact: true });
    if (await collapse.isVisible()) await collapse.click();
    await page.getByRole('button', { name: '打开任务工作台', exact: true }).click();
    const tabs = page.getByRole('tablist', { name: '任务工作台视图' });
    await expect(tabs).toBeVisible();
    const data = tabs.getByRole('tab', { name: '数据', exact: true });
    await data.click();
    await page.keyboard.press('End');
    await expect(tabs.getByRole('tab', { name: '成果', exact: true })).toBeFocused();
    await page.keyboard.press('Home');
    await expect(data).toBeFocused();
    const bounds = await page.getByTestId('workspace-panel').boundingBox();
    expect(bounds!.x).toBeGreaterThanOrEqual(0);
    expect(bounds!.x + bounds!.width).toBeLessThanOrEqual(width);
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    await noHighImpactViolations(page);
    await page.screenshot({ path: testInfo.outputPath('workbench.png'), fullPage: true });
  });
}

test('long scientific replies keep readable hierarchy and complete tables on desktop', async ({ page }, testInfo) => {
  const limitation = '插值估计不代表实际观测，单位为米，缺测值未填零。'.repeat(5);
  await page.setViewportSize({ width: 1280, height: 900 });
  await mockApplication(page, true, `# 分析报告\n\n## 水位趋势\n\n### 适用范围\n\n正文保留真实数据和时间边界。\n\n> 观测与估计应分别解释。\n\n| 监测井 | 适用范围与限制 |\n| --- | --- |\n| W01 | ${limitation} |`);
  await page.goto('/#/sessions/application-session');
  await expect(page.getByRole('heading', { name: '分析报告', exact: true })).toBeVisible();
  const panel = page.getByRole('button', { name: '折叠任务工作台', exact: true });
  if (await panel.isVisible()) await panel.click();
  const sizes = await page.locator('.markdown-content h1, .markdown-content h2, .markdown-content h3').evaluateAll(nodes => nodes.map(node => parseFloat(getComputedStyle(node).fontSize)));
  expect(sizes[0]).toBeGreaterThan(sizes[1]);
  expect(sizes[1]).toBeGreaterThan(sizes[2]);
  const table = page.getByRole('region', { name: '分析表格，可横向滚动' });
  await table.focus();
  await expect(table).toBeFocused();
  const cell = table.getByRole('cell', { name: limitation });
  await expect(cell).toBeVisible();
  expect(await cell.evaluate(node => getComputedStyle(node).textOverflow)).toBe('clip');
  expect(await cell.evaluate(node => getComputedStyle(node).whiteSpace)).toBe('normal');
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.screenshot({ path: testInfo.outputPath('scientific-reply.png'), fullPage: true });
});

test('upload, database, confirmation and result surfaces have no serious accessibility findings', async ({ page }) => {
  await mockApplication(page, true);
  await page.goto('/#/sessions/application-session');
  await expect(page.getByTestId('confirmation-card')).toBeVisible();
  await expect(page.getByTestId('result-card')).toBeVisible();
  await noHighImpactViolations(page);
  await page.getByRole('button', { name: '数据库', exact: true }).click();
  await expect(page.getByTestId('database-workbench')).toBeVisible();
  await noHighImpactViolations(page);
  await page.keyboard.press('Escape');
  await page.locator('input[type="file"]').setInputFiles({
    name: 'sample.csv', mimeType: 'text/csv', buffer: Buffer.from('x,y\n1,2\n'),
  });
  await expect(page.getByRole('dialog', { name: '添加资料' })).toBeVisible();
  await noHighImpactViolations(page);
});
