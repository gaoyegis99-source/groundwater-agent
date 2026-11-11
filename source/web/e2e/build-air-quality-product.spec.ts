import { expect, test } from '@playwright/test';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { validationOutput } from '../../packages/ui-boundary/build-path.ts';
import { dataDetail, historyFixture } from '../src/test-fixtures';
import type { Confirmation, History } from '../src/types';

const sourceRoot = fileURLToPath(new URL('../..', import.meta.url));
const fixture = validationOutput(sourceRoot, 'air-quality-fixture');
const readJson = (name: string) => JSON.parse(readFileSync(resolve(fixture, name), 'utf8'));

async function mock(page: import('@playwright/test').Page, productId = 'air-quality', history?: History) {
  const profile = readJson('product-ui.json');
  const capabilities = readJson('capabilities.json');
  capabilities.productId = productId;
  capabilities.currentUser = { subject: 'air-user', displayName: '空气质量分析用户', roles: ['researcher'] };
  await page.route('**/product-ui.json', route => route.fulfill({ json: profile }));
  await page.route('**/product-icon.svg', route => route.fulfill({ contentType: 'image/svg+xml', body: readFileSync(resolve(fixture, 'product-icon.svg')) }));
  await page.route('**/api/v12/**', route => {
    const path = new URL(route.request().url()).pathname;
    if (path.endsWith('/capabilities')) return route.fulfill({ json: capabilities });
    if (path.endsWith('/sessions')) return route.fulfill({ json: { items: [], nextCursor: null } });
    if (path.endsWith('/messages') && history) return route.fulfill({ json: history });
    if (path.endsWith('/results')) return route.fulfill({ json: { items: [], nextCursor: null } });
    if (path.endsWith('/data/versions/air-version')) return route.fulfill({ json: dataDetail({ versionId: 'air-version' }) });
    if (path.endsWith('/events') || path.endsWith('/preview')) return route.fulfill({ contentType: 'text/event-stream', body: ': connected\n\n' });
    throw new Error(`Unexpected air-quality request: ${path}`);
  });
}

test('production shell renders every product-owned air-quality presentation without leaked domain copy', async ({ page }) => {
  await mock(page);
  await page.goto('/');
  await expect(page.getByText('空气质量分析')).toBeVisible();
  await expect(page.getByRole('heading', { name: '想了解哪些空气质量数据？' })).toBeVisible();
  await page.getByRole('button', { name: '概览颗粒物' }).click();
  await expect(page.getByRole('textbox', { name: '消息输入框' })).toHaveValue('请汇总 PM2.5 观测。');
  await page.getByRole('button', { name: '能力目录' }).click();
  await page.getByText('所需资料与参数', { exact: true }).click();
  for (const text of ['颗粒物概览', '空气质量观测表', '颗粒物字段', '颗粒物统计摘要', '颗粒物统计范围与语义', '城市站点对比', '标准统计']) await expect(page.getByText(text, { exact: true })).toBeVisible();
  const forbidden = ['groundwater', '地下水', '腰站堡', 'yaozhanbao'];
  const dom = (await page.locator('body').innerText()) + await page.locator('html').evaluate(node => node.outerHTML);
  for (const value of forbidden) expect(dom.toLowerCase()).not.toContain(value.toLowerCase());
});

test('product identity mismatch fails closed in the production shell', async ({ page }) => {
  await mock(page, 'different-product');
  await page.goto('/');
  await expect(page.getByRole('heading', { name: '产品配置不可用' })).toBeVisible();
  await expect(page.getByRole('textbox', { name: '消息输入框' })).toHaveCount(0);
  await expect(page.getByRole('button', { name: '新建对话' })).toHaveCount(0);
});

test('large air-quality confirmation keeps every product-defined parameter group visible', async ({ page }) => {
  const parameters = Object.fromEntries(Array.from({ length: 25 }, (_, index) => [
    `calibrationOption${index}`, `air-value-${index}`,
  ]));
  const confirmation: Confirmation = {
    confirmationId: 'air-confirmation', goalRevision: 1, status: 'CONFIRMED',
    agentRunId: 'air-agent', reason: '', decision: 'confirm', decisionState: null,
    actions: [{
      toolCallId: 'air-summary', tool: 'run_air_quality_summary',
      modelId: 'air-quality.summary', modelVersion: '3.0.0',
      workflowId: 'air-quality.summary', workflowVersion: '1.0.0',
      arguments: { versionId: 'air-version', axis: 'columns', column: 'pm25', ...parameters },
    }],
  };
  const history = historyFixture({
    sessionId: 'air-confirm', items: [{ messageId: 1, goalRevision: 1, role: 'user', content: '运行颗粒物概览' }],
    turns: [{ agentRunId: 'air-agent', status: 'COMPLETE', confirmations: [confirmation] }],
  });
  await mock(page, 'air-quality', history);
  await page.goto('/#/sessions/air-confirm');
  const card = page.getByTestId('confirmation-card');
  await card.getByText('查看本批次 1 项分析的资料与参数').click();
  await card.locator('summary').filter({ hasText: '气象校准' }).click();
  for (let index = 0; index < 25; index += 1) {
    await expect(card.getByText(`air-value-${index}`, { exact: true })).toBeVisible();
  }
  await card.locator('summary').filter({ hasText: '基本参数' }).click();
  await expect(card.getByText('颗粒物字段', { exact: true })).toBeVisible();
});
