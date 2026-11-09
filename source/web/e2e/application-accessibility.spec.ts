import AxeBuilder from '@axe-core/playwright';
import { expect, test, type Page } from '@playwright/test';
import { readFileSync } from 'node:fs';
import { capabilitiesForProfile, dataDetail, dataRef, historyFixture, taskFixture, turnFixture } from '../src/test-fixtures';
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

async function mockApplication(page: Page, withSession = false) {
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
    turns: [turnFixture({ goalRevision: 1, status: 'WAITING_EXECUTION', confirmations: [confirmation], executionTasks: [task] })],
  });
  await page.route('**/product-ui.json', route => route.fulfill({ json: profile }));
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
  { width: 390, height: 844 },
]) {
  test(`application home reflows and has no serious accessibility findings at ${viewport.width}x${viewport.height}`, async ({ page }) => {
    await page.setViewportSize(viewport);
    await mockApplication(page);
    await page.goto('/');
    await expect(page.getByText('结果须结合输入、参数、lineage 和验收报告解释。')).toBeVisible();
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    await page.keyboard.press('Tab');
    await expect(page.locator(':focus')).toBeVisible();
    await noHighImpactViolations(page);
  });
}

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
